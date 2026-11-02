# 2026-11-02 — Stream answers token-by-token over Server-Sent Events

## What changed

- `chats/services/rag/generate_response.py`
  - Extracted `_build_chain_input()` (context build, token budget, history trim,
    LangChain message conversion) out of `generate_response()`.
  - `generate_response()` now `invoke`s the chain on that input — behavior unchanged.
  - New `generate_response_stream()` — `yield from generate_answer_chain.stream(...)`
    on the same input and the same chain.
- `chats/services/rag/pipeline.py`
  - Extracted `_retrieve_context()` (rewrite → retrieve → rerank) and
    `_save_exchange()` (the `bulk_create` of user + assistant rows).
  - `ask()` is rebuilt from them, with the same signature and call order.
  - New `ask_stream()` generator: yields fragments, accumulates them, and saves once
    after the last fragment.
- `chats/api/views.py`
  - New `MessageStreamAPIView` (`APIView`) and module helper `_sse()`.
  - Added a module `logger`.
- `chats/api/urls.py`
  - New route `<int:pk>/messages/stream/` (`api_message_stream`).
  - `ruff format` re-wrapped the existing `/messages/` `path(...)` line (cosmetic only).
- `chats/templates/chats/chat.html`
  - `sendQuestion()` now calls the streaming endpoint.
  - New `readSSE()` async generator parses SSE frames from
    `fetch().body.getReader()`.
  - `appendMessage()` returns its wrapper, and a `formatTime()` helper was extracted.
  - New CSS rule `.streaming .bubble { white-space: pre-wrap; }`.
- `CLAUDE.md` — streaming data flow, API table row, protocol, operational notes.

## Why

Users waited for the whole answer before seeing anything. Most of that wait is the
LLM producing tokens. The non-stream endpoint had to stay for existing API callers,
so streaming lives on a separate URL.

## How

- **Transport.** SSE framing over a POST response:
  - `event: token|done|error`
  - `data: <single-line JSON>`
  - JSON-encoding keeps newlines inside tokens from breaking the framing.
- **Errors before and after the stream starts.** Validation (404/400) runs before the
  `StreamingHttpResponse` is returned, so those errors stay normal JSON. Anything inside
  the generator (rewrite, retrieval, rerank, generation) happens after the 200 status is
  fixed, so it becomes a generic `error` event. The traceback is logged server-side, and
  exception text never reaches the client.
- **When headers go out.** Not immediately. Per PEP 3333, the WSGI server sends the
  status line and headers on the first non-empty chunk, which is the first LLM token.
  Measured: request sent at 0 s, status line and first token both at 5.04 s (another
  run: 3.40 s).
- **Content negotiation.** `perform_content_negotiation(force=True)` is overridden
  because `Accept: text/event-stream` otherwise 406s in DRF before the view runs.
- **Rendering.** The client shows raw text during the stream, then swaps in the
  server-rendered `content_html` from `done`. `render_answer` stays the only Markdown
  and citation renderer.
- **Tradeoff.** No live Markdown while streaming. In exchange, two renderers can't
  drift apart.
- **Persistence.** Only the complete answer is saved. On client disconnect or LLM
  failure, nothing is saved: no question row and no partial answer. This matches the
  non-stream failure behavior.

## Affected modules and behaviors

- The chat page now uses `/messages/stream/`. `/messages/` is unchanged and still
  works (regression-checked: 201, 2-element list).
- **Workers.** Each open stream holds a WSGI worker thread for the whole answer.
  `runserver` is threaded, so this is fine in dev. Production gunicorn needs `gthread`
  or more workers.
- **ASGI.** The view is sync. Under ASGI, Django would buffer the whole generator via
  `sync_to_async(list)`, so streaming would silently stop working.
- No changes to auth, migrations, models, async tasks, or webhooks.
- No new dependencies.

## Intended outcome

The first answer text appears as soon as the LLM starts generating, not after the full
answer. Signal that it works: in the browser, the assistant bubble grows
progressively. In the SSE stream, `token` events arrive spread over time, followed by
exactly one `done`.

## Verification

- `uv run ruff check .`
  - Result: `Found 2 errors`.
  - These are only the two `F401`s that were already there (`chats/admin.py`,
    `chats/tests.py`). Nothing new.
- `uv run ruff format --check` on the 4 Python files touched: passed.
- `uv run python manage.py check`: `System check identified no issues (0 silenced).`
- **Live SSE, run by the orchestrator** against `runserver 9011`, real Postgres and
  OpenAI, chat 7, `Accept: text/event-stream`:
  - `status 200 text/event-stream`
  - `tokens 216 first 4.49s last 5.97s`
  - `terminal ['done']`
  - `ids [(91, 'user', False), (92, 'assistant', True)]` — the boolean is
    "`content_html` present"
  - `streamed == saved: True`
