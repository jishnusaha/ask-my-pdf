# Decision log

## 2026-11-02 — OpenAI for every model call (replaces three incorrect 2026-08-23 entries)

The code uses OpenAI throughout:
- Query rewriting and answer generation share the single `llm` in
  `chats/services/rag/config.py`: `ChatOpenAI`, `gpt-4.1-mini`, `temperature=0.2`.
- Embeddings use `text-embedding-3-small` (1536 dimensions).
- History trimming counts tokens with tiktoken's encoding for `MODEL_NAME`,
  which resolves to `o200k_base`. `cl100k_base` is only the `KeyError` fallback.
- The budget ceiling is `TOKEN_LIMIT = 128000`.

**Why this entry exists:** three entries dated 2026-08-23 described a migration
to Anthropic that the code never had:
- `claude-opus-5` for generation;
- a second `rewrite_llm` client running `claude-haiku-4-5`;
- `cl100k_base` as a stand-in for Anthropic's tokenizer, with a "1M context"
  budget.

`git log --all -G'[Aa]nthropic|rewrite_llm'` finds only the commit that
created this log (`ad83085`), and `uv.lock` has no `anthropic` package. Those entries were
deleted.

**Still true from them:** replacing `text-embedding-3-small` with a model of a
different dimension needs a migration on `DocumentChunk.embedding` (1536) and a
re-embed of every stored chunk.

**Rules out:**
- Treating the deleted entries as history.
- Changing `config.llm` without considering both callers: `query_rewriter.py`
  and `generate_response.py` use the same instance.

## 2026-09-07 — Template views are GET-only; every mutation goes through `/chats/api/`

`chats/views.py` holds exactly two view classes (`ChatListView`,
`ChatDetailView`), each with a `get` and nothing else, and it does not import
the RAG service layer. All writes — ingest, ask, re-upload — are reached only
through the DRF layer in `chats/api/`, which the templates call with `fetch()`.
**Why:** both paths existed simultaneously after the DRF migration; the
form-and-redirect handlers had no caller and were a second implementation of
ingest, ask, re-upload, and chunk listing that would drift from the API. **Rules
out:** adding a `post`/`put`/`delete` to a template view as a shortcut, and
hand-rolled `JsonResponse` endpoints alongside DRF — a new mutation or JSON
read belongs in `chats/api/` with a serializer, and DRF's global
`LimitOffsetPagination` is the pagination mechanism, not manual `limit`/`offset`
parsing.

## 2026-11-02 — Answer streaming is SSE framing over a sync POST, consumed with fetch()

`/chats/api/<pk>/messages/stream/` returns a `StreamingHttpResponse` of
`event:`/`data:` frames from a sync generator. The chat page reads it with
`fetch()` plus `body.getReader()`. Only the complete answer is saved, and the
client swaps in server-rendered `content_html` on `done`.
**Why:** this is the smallest change that streams on the existing WSGI stack,
and it adds no new dependencies.
**Rules out:**
- WebSockets/Channels, which would need ASGI, Redis and a consumer layer for a
  one-way stream.
- `EventSource`, which is GET-only, so the question would have to go in the
  query string.
- A client-side Markdown renderer, which would be a second renderer that drifts
  from `render_answer`.
- Saving partial answers on disconnect.
- Moving to ASGI without first rewriting the generator as async. Django buffers
  sync iterators under ASGI, which would silently disable streaming.

## 2026-11-02 — Streaming is the only way to ask; the blocking path is removed

The blocking path is gone:
- `POST /chats/api/<pk>/messages/`
- `pipeline.ask()`
- the `invoke`-based `generate_response()`

`ask_stream()` and `generate_response_stream()` are each one straight-line
function again. `/chats/api/<pk>/messages/` is now `MessageListAPIView`
(GET only; POST returns 405).

**Why:** nothing in the repo called the blocking endpoint after `chat.html`
moved to `/messages/stream/`. Keeping both paths required helpers
(`_retrieve_context`, `_save_exchange`, `_build_chain_input`) whose only purpose
was to let the two paths share code.

**Kept deliberately:** the three GET endpoints (`/chats/api/`, `.../messages/`,
`.../chunks/`). No page calls them, because the sidebar and history are
server-rendered. They are kept as the JSON read API.

`query_rewriter.py` keeps `.invoke`: retrieval needs the complete rewritten
query.

**Rules out:**
- Re-adding a parallel non-streaming ask endpoint. Callers that need one JSON
  result should read the `done` event, or ask via the stream and then call
  `GET /messages/`.
- Treating the unused GETs as dead code.

## 2026-11-04 — Voice mode is a `mode` flag on the existing ask path, with a second answer prompt built from shared pieces

`ChatMessage.mode` (`"text"` / `"voice"`, default `"text"`) is stored on both
rows of a turn and means "voice mode was on", not "the question was spoken"
(in voice mode the user can still type). `"voice"` selects
`voice_answer_generation_prompt`; everything else in the RAG pipeline is shared.
Plan: `docs/plan/voice-mood/`.

The text and voice answer prompts are concatenated from shared constants in
`prompts.py` (`_ANSWER_INTRO`, `_CITATION_RULES_AND_CONTEXT`); only the
formatting-rules piece differs. The text prompt renders byte-for-byte as before.

**Why:** `render_answer` and the frontend speech cleaner both depend on the
exact `[Page N]` format, so the citation rules must not drift between two copies.
Answer length is set by the prompt, not by `max_tokens`, because `config.llm`
(and `TOKEN_BUFFER` as its `max_tokens`) is shared with text mode.

**Voice prompt must name a fallback for forbidden formats.** Without the line
"If the user asks for a table, a list or another visual format, give the same
information in spoken sentences instead", a voice question ending "in a table"
answered "I couldn't find that in the document." on two of two runs, although
the text-mode answer found it.

**Rules out:**
- A separate voice pipeline or endpoint for asking.
- Naming the field `input_mode`.
- Editing the citation rules in only one of the two answer prompts.
- Shortening voice answers by lowering `TOKEN_BUFFER` / `max_tokens`.

## 2026-11-04 — Speech-to-text calls fail fast and send nothing client-written but the audio

In `chats/services/voice/transcriber.py`, the ElevenLabs call uses
`request_options={"max_retries": 0}` and `timeout=8`, and
`TranscribeAPIView` sends the provider `audio.<validated ext>`, never the
client's filename.

**Why:** the SDK retries 5xx/408/409/429 twice with backoff by default
(3 billed attempts, measured offline against fake 503s), and the user waits
through all of them. Once the OpenAI fallback exists (B6), the right reaction
to an ElevenLabs failure is to switch providers, not retry. The `timeout` is
httpx per-phase, not a whole-request cap: one real call took 16.5 s with it set.

Key-term cleaning drops `< > { } [ ] \` (the ElevenLabs SDK docstring for
`speech_to_text.convert` forbids `{ } [ ] \` as well as `< >`; the voice plan
listed only `< >`).

**Rules out:**
- Relying on SDK retries for transcription reliability.
- ElevenLabs `no_verbatim` filler removal: the plan (B7) keeps the original
  question for display and saving and cleans only the search query, and the
  OpenAI fallback has no equivalent.
