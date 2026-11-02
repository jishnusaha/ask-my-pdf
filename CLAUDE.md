# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start the database (required before running the server or any DB-touching command)
docker compose up -d

# Install dependencies
uv sync

# Run database migrations
python manage.py migrate

# Start the development server.
# `runserver` alone binds 127.0.0.1:8000, which does NOT match the default
# CSRF_TRUSTED_ORIGINS (http://127.0.0.1:9001) — pass the port explicitly.
# The VS Code "Django" launch config already passes 9001.
python manage.py runserver 9001

# Lint with ruff
uv run ruff check .
uv run ruff format .

# Run tests — see "Tests" below: there is no suite yet, this collects nothing.
python manage.py test
```

Every management command imports the RAG service layer, which loads the ~80MB
cross-encoder into memory before the command runs. Expect a few seconds of
startup on even `manage.py check`.

`uv run ruff check .` currently reports 2 pre-existing `F401` errors, both in
untouched Django scaffolding (`chats/admin.py`, `chats/tests.py`). They are not
regressions from recent work.

## Architecture

Django monolith with a RAG pipeline implemented as a service layer. Template views (GET only) render pages; all mutations are handled by a DRF API layer under `chats/api/`.

**Data flow — PDF ingestion (API):**
`ChatListCreateAPIView.create` → `serializer.save()` → **`ChatCreateSerializer.create`** does the real work: `parser.extract_text_by_page` → `embedder.chunk_pages` (also computes the SHA-256 `content_hash`) → `embedder.embed_chunks` → `ingestor.ingest_chunks` (atomic DB write). The view itself only re-serializes the result with `ChatSerializer` and returns 201.

**Data flow — question answering (API):**
`MessageListCreateAPIView.create` → `pipeline.ask()` → query rewrite → vector retrieval (top `k=5`) → cross-encoder rerank → token-aware prompt build → LLM call → `ChatMessage.bulk_create`. `ask()` returns nothing; the view captures the new rows by recording `last_id` before the call and re-querying `id__gt=last_id` afterwards.

**Data flow — streaming question answering (API):**
`MessageStreamAPIView.post` validates (404 unknown chat, 400 bad `question` — both plain JSON, before any stream starts) and returns a `StreamingHttpResponse` (`text/event-stream`) wrapping the `_event_stream` generator → `pipeline.ask_stream()` → the same rewrite/retrieve/rerank (`_retrieve_context`, shared with `ask()`) → `generate_response_stream()` (`generate_answer_chain.stream(...)`, same chain and same `_build_chain_input` as the non-stream path) → yields text fragments, then `_save_exchange` once the answer is complete. Rewrite, retrieval and rerank run *inside* the generator, i.e. after the view has already returned a 200 response object, so a failure anywhere in the pipeline surfaces as an `error` event, not a 5xx. **Headers are not sent immediately:** WSGI servers (PEP 3333; `wsgiref.handlers.BaseHandler.write` sends headers on the first non-empty write) transmit nothing until the generator yields its first value, and the first yield is the first LLM token — measured ~3.4–5 s after the request, with status line and first token arriving together. If the client disconnects mid-answer, the server's next socket write fails, it calls `close()` on the response, which closes the generator (`GeneratorExit` at the `yield`), and **nothing is saved** — neither the question nor a partial answer. A disconnect during rewrite/retrieve/rerank is not noticed until the first token write, because nothing is written before then.

**Data flow — PDF re-upload (API):**
`ChatReuploadAPIView.post` → `parser.extract_text_by_page` → `embedder.chunk_pages` → `ingestor.reingest_chunks`. Hash comparison and selective embedding happen *inside* `reingest_chunks`, not as separate steps in the view: it builds a `content_hash → embedding` map from the existing rows (deliberately outside the transaction, so no DB lock is held across the OpenAI call), reuses embeddings for unchanged text, embeds only new/changed chunks, then atomically deletes and re-creates all chunks. Chat history is untouched.

### RAG service layer (`chats/services/rag/`)

Each file has exactly one responsibility:

| File | Responsibility |
|---|---|
| `config.py` | `MODEL_NAME` (`gpt-4.1-mini`), `TOKEN_LIMIT`, `TOKEN_BUFFER`, `RERANK_THRESHOLD`, the single shared `llm` instance (`ChatOpenAI`, `temperature=0.2`) |
| `prompts.py` | The two `ChatPromptTemplate`s: `query_rewriter_prompt` and `answer_generation_prompt` (the latter carries the citation rules), plus an unused `SYSTEM_PROMPT` string |
| `types.py` | Pydantic `BaseModel`s `PageData` and `ChunkData` (strict `extra="forbid"`); `ChunkData` also carries `content_hash` and an optional `embedding` |
| `parser.py` | PDF text extraction via `pypdf`; `render_answer()` converts raw markdown + `[Page N]` markers to safe HTML |
| `embedder.py` | Chunking (`RecursiveCharacterTextSplitter`, 1600/200) with SHA-256 hashing, batch embedding (`text-embedding-3-small`, skips chunks that already have an embedding), pgvector cosine retrieval |
| `ingestor.py` | `ingest_chunks` — atomic new `Chat` + `DocumentChunk` save; `reingest_chunks` — hash-based embedding reuse then atomic chunk replacement on an existing `Chat` |
| `query_rewriter.py` | Rewrites follow-up questions into standalone retrieval queries, using the last 6 history messages; returns the original query unchanged when there is no history |
| `reranker.py` | Cross-encoder scoring (`cross-encoder/ms-marco-MiniLM-L-6-v2`), filters below `RERANK_THRESHOLD=3`, falls back to the top chunk if everything is filtered |
| `generate_response.py` | Token budget trimming (drops oldest history first), LangChain chain invocation |
| `pipeline.py` | Orchestrates the full `ask()` flow |

### Key design decisions

- The `CrossEncoder` model and `OpenAIEmbeddings` / `splitter` instances are initialized once at module import time — they are singletons per Django worker process. The reranker model (~80MB) downloads from HuggingFace on first run and caches at `~/.cache/huggingface/hub/`.
- **One LLM instance serves both calls.** `query_rewriter.py` and `generate_response.py` both import the same `llm` from `config.py`; there is no separate cheaper model for rewriting. Any change to `config.llm` affects both.
- `TOKEN_BUFFER` does double duty: it is both the reserve subtracted from the history budget *and* the `max_tokens` output cap on the `llm` instance. Changing it changes both behaviors.
- `ChatMessage.content` stores raw markdown + `[Page N]` citation markers. It is rendered to HTML in **two** places: the `render_answer` template filter (`chats/templatetags/chat_extras.py`) for server-rendered history, and `ChatMessageSerializer.content_html` for messages the JS appends live. Both call `parser.render_answer`; `content_html` is `null` for user messages.
- `DocumentChunk.embedding` is a 1536-dim `VectorField` (pgvector). Vector similarity search uses `CosineDistance` from `pgvector.django`.
- Token budget: `TOKEN_LIMIT (128000) - TOKEN_BUFFER (2000) - system_prompt_tokens - query_tokens = history budget`. If budget is exceeded, oldest messages are dropped.
- **The uploaded PDF is never persisted.** It is parsed in memory and discarded; only the extracted chunks live in the DB. `MEDIA_URL` / `MEDIA_ROOT` are configured in settings but unused. Re-upload therefore requires the user to supply the file again, and there is nothing to serve back for download.

### Database

PostgreSQL with the `pgvector` extension is required — SQLite is not supported. The Docker image `pgvector/pgvector:pg16` is used. The extension is created by the app's own migration (`0001_initial` runs `CreateExtension("vector")`), so a bare Postgres database is fine as long as the role may create extensions.

`docker-compose.yml` publishes `${POSTGRES_PORT}:5432` with no fallback — the port comes entirely from `.env`. This project uses **9002** by convention (README example and the running container).

Migrations: `0001_initial` (extension + all three models), `0002_add_content_hash_to_documentchunk` (adds `content_hash` and backfills SHA-256 hashes for existing rows).

### URL structure

There is **no route at `/`** — it returns 404. The entry point is `/chats/`. `/admin/` is mounted but no `django.contrib.admin` models are registered.

**Template views** (GET only — render pages):

| URL | Name | View | Purpose |
|---|---|---|---|
| `/chats/` | `chat_list` | `ChatListView` | List chats / upload page |
| `/chats/<pk>/` | `chat_detail` | `ChatDetailView` | Chat UI |

**DRF API** (all mutations, under `/chats/api/`):

| Method | URL | View | Purpose |
|---|---|---|---|
| `GET` | `/chats/api/` | `ChatListCreateAPIView` | List all chats |
| `POST` | `/chats/api/` | `ChatListCreateAPIView` | Upload PDF + create chat (`pdf` file, optional `title`) |
| `GET` | `/chats/api/<pk>/messages/` | `MessageListCreateAPIView` | List messages for a chat |
| `POST` | `/chats/api/<pk>/messages/` | `MessageListCreateAPIView` | Send a question (`question`); returns a bare `[user, assistant]` list |
| `POST` | `/chats/api/<pk>/messages/stream/` | `MessageStreamAPIView` | Same as above, but streams the answer as Server-Sent Events (see below). Used by the chat page. |
| `POST` | `/chats/api/<pk>/reupload/` | `ChatReuploadAPIView` | Replace PDF for an existing chat (`pdf` file) |
| `GET` | `/chats/api/<pk>/chunks/` | `ChunkListAPIView` | List stored chunks |

### API behavior worth knowing

- **All three GET list endpoints are paginated**, not just chunks. `DEFAULT_PAGINATION_CLASS = LimitOffsetPagination` with `PAGE_SIZE = 20` is global, so `/chats/api/`, `/chats/api/<pk>/messages/` and `/chats/api/<pk>/chunks/` all return `{"count", "next", "previous", "results"}` and all accept `limit`/`offset`. The `POST` to `/messages/` is the exception — it goes through `create()` and returns a plain list.
- **The API is unauthenticated and effectively CSRF-exempt.** `DEFAULT_AUTHENTICATION_CLASSES` and `DEFAULT_PERMISSION_CLASSES` are both `[]`, so DRF never runs `SessionAuthentication.enforce_csrf`. POSTs without a CSRF token reach the view and are processed. This is why the templates' `fetch()` calls send no token. Anyone who can reach the server can read every chat and spend OpenAI credit; adding auth means adding CSRF handling to the templates' `fetch()` calls at the same time.
- Chats have no owner field and no scoping — the sidebar lists every chat in the database for every visitor.
- **Streaming protocol (`/messages/stream/`).** Body `{"question": "..."}`. Response is `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`; every frame is `event: <name>\ndata: <single-line JSON>\n\n`:
  - `token` → `{"delta": "<text fragment>"}`, zero or more.
  - `done` → `[user_msg, assistant_msg]`, the same `ChatMessageSerializer` shape as the non-stream POST (assistant `content_html` rendered server-side).
  - `error` → `{"detail": "The answer could not be generated. Please try again."}` — generic on purpose; the traceback goes to the server log only. Nothing is saved.
  Exactly one of `done`/`error` ends each stream. The view overrides `perform_content_negotiation(force=True)` because `Accept: text/event-stream` matches no DRF renderer and would otherwise 406 before the view runs. `chat.html` consumes this with `fetch()` + `response.body.getReader()` (not `EventSource`, which cannot POST); it shows raw text via `textContent` while streaming and swaps in `content_html` on `done`, so `render_answer` stays the single renderer.
- **Every ask holds a server worker for the whole answer (~5–20 s), streaming or not** — the time is spent waiting on OpenAI either way. Fine under threaded `runserver`. For production (no app server is installed yet — `gunicorn` is not in `pyproject.toml`), use gunicorn's threaded worker class, e.g. `gunicorn config.wsgi --worker-class gthread --workers 2 --threads 8`, so one slow question doesn't block others. The streaming view is sync — under ASGI (uvicorn/daphne), Django consumes a sync generator fully via `sync_to_async(list)` before sending (`django/http/response.py`), which silently turns streaming back into one delayed response. Convert it to `async def` + LangChain `astream` before any move to ASGI.
- The `last_id` / `id__gt` capture is racy if two asks on the same chat overlap (both views share it). Streaming makes requests longer, so overlap is likelier.

### Tests

There is **no test suite**. `chats/tests.py` is the unmodified Django stub, so `python manage.py test` collects zero tests. When writing the first test, note that `ALLOWED_HOSTS` defaults to `["127.0.0.1"]` only: Django's test client sends `Host: testserver` and will be rejected with `DisallowedHost` unless the test overrides the host or `ALLOWED_HOSTS`.

### Environment variables

Loaded via `django-environ` from `.env` at project root.

Required (no default — startup fails if missing): `SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`. `OPENAI_API_KEY` is not read by `settings.py` — the OpenAI SDK picks it up from the environment, so it is required at request time rather than at startup.

Optional: `DEBUG` (default `False`), `ALLOWED_HOSTS` (default `127.0.0.1`), `CSRF_TRUSTED_ORIGINS` (default `http://127.0.0.1:9001`).

---

*Verified against `main` @ `ad83085`. Claims about routes, pagination shape, CSRF behavior, and the `runserver` default port were checked by executing them, not read from prior docs.*

*Streaming sections (data flow, `/messages/stream/` row, protocol, worker-holding and ASGI notes) verified against `main` @ `018a895` plus the 2026-11-02 streaming change, by live SSE requests to the endpoint; the ASGI buffering claim was read from `django/http/response.py` (`sync_to_async(list)`), not exercised. The `chat.html` consumer was not exercised in a browser.*
