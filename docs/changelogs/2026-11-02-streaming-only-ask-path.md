# 2026-11-02 — Streaming becomes the only ask path; docs aligned with the code

Follows `41c7ac8` ("Add SSE streaming endpoint for chat answers"). That commit
added `/messages/stream/` next to the blocking `POST /messages/`. This change
removes the blocking path and brings the docs up to date with the code at
`af78051` plus this change.

## What changed

**Code: blocking ask path removed**
- `chats/api/views.py`
  - `MessageListCreateAPIView` → `MessageListAPIView` (`ListAPIView`).
    `create()` and `get_chat()` were removed, and the `ask` import dropped.
  - `MessageStreamAPIView` and `_sse` are unchanged.
- `chats/api/urls.py`
  - `<int:pk>/messages/` now routes to `MessageListAPIView`.
  - Its route name changed from `api_message_list_create` to `api_message_list`.
    No code reverses either name.
- `chats/services/rag/pipeline.py`
  - `ask()` deleted.
  - `ask_stream()` is one straight-line function: rewrite → retrieve → rerank →
    stream → `bulk_create`. The helpers `_retrieve_context` and `_save_exchange`
    were inlined and removed.
- `chats/services/rag/generate_response.py`
  - The blocking `generate_response()` (`chain.invoke`) was deleted.
  - `generate_response_stream()` builds its own input and streams the chain. The
    helper `_build_chain_input` was inlined and removed.

**Docs**
- `CLAUDE.md`
  - Question-answering data flow rewritten for the single streaming path, as one
    flow line plus three notes:
    - where errors land;
    - when headers are sent;
    - what a disconnect does.
  - The RAG table, API table and `content_html` note now match the code.
  - Removed the false claim that streaming makes requests longer.
  - Ruff note: 1 pre-existing `F401` (`chats/tests.py`). `af78051` made
    `chats/admin.py` use its import.
  - Admin note: `Chat`, `ChatMessage` and `DocumentChunk` are registered, and
    logging in needs a staff user.
  - Footer verification note rewritten.
- `README.md`
  - Features, "How it works", the project tree, the RAG table, the mermaid flow
    and the API table now describe streaming as the only ask path.
  - New "Streaming Answers" section covering:
    - the event protocol;
    - save-on-complete;
    - `fetch()` vs `EventSource`;
    - the deployment note on WSGI/gthread and ASGI buffering.
  - Fixed inaccuracies that predate today:
    - `runserver` → `runserver 9001`, and visit `127.0.0.1:9001`, not `localhost`;
    - the `POSTGRES_PORT` example `5432` → `9002`;
    - "Token Budget Guard" described the "model's context window"; it now
      describes the fixed `TOKEN_LIMIT` − `TOKEN_BUFFER` budget;
    - the tree was missing `config/asgi.py` and `docs/`.
- `docs/decisions/DECISIONS.md`: new entry, "Streaming is the only way to ask; the
  blocking path is removed".
- `.claude/agent-memory/implementer/`: the implementer's notes on live-testing
  traps (check chat ids first; `.env` reads are denied; ruff exits 1; server
  start/stop recipe). The global policy says to commit this directory.

## Why

- Once `chat.html` moved to `/messages/stream/`, a repo-wide search found no
  caller of `POST /messages/`: no template, JS, Python or test.
- Keeping both paths needed helpers whose only job was letting the two share code.
  The user asked to keep only streaming and simplify.
- The user chose to keep all three GET endpoints (`/chats/api/`, `.../messages/`,
  `.../chunks/`). No page calls them, because the sidebar and history are
  server-rendered in `chats/views.py`, but they are the JSON read API.
- `query_rewriter.py` keeps `.invoke`, because retrieval needs the complete
  rewritten query.

## Affected modules and behaviors

- `POST /chats/api/<pk>/messages/` now returns **405** (`Allow: GET, HEAD, OPTIONS`).
  Any outside client still posting there breaks. None exists in the repo.
- These are unchanged:
  - `/messages/stream/`, upload and re-upload;
  - all GET endpoints;
  - `chat.html`.
- No migrations, models, settings or dependencies changed.

## Intended outcome

One ask path, so a change to retrieval or generation is made in one function.
Signal: no references to the removed symbols remain, and the chat page still
streams answers.

## Verification

- `uv run ruff check .` → only the pre-existing
  `F401 django.test.TestCase imported but unused` (`Found 1 error.`).
- `ruff format --check` on the 4 Python files → passed.
- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- A grep for `ask(`, `generate_response(`, `_build_chain_input`,
  `_retrieve_context`, `_save_exchange`, `MessageListCreateAPIView` and
  `api_message_list_create` in `chats/` and `config/` → no matches.
- Django test client, chat 11 (orchestrator):
  - `POST /chats/api/11/messages/` → `405`, `Allow: GET, HEAD, OPTIONS`.
  - `GET /chats/api/11/messages/` → `200` with `count/next/previous/results`.
  - `GET /chats/api/` → `200`; `GET /chats/api/11/chunks/?limit=1` → `200`.
  - Stream with `{"question": ""}` → `400`; stream for chat `999999` → `404`.
- Live run on `runserver 9011` with real OpenAI (implementer):
  - The stream returned `200 text/event-stream` with `Cache-Control: no-cache` and
    `X-Accel-Buffering: no`.
  - It sent 180 `token` events, exactly one `done` (2 messages, assistant
    `content_html` rendered) and 0 `error`.
  - The two test rows (ids 109–110) were then deleted by id. The table is back to
    0 messages.
- Host check (`RequestFactory.get_host()` with project settings):
  `127.0.0.1:9001` accepted, `localhost:9001` rejected. This backs the README's
  Getting Started fix.
- **Not verified:**
  - the `chat.html` flow in a real browser (its code did not change);
  - that the README mermaid diagram renders on GitHub.

## Critical notes

- The `last_id` race in `_event_stream` remains. Overlapping asks on one chat can
  put the other request's rows in a `done` event.
- The API is still unauthenticated; anyone who can reach the server can spend
  OpenAI credit.
- The dev DB held only chat 11, with 0 messages, when this work started. The
  earlier chats were removed outside this session.
- Rollback: `git revert` this commit. No migrations are involved.
