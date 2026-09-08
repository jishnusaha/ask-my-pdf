# Decision log

## 2026-08-23 — Anthropic for generation, OpenAI retained for embeddings

Answer generation and query rewriting moved to Anthropic (`claude-opus-5` and
`claude-haiku-4-5`). Embeddings stay on OpenAI `text-embedding-3-small`.
**Why:** Anthropic ships no embeddings API, so a full provider migration is not
available. **Rules out:** treating "remove OpenAI" as achievable without also
changing the vector dimension on `DocumentChunk.embedding` (1536), which would
require a migration and a re-embed of every stored chunk. Revisit only if
moving to Voyage AI or a local sentence-transformers model is worth that cost.

## 2026-08-23 — History trimming uses tiktoken cl100k_base, not Anthropic's tokenizer

`_count_tokens` approximates with `cl100k_base`. **Why:** Anthropic has no local
tokenizer, and the accurate alternative (`client.messages.count_tokens`) is a
network call — `_trim_history` counts once per history message, so it would add
N round-trips per question. **Rules out:** treating the budget numbers as exact;
`TOKEN_BUFFER` exists to absorb the drift. With a 1M context window the trimming
path rarely binds anyway.

## 2026-08-23 — Query rewriting routed to a separate, cheaper model

`rewrite_llm` (Haiku 4.5) is a second client rather than reusing `llm`.
**Why:** rewriting a follow-up into a standalone query is mechanical; paying
Opus 5 rates and Opus 5 thinking latency for it is waste on the critical path.
**Rules out:** a single shared `llm` singleton — any future config change must
consider both instances.

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