- **Live checks run by the backend implementer:**
  - Response headers: `Cache-Control: no-cache` and `X-Accel-Buffering: no` present.
  - Empty and whitespace-only `question` → 400 JSON.
  - Chat 999999 → 404 JSON. Both with `Accept: text/event-stream`, so no 406.
  - Non-stream `/messages/` → 201 with a 2-element list.
  - Mocked mid-stream LLM failure → `token` events, then exactly one generic `error`
    event, and 0 rows saved.
  - `gen.close()` after the first token → 0 rows saved.
- **Frontend:**
  - The template loads.
  - `node --check` on the extracted script passes.
  - `readSSE` was run in node against a mock stream cut into 7-byte chunks, splitting
    multi-byte UTF-8 characters. It parsed correctly, and a truncated stream yielded no
    terminal event.
- **Real socket disconnect, run by the orchestrator.** A raw-socket client read 5 tokens
  and then closed the connection at 3.41 s. 15 s later the row count was unchanged
  (72 → 72), so nothing was saved. Not observed directly: that the upstream OpenAI
  stream is aborted. That is inferred from the generator-close chain, not measured.
- **Cleanup.** Every `ChatMessage` row created by verification was deleted by explicit
  id: 83–90 by the implementer, 91–94 by the orchestrator. The table is back to 70 rows.
- **Unverified:**
  - The `chat.html` DOM flow in a real browser: the Chrome extension was not connected.
    The bubble growing, the swap to `content_html`, and the error bubble have not been
    observed.

## Critical notes

- **Security.** The new endpoint has the same unauthenticated, CSRF-exempt exposure as
  `/messages/` (see `CLAUDE.md`). Anyone who can reach the server can spend OpenAI
  credit. Error events deliberately carry no exception detail.
- **Race condition.** The `last_id` capture is shared with the non-stream view and is
  racy if asks on one chat overlap. Longer requests make overlap likelier. This was not
  fixed, and it is a pre-existing pattern.
- **Rollback.** Reverting the `chat.html` fetch URL plus the old response handling
  restores the previous UI. The backend additions are purely additive.
- **Divergence resolved later in this session.** See below.

## Also in this session — decision log and README aligned with the code

- **What.**
  - `docs/decisions/DECISIONS.md`: deleted three 2026-08-23 entries and replaced
    them with one accurate 2026-11-02 entry. The deleted entries described:
    - Anthropic `claude-opus-5` for generation;
    - a separate `rewrite_llm` running `claude-haiku-4-5`;
    - `cl100k_base` used because Anthropic has no tokenizer, with a "1M context".
  - `README.md`:
    - added the `/messages/stream/` endpoint row;
    - added the streaming step to the "User Question" flow;
    - ticked "Streaming responses" in the roadmap.
- **Why.** The user asked to remove the Anthropic claims and align the docs with
  the code. None of those claims were ever true in code:
  - `config.py` has one `ChatOpenAI` (`gpt-4.1-mini`) serving both rewriting and
    generation.
  - `tiktoken.encoding_for_model("gpt-4.1-mini")` returns `o200k_base`, so
    `cl100k_base` is only the fallback.
  - `TOKEN_LIMIT` is 128000.
- **Verification.**
  - `git log --all --oneline -G'[Aa]nthropic|[Cc]laude-(opus|haiku)|rewrite_llm' -- . ':!uv.lock' ':!CLAUDE.md'`
    returns only `ad83085`, the commit that created the log.
  - `grep -ci anthropic uv.lock` returns `0`.
  - The tiktoken encoding was checked by running
    `uv run python -c "import tiktoken; print(tiktoken.encoding_for_model('gpt-4.1-mini').name)"`,
    which prints `o200k_base`.
  - A repo-wide grep (excluding `.venv`, `.git`, `uv.lock`, `.env`) finds no
    remaining Anthropic, Opus, Haiku or `rewrite_llm` references outside this
    changelog and the correction entry.
- **Not checked.** `.env` was not read, since it holds secrets. If it still has an
  `ANTHROPIC_API_KEY` line, nothing reads it, and the user can delete it.

## Also in this session — `.gitignore` additions

- **What.** Added `.DS_Store`, `.claude/settings.local.json` and
  `.claude/worker-ledger.jsonl` to `.gitignore`.
- **Why.** These are machine-local files:
  - `.DS_Store` is macOS Finder view metadata.
  - `settings.local.json` holds personal Claude Code permissions.
  - The worker ledger is a per-machine log of subagent runs, read by the
    `session-brief` hook.

  The orchestration onboarding checklist lists the last two as ignored. This
  repo had never been onboarded.
- **Verification.** `git check-ignore -v` matches all three paths to the new
  rules. `git ls-files` showed none of them were tracked, so no
  `git rm --cached` was needed.
- **Known gap, not fixed.** Both ledger rows from this session have
  `"result": null`, although both worker reports began with `## Result` /
  `DONE`. The `worker-ledger.mjs` hook did not capture the outcome. The likely
  cause is that the report arrives via a hand-back tool call rather than as the
  worker's last message. This is unconfirmed; `node bootstrap.mjs doctor` is the
  suggested diagnostic.
