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

`uv run ruff check .` currently reports 1 pre-existing `F401` error: the unused
`TestCase` import in the untouched Django stub `chats/tests.py`. It is not a
regression from recent work.

## Architecture

Django monolith with a RAG pipeline implemented as a service layer. Template views (GET only) render pages; all mutations are handled by a DRF API layer under `chats/api/`.

**Data flow — PDF ingestion (API):**
`ChatListCreateAPIView.create` → `serializer.save()` → **`ChatCreateSerializer.create`** does the real work: `parser.extract_text_by_page` → `embedder.chunk_pages` (also computes the SHA-256 `content_hash`) → `embedder.embed_chunks` → `key_terms.extract_key_terms(title, first 3 pages)` (one more LLM call, outside the transaction; `[]` on any failure, never fails the upload) → `ingestor.ingest_chunks(title, chunks, key_terms)` (atomic DB write). The view itself only re-serializes the result with `ChatSerializer` and returns 201.

**Data flow — question answering (API, streaming is the only path):**
`MessageStreamAPIView.post` → `StreamingHttpResponse(_event_stream(...))` → `pipeline.ask_stream()` → query rewrite (`.invoke` — must finish before retrieval) → vector retrieval (top `k=5`) → cross-encoder rerank → `generate_response.generate_response_stream()` (picks the text or voice prompt and chain from `mode`, token-aware prompt build, then `chain.stream(...)`) → each text fragment is yielded up as an SSE `token` event → once the answer is complete, `ChatMessage.bulk_create` saves the user + assistant rows, both with the request's `mode` → `_event_stream` re-queries them (`last_id` recorded before the call, then `id__gt=last_id`) and sends the `done` event. There is no non-streaming ask endpoint; `POST /chats/api/<pk>/messages/` returns 405.

- **Where errors land.** The chat lookup (404) and `MessageCreateSerializer` validation (400) run in `post()` before the response is built, so they are normal DRF JSON errors. Everything after that runs inside the generator, after the view has already returned a 200 response object, so any failure in rewrite/retrieve/rerank/generation becomes an SSE `error` event, not a 5xx.
- **Headers are not sent immediately.** WSGI servers send nothing until the generator yields its first non-empty value (PEP 3333; `wsgiref.handlers.BaseHandler.write` sends headers on the first write). The first yield is the first LLM token, so the status line, headers and first token arrive together — measured 3.4–5.0 s after the request.
- **Client disconnects.** The server only notices on its next socket write: the write fails, it calls `close()` on the response, which closes the generator (`GeneratorExit` at the `yield`), so `bulk_create` never runs and **nothing is saved** — neither the question nor a partial answer. A disconnect during rewrite/retrieve/rerank is not noticed until the first token write.

**Data flow — PDF re-upload (API):**
`ChatReuploadAPIView.post` → `parser.extract_text_by_page` → `embedder.chunk_pages` → `ingestor.reingest_chunks`. Hash comparison and selective embedding happen *inside* `reingest_chunks`, not as separate steps in the view: it builds a `content_hash → embedding` map from the existing rows (deliberately outside the transaction, so no DB lock is held across the OpenAI call), reuses embeddings for unchanged text, embeds only new/changed chunks, then atomically deletes and re-creates all chunks. After that the view re-extracts `chat.key_terms` from the new pages and saves only that field (`[]` on failure — the old terms describe the old PDF). Chat history is untouched.

### RAG service layer (`chats/services/rag/`)

Each file has exactly one responsibility:

