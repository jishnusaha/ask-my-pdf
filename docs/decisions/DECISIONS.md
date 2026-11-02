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
