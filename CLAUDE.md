# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Start the database (required before running the server)
docker compose up -d

# Install dependencies
uv sync

# Run database migrations
python manage.py migrate

# Start the development server (runs on port 9001 by default per CSRF config)
python manage.py runserver

# Lint with ruff
uv run ruff check .
uv run ruff format .

# Run tests
python manage.py test
```

## Architecture

Django monolith with a RAG pipeline implemented as a service layer. Template views (GET only) render pages; all mutations are handled by a DRF API layer under `chats/api/`.

**Data flow — PDF ingestion (API):**
`ChatListCreateAPIView.post` → `parser.extract_text_by_page` → `embedder.chunk_pages` → `embedder.embed_chunks` → `ingestor.ingest_chunks` (atomic DB write)

**Data flow — question answering (API):**
`MessageListCreateAPIView.post` → `pipeline.ask()` → query rewrite → vector retrieval → cross-encoder rerank → token-aware prompt build → LLM call → `ChatMessage.bulk_create`

**Data flow — PDF re-upload (API):**
`ChatReuploadAPIView.post` → `parser.extract_text_by_page` → `embedder.chunk_pages` → hash comparison → embed only new/changed chunks → `ingestor.reingest_chunks` (atomic replace, chat history preserved)

### RAG service layer (`chats/services/rag/`)

Each file has exactly one responsibility:

| File | Responsibility |
|---|---|
| `config.py` | Model name (`gpt-4.1-mini`), `TOKEN_LIMIT`, `TOKEN_BUFFER`, `RERANK_THRESHOLD`, shared `llm` instance |
| `types.py` | Pydantic `PageData` and `ChunkData` dataclasses (strict `extra="forbid"`) |
| `parser.py` | PDF text extraction via `pypdf`; `render_answer()` converts raw markdown + `[Page N]` markers to safe HTML |
| `embedder.py` | Chunking (`RecursiveCharacterTextSplitter`, 1600/200), batch embedding (`text-embedding-3-small`), pgvector cosine retrieval |
| `ingestor.py` | Atomic `Chat` + `DocumentChunk` DB save |
| `query_rewriter.py` | Rewrites follow-up questions into standalone retrieval queries using LLM |
| `reranker.py` | Cross-encoder scoring (`cross-encoder/ms-marco-MiniLM-L-6-v2`), filters below `RERANK_THRESHOLD=3`, fallback to top chunk |
| `generate_response.py` | Token budget trimming (drops oldest history first), LangChain chain invocation |
| `pipeline.py` | Orchestrates the full `ask()` flow |

### Key design decisions

- The `CrossEncoder` model and `OpenAIEmbeddings` / `splitter` instances are initialized once at module import time — they are singletons per Django worker process. The reranker model (~80MB) downloads from HuggingFace on first run and caches at `~/.cache/huggingface/hub/`.
- `ChatMessage.content` stores raw markdown + `[Page N]` citation markers. Rendering to HTML happens at display time via the `render_answer` template filter (`chats/templatetags/chat_extras.py`).
- `DocumentChunk.embedding` is a 1536-dim `VectorField` (pgvector). Vector similarity search uses `CosineDistance` from `pgvector.django`.
- Token budget: `TOKEN_LIMIT (128000) - TOKEN_BUFFER (2000) - system_prompt_tokens - query_tokens = history budget`. If budget is exceeded, oldest messages are dropped.

### Database

PostgreSQL with the `pgvector` extension is required — SQLite is not supported. The Docker image `pgvector/pgvector:pg16` is used. Default port is `9002` (mapped to Postgres internal 5432).

### URL structure

**Template views** (GET only — render pages):

| URL | View | Purpose |
|---|---|---|
| `/chats/` | `ChatListView` | List chats / upload page |
| `/chats/<pk>/` | `ChatDetailView` | Chat UI |

**DRF API** (all mutations, under `/chats/api/`):

| Method | URL | View | Purpose |
|---|---|---|---|
| `GET` | `/chats/api/` | `ChatListCreateAPIView` | List all chats |
| `POST` | `/chats/api/` | `ChatListCreateAPIView` | Upload PDF + create chat |
| `GET` | `/chats/api/<pk>/messages/` | `MessageListCreateAPIView` | List messages for a chat |
| `POST` | `/chats/api/<pk>/messages/` | `MessageListCreateAPIView` | Send a question (returns `[user, assistant]`) |
| `POST` | `/chats/api/<pk>/reupload/` | `ChatReuploadAPIView` | Replace PDF for an existing chat |
| `GET` | `/chats/api/<pk>/chunks/` | `ChunkListAPIView` | List stored chunks (paginated, `limit`/`offset`) |

### Environment variables

Loaded via `django-environ` from `.env` at project root. Required: `OPENAI_API_KEY`, `SECRET_KEY`, `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_HOST`, `POSTGRES_PORT`. `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS` default to `127.0.0.1` / `http://127.0.0.1:9001`.
