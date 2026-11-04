# 2026-11-04 — Voice mode B4: extract key terms at upload and re-upload

Plan: `docs/plan/voice-mood/voice-mode-v2-01-backend-plan.md`, step B4 (first step of
Stage 2). Follows `7615cb5` (Stage 1 backend).

## What changed

- `chats/models.py` — `Chat.key_terms = JSONField(default=list, blank=True)`.
- `chats/migrations/0004_chat_key_terms.py` — generated `AddField`; SQL adds `jsonb
  DEFAULT '[]' NOT NULL` then drops the DB default. No backfill.
- `chats/services/rag/types.py` — new `KeyTerms(BaseModel)` (`terms: List[str]`,
  `extra="forbid"`), the structured-output schema.
- `chats/services/rag/prompts.py` — new `key_terms_prompt` (system rules + human
  message with `{title}` and `{text}`).
- `chats/services/rag/key_terms.py` (new)
  - `key_terms_chain = key_terms_prompt | llm.with_structured_output(KeyTerms)`
    (default `method="json_schema"` in `langchain-openai` 1.3.3).
  - `extract_key_terms(title, texts)` — joins the texts, cuts to `MAX_INPUT_CHARS =
    8000`, invokes the chain, returns `_clean_terms(...)`. Wrapped in `try/except
    Exception`: logs with `logger.exception` and returns `[]`.
  - `_clean_terms` — collapses whitespace (removes `\r`/`\n`), drops terms that are
    empty, contain `<` or `>`, have more than `MAX_TERM_WORDS = 5` words or more than
    `MAX_TERM_CHARS = 50` characters, or repeat an earlier term case-insensitively;
    keeps the first `MAX_TERMS = 40`.
- `chats/services/rag/ingestor.py` — `ingest_chunks(title, chunks, key_terms)`;
  `Chat.objects.create(title=title, key_terms=key_terms)`.
- `chats/api/serializers.py` — `ChatCreateSerializer.create` calls
  `extract_key_terms(title, [p.text for p in pages[:3]])` after embedding and before
  `ingest_chunks`.
- `chats/api/views.py` — `ChatReuploadAPIView.post`, after `reingest_chunks`: sets
  `chat.key_terms` from the new pages and `chat.save(update_fields=["key_terms"])`.
- `CLAUDE.md` — upload and re-upload data flows, `rag/` table (`prompts.py`,
  `types.py`, new `key_terms.py`, `ingestor.py`), "one LLM instance" note, migrations
  list, B4 verification footer.
- `docs/plan/voice-mood/voice-mode-v2-01-backend-plan.md` — Progress: B4 ticked.

## Why

B5/B6 pass these terms to the speech-to-text providers as hints (`keyterms` for
ElevenLabs, `keywords` for OpenAI), so document-specific words are spelled correctly.

## How / decisions

- Uses the shared `config.llm` (`gpt-4.1-mini`), per the 2026-11-02 decision (one
  OpenAI model for every call); no separate client.
- Cleaning is enforced in code after the LLM, not trusted to the prompt: OpenAI
  rejects the whole transcription request if one keyword contains `<`, `>` or a line
  break. Cleaning once at save time means a bad term can never break transcription.
- Length limit is `<= 50` characters (ElevenLabs: "at most 50"); the plan's wording
  "under 50" was read as the provider rule.
- Whitespace inside a term is collapsed rather than the term dropped, so a term the
  PDF wraps across lines survives as one line.
- Upload: extraction runs outside `ingest_chunks`' `transaction.atomic()`, so no
  transaction is held open across the LLM call (same reason `reingest_chunks` embeds
  before its transaction).
- Re-upload: a failed extraction writes `[]`, not the old terms — they describe the
  previous PDF.

## Affected modules and behaviors

- `POST /chats/api/` and `POST /chats/api/<pk>/reupload/` each make one more LLM call
  (about 1–2 s); response shapes unchanged. `key_terms` is not exposed by any
  serializer.
- No change to the ask path, auth, async tasks, webhooks or analytics.

## Intended outcome

Every new or re-uploaded chat stores up to 40 provider-safe terms from its PDF;
signal: `Chat.key_terms` lists the PDF's names/jargon, and no upload fails because of
extraction.

## Verification

- `manage.py migrate` → `Applying chats.0004_chat_key_terms... OK`; chat 11 (pre-B4)
  → `key_terms == []`.
- `_clean_terms` on hand-made inputs: `'line\nbreak\r\nterm'` → `'line break term'`;
  `<script>`, `a > b`, empty, 6-word and 51-char terms dropped; 5-word and 50-char
  kept; `'rag'` dropped after `'RAG'`; 60 terms → 40.
- `extract_key_terms` on chat 11's stored chunks → 36 terms (e.g. `LoRA`, `PEFT`,
  `RLHF`, `CI/CD pipelines`), zero rule violations.
- Failure path (chain replaced with one that raises) → `[]`, traceback logged.
- Real API with generated PDFs (`quantum.pdf`, `k8s.pdf`, synthetic text):
  - `POST /chats/api/` → 201, chat 12, 21 quantum terms.
  - `POST /chats/api/12/reupload/` with `k8s.pdf` → 200, 22 Kubernetes terms
    (`kubelet`, `etcd`, `Helm charts`, `CRDs`, …), title unchanged.
  - The first re-upload attempt returned the old terms: the dev server's autoreloader
    had missed the last `views.py` edit (worker started mid-edit). After `touch
    chats/api/views.py` forced a reload, the same request gave the new terms.
- Real view + serializer + embedding + DB via Django test client, key-terms chain
  forced to raise: upload → 201, chat 13, 2 chunks, `key_terms == []`; re-upload chat
  12 → 200, `key_terms == []`.
- `uv run ruff check .` → only the pre-existing `chats/tests.py` F401.

Not verified: a real PDF with dense layout or many pages (only synthetic 2-page PDFs
and chat 11's text); a real OpenAI-side failure (simulated only).

## Critical notes

- Upload and re-upload latency grows by one LLM call; cost is one small
  `gpt-4.1-mini` call per upload (input capped at 8,000 characters).
- Test data created: chat 12 ("Quantum test (voice B4)", now quantum chunks with
  `key_terms == []` after the failure test) and chat 13 ("B4 failure test").
- Rollback: revert the commit; `migrate chats 0003` drops the column.