| File | Responsibility |
|---|---|
| `config.py` | `MODEL_NAME` (`gpt-4.1-mini`), `TOKEN_LIMIT`, `TOKEN_BUFFER`, `RERANK_THRESHOLD`, the single shared `llm` instance (`ChatOpenAI`, `temperature=0.2`) |
| `prompts.py` | The five `ChatPromptTemplate`s: `query_rewriter_prompt`, `voice_query_rewriter_prompt` (also removes fillers, even from a standalone question), `answer_generation_prompt` (text mode, Markdown), `voice_answer_generation_prompt` (voice mode: short spoken sentences, no Markdown) and `key_terms_prompt`, plus an unused `SYSTEM_PROMPT` string. The two answer prompts are built from shared string pieces (`_ANSWER_INTRO`, `_CITATION_RULES_AND_CONTEXT`) and differ only in their formatting rules, so the citation rules cannot drift apart. The two rewrite prompts likewise share `_REWRITE_INTRO_AND_RULES` and `_REWRITE_HUMAN` and differ only in their last rules. Two lines of `_REWRITE_INTRO_AND_RULES` end in a trailing space kept from the original text prompt, so text mode renders byte-for-byte as before — an editor that strips trailing whitespace will change it |
| `types.py` | Pydantic `BaseModel`s `PageData`, `ChunkData` and `KeyTerms` (all strict `extra="forbid"`); `ChunkData` also carries `content_hash` and an optional `embedding`; `KeyTerms` (`terms: list[str]`) is the structured-output schema for key term extraction |
| `key_terms.py` | `extract_key_terms(title, texts)`: one `llm.with_structured_output(KeyTerms)` call on the title + first pages (cut to 8,000 chars), then cleans every term for both speech-to-text providers (whitespace collapsed, none of `< > { } [ ] \`, ≤ 5 words, ≤ 50 chars, case-insensitive dedupe, max 40). Never raises: logs and returns `[]` |
| `parser.py` | PDF text extraction via `pypdf`; `render_answer()` converts raw markdown + `[Page N]` markers to safe HTML |
| `embedder.py` | Chunking (`RecursiveCharacterTextSplitter`, 1600/200) with SHA-256 hashing, batch embedding (`text-embedding-3-small`, skips chunks that already have an embedding), pgvector cosine retrieval |
| `ingestor.py` | `ingest_chunks` — atomic new `Chat` (with `key_terms`) + `DocumentChunk` save; `reingest_chunks` — hash-based embedding reuse then atomic chunk replacement on an existing `Chat` |
| `query_rewriter.py` | `get_rewritten_query(original_query, chat_history, mode)`: rewrites follow-up questions into standalone retrieval queries, using the last 6 history messages. Text mode returns the original query unchanged when there is no history (no LLM call); voice mode always calls the LLM (`voice_query_rewrite_chain`), also for the first question, and also removes fillers |
| `reranker.py` | Cross-encoder scoring (`cross-encoder/ms-marco-MiniLM-L-6-v2`), filters below `RERANK_THRESHOLD=3`, falls back to the top chunk if everything is filtered |
| `generate_response.py` | `generate_response_stream`: picks `generate_answer_chain` or `voice_answer_chain` from `mode`, builds the context, trims history to the token budget (counting the chosen prompt's system message; drops oldest first), then streams the chain (`chain.stream`), yielding text fragments |
| `pipeline.py` | `ask_stream()` (generator): rewrite → retrieve (rewritten query) → rerank (original query in text mode, rewritten filler-free query in voice mode) → stream the answer (always the original question) → `bulk_create` of user + assistant rows once complete, both carrying `mode` |

### Voice service layer (`chats/services/voice/`)

Speech-to-text is not part of RAG, so it has its own package. `transcriber.py`: `transcribe(audio_bytes, filename, key_terms, title) -> str` is the only entry point — callers never know the provider. Order:

1. **ElevenLabs** (`elevenlabs` SDK, model `scribe_v2`) — skipped when `ELEVENLABS_API_KEY` is empty or the pause flag is set.
2. On an ElevenLabs **quota** error: set the cache key `stt:elevenlabs:paused` for 6 hours, then use OpenAI. On **any other** ElevenLabs error (wrong key, 5xx, timeout, unexpected response type): log it and use OpenAI, no pause.
3. **OpenAI** (`openai` SDK, model `gpt-transcribe`, `prompt="A question about a PDF titled <title>."`, `key_terms` as `extra_body={"keywords": [...]}` when non-empty; `timeout=15`, `max_retries=0`, client created per call so a missing `OPENAI_API_KEY` fails the request, not startup).
4. Both failed → `TranscriptionUnavailable`, which the view turns into a 503.

Each request logs `STT provider: elevenlabs` or `STT provider: openai`.

- **Quota detection reads the error body, not the status code** (a 401 can also mean a wrong key): `detail.code == "insufficient_credits"` (ElevenLabs' current error docs: type `payment_required`, HTTP 402) or the legacy `detail.status == "quota_exceeded"`. The docs call `status` a legacy field.
- **The pause flag lives in the default `LocMemCache`** (no `CACHES` setting): one per worker process, so with several gunicorn workers each one discovers empty credits separately. Restarting the server clears it.
- The `openai` 2.44.0 SDK has no `keywords` parameter; in the multipart upload it encodes the list as repeated `keywords[]` fields (observed offline).
- `key_terms` are sent as `keyterms` only when non-empty (`+20%` cost per the SDK docstring; chats created before key terms have `[]`).
- `tag_audio_events=False`, so no `(laughter)`-style tags end up in the question text.
- `timeout=8` on the client is an httpx **per-phase** timeout (connect / upload / each wait for data), not a cap on the whole request.
- `request_options={"max_retries": 0}`: the SDK otherwise retries 5xx/408/409/429 twice with backoff (3 billed attempts).
- The response type is a union; anything but `SpeechToTextChunkResponseModel` raises `TypeError`.

### Key design decisions

- The `CrossEncoder` model and `OpenAIEmbeddings` / `splitter` instances are initialized once at module import time — they are singletons per Django worker process. The reranker model (~80MB) downloads from HuggingFace on first run and caches at `~/.cache/huggingface/hub/`.
- **One LLM instance serves every call.** `query_rewriter.py`, `generate_response.py` and `key_terms.py` all import the same `llm` from `config.py`; there is no separate cheaper model for rewriting or key terms. Any change to `config.llm` affects all of them.
- `TOKEN_BUFFER` does double duty: it is both the reserve subtracted from the history budget *and* the `max_tokens` output cap on the `llm` instance. Changing it changes both behaviors.
- `ChatMessage.content` stores raw markdown + `[Page N]` citation markers. It is rendered to HTML in **two** places: the `render_answer` template filter (`chats/templatetags/chat_extras.py`) for server-rendered history, and `ChatMessageSerializer.content_html` for API responses — the SSE `done` event (which `chat.html` swaps in when an answer finishes streaming) and `GET /messages/`. Both call `parser.render_answer`; `content_html` is `null` for user messages.
- `DocumentChunk.embedding` is a 1536-dim `VectorField` (pgvector). Vector similarity search uses `CosineDistance` from `pgvector.django`.
- Token budget: `TOKEN_LIMIT (128000) - TOKEN_BUFFER (2000) - system_prompt_tokens - query_tokens = history budget`. If budget is exceeded, oldest messages are dropped.
- **The uploaded PDF is never persisted.** It is parsed in memory and discarded; only the extracted chunks live in the DB. `MEDIA_URL` / `MEDIA_ROOT` are configured in settings but unused. Re-upload therefore requires the user to supply the file again, and there is nothing to serve back for download.

### Database

PostgreSQL with the `pgvector` extension is required — SQLite is not supported. The Docker image `pgvector/pgvector:pg16` is used. The extension is created by the app's own migration (`0001_initial` runs `CreateExtension("vector")`), so a bare Postgres database is fine as long as the role may create extensions.

`docker-compose.yml` publishes `${POSTGRES_PORT}:5432` with no fallback — the port comes entirely from `.env`. This project uses **9002** by convention (README example and the running container).

Migrations: `0001_initial` (extension + all three models), `0002_add_content_hash_to_documentchunk` (adds `content_hash` and backfills SHA-256 hashes for existing rows), `0003_chatmessage_mode` (adds `ChatMessage.mode`, `"text"` or `"voice"`, default `"text"`; existing rows become `"text"`), `0004_chat_key_terms` (adds `Chat.key_terms`, `jsonb`, default `[]`; no backfill, so chats created before it keep `[]`).

### URL structure

There is **no route at `/`** — it returns 404. The entry point is `/chats/`. `/admin/` is mounted and `chats/admin.py` registers `Chat`, `ChatMessage` and `DocumentChunk` with the default `ModelAdmin` (no customization). Logging in needs a staff user (`python manage.py createsuperuser`); the admin is the only part of the app behind authentication.

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
| `GET` | `/chats/api/<pk>/messages/` | `MessageListAPIView` | List messages for a chat (`POST` here returns 405) |
| `POST` | `/chats/api/<pk>/messages/stream/` | `MessageStreamAPIView` | Send a question (`question`); streams the answer as Server-Sent Events (see below). The only way to ask; used by the chat page. |
| `POST` | `/chats/api/<pk>/reupload/` | `ChatReuploadAPIView` | Replace PDF for an existing chat (`pdf` file) |
| `GET` | `/chats/api/<pk>/chunks/` | `ChunkListAPIView` | List stored chunks |
| `POST` | `/chats/api/<pk>/transcribe/` | `TranscribeAPIView` | Speech-to-text for one recorded question (multipart `audio`); returns `{"text": "..."}` (see below) |

### API behavior worth knowing

- **All three GET list endpoints are paginated**, not just chunks. `DEFAULT_PAGINATION_CLASS = LimitOffsetPagination` with `PAGE_SIZE = 20` is global, so `/chats/api/`, `/chats/api/<pk>/messages/` and `/chats/api/<pk>/chunks/` all return `{"count", "next", "previous", "results"}` and all accept `limit`/`offset`. No client in the repo calls these GETs — the sidebar and chat history are server-rendered by the template views — but they are kept as the JSON read API.
- **The API is unauthenticated and effectively CSRF-exempt.** `DEFAULT_AUTHENTICATION_CLASSES` and `DEFAULT_PERMISSION_CLASSES` are both `[]`, so DRF never runs `SessionAuthentication.enforce_csrf`. POSTs without a CSRF token reach the view and are processed. This is why the templates' `fetch()` calls send no token. Anyone who can reach the server can read every chat and spend OpenAI credit; adding auth means adding CSRF handling to the templates' `fetch()` calls at the same time.
- Chats have no owner field and no scoping — the sidebar lists every chat in the database for every visitor.
- **Transcribe (`/transcribe/`).** Multipart, one file field `audio`. Order: unknown chat → 404; then `TranscribeSerializer` → 400 for a missing/empty file, over 5 MB, or an extension other than `webm`/`mp4`/`m4a`/`wav` (checked by extension, not `content_type`; no `ogg`). Then `transcribe(...)` with the chat's `key_terms` and `title`, and `200 {"text": "..."}` (stripped; `""` when nothing was said). The client's filename is never forwarded: the provider gets `audio.<validated ext>`. The audio is never stored. If both providers fail: `503 {"detail": "Voice input is unavailable right now, please type your question."}`. Worst-case wait is both providers' per-phase timeouts added together. Like the ask endpoint it is unauthenticated, so anyone who can reach the server can spend ElevenLabs credits.
- **Streaming protocol (`/messages/stream/`).** Body `{"question": "...", "mode": "text" | "voice"}`. `mode` is optional (default `"text"`); any other value is a 400 (`{"mode": ["\"loud\" is not a valid choice."]}`) before the stream starts. `mode` means voice mode was on for the turn, not how the question was entered; `"voice"` selects the voice answer prompt and the filler-cleaning rewrite, and is saved on both rows. `ChatMessageSerializer` returns it (`"mode": "text" | "voice"`), so both `GET /messages/` and the `done` event carry it. Response is `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`; every frame is `event: <name>\ndata: <single-line JSON>\n\n`:
  - `token` → `{"delta": "<text fragment>"}`, zero or more.
  - `done` → `[user_msg, assistant_msg]`, serialized with `ChatMessageSerializer` (fields `id`, `role`, `mode`, `content`, `content_html`, `created_at`; assistant `content_html` rendered server-side).
  - `error` → `{"detail": "The answer could not be generated. Please try again."}` — generic on purpose; the traceback goes to the server log only. Nothing is saved.
  Exactly one of `done`/`error` ends each stream. The view overrides `perform_content_negotiation(force=True)` because `Accept: text/event-stream` matches no DRF renderer and would otherwise 406 before the view runs. `chat.html` consumes this with `fetch()` + `response.body.getReader()` (not `EventSource`, which cannot POST); it shows raw text via `textContent` while streaming and swaps in `content_html` on `done`, so `render_answer` stays the single renderer.
- **Every ask holds a server worker for the whole answer** (about 6–7 s end to end in measured runs, longer for long answers) — the time is spent waiting on OpenAI. Fine under threaded `runserver`. For production (no app server is installed yet — `gunicorn` is not in `pyproject.toml`), use gunicorn's threaded worker class, e.g. `gunicorn config.wsgi --worker-class gthread --workers 2 --threads 8`, so one slow question doesn't block others. The streaming view is sync — under ASGI (uvicorn/daphne), Django consumes a sync generator fully via `sync_to_async(list)` before sending (`django/http/response.py`), which silently turns streaming back into one delayed response. Convert it to `async def` + LangChain `astream` before any move to ASGI.
- The `last_id` / `id__gt` capture in `_event_stream` is racy: if two asks on the same chat overlap, one `done` event can include the other request's rows. Not fixed.

### Tests

There is **no test suite**. `chats/tests.py` is the unmodified Django stub, so `python manage.py test` collects zero tests. When writing the first test, note that `ALLOWED_HOSTS` defaults to `["127.0.0.1"]` only: Django's test client sends `Host: testserver` and will be rejected with `DisallowedHost` unless the test overrides the host or `ALLOWED_HOSTS`.

### Environment variables

Loaded via `django-environ` from `.env` at project root.

Required (no default — startup fails if missing): `SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`. `OPENAI_API_KEY` is not read by `settings.py` — the OpenAI SDK picks it up from the environment, so it is required at request time rather than at startup.

Optional: `DEBUG` (default `False`), `ALLOWED_HOSTS` (default `127.0.0.1`), `CSRF_TRUSTED_ORIGINS` (default `http://127.0.0.1:9001`), `CORS_ALLOWED_ORIGINS` (default `[]`, comma-separated), `ELEVENLABS_API_KEY` (default `""`; read as `settings.ELEVENLABS_API_KEY` for voice input — empty means ElevenLabs is skipped, so the server starts without it).

---

*Verified 2026-11-02 against `main` @ `af78051` plus the streaming-only change in the working tree. Routes, pagination shape, CSRF behavior, the `runserver` default port, the 405 on `POST /messages/`, and the SSE protocol (events, headers, 400/404 before the stream, no save on disconnect, header timing) were checked by executing them, not read from prior docs. Not exercised: the ASGI buffering claim (read from `django/http/response.py`, `sync_to_async(list)`) and the `chat.html` consumer in a real browser.*

*Voice-mode Stage 1 (`ChatMessage.mode`, `mode` in the ask body, the voice answer prompt) verified 2026-11-04 against `main` @ `aeb0e86` plus the B3 working-tree change, by real requests to `/messages/stream/`: no `mode` / `"voice"` / `"loud"` (400), and text vs voice answers on the same questions.*

*Voice-mode B4 (`Chat.key_terms`) verified 2026-11-04 against `main` @ `7615cb5` plus the B4 working-tree change: real `POST /chats/api/` and `/reupload/` with generated PDFs (terms extracted, then replaced on re-upload), and upload/re-upload through the real view with the key-terms chain forced to raise (201/200, `key_terms == []`). `key_terms` is not exposed by any serializer.*

*Voice-mode B5 (`/transcribe/`) verified 2026-11-04 against `main` @ `839509a` plus the B5 working-tree change: the seven 400/404/405 cases by real requests to the running server; two real ElevenLabs transcriptions (3.2 s wav with 16 keyterms, 1.8 s m4a without) through the real route and view, with the outgoing request observed (keyterms count, fixed filename); the retry setting checked offline against fake 503 responses (1 attempt vs the SDK default's 3). Not exercised: a real `webm`/`mp4` browser recording, nginx's upload limit, silence → `""`.*

*Voice-mode B6 (OpenAI fallback) verified 2026-11-04 against `main` @ `3196437` plus the B6 working-tree change, offline only: eight scenarios through the real route, view, serializer and both SDKs, with httpx answering `api.elevenlabs.io` / `api.openai.com` locally (works; 401 wrong key → fallback, no pause; 402 `insufficient_credits` → pause; next request skips ElevenLabs; legacy `quota_exceeded` → pause; non-JSON 500 → fallback; no key → straight to OpenAI; both fail → 503), plus the OpenAI upload fields. **Not verified against the real OpenAI API:** that `gpt-transcribe` accepts the request and reads the `keywords[]` fields. The real ElevenLabs quota body has not been seen.*

*Voice-mode B7 (filler cleaning) verified 2026-11-04 against `main` @ `6445853` plus the B7 working-tree change, offline only: text rewrite prompt byte-identical to before; `get_rewritten_query` for text/voice × with/without history with both chains replaced by recorders; `ask_stream` with rewrite, retrieval, rerank and generation replaced by recorders and the real `bulk_create` rolled back. **Not verified:** how well the real LLM removes fillers with `voice_query_rewriter_prompt` (no LLM call made).*

*Voice-mode B8 (`mode` in message responses) verified 2026-11-05 against `main` @ `47679f7` plus the B8 working-tree change: real `GET /chats/api/11/messages/?limit=50` on the running server (every message has `mode`; 10 text / 24 voice, paired per turn), and the SSE `done` event through the real streaming view for `voice` and `text` with the rewrite/retrieve/rerank/generate steps faked and the save rolled back (both rows carry the request's `mode`).*
