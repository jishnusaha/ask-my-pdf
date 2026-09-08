# 2026-09-07 — Remove template views duplicated by the DRF API layer

## What changed

- `chats/views.py` — deleted four dead handlers left over from the pre-DRF UI:
  - `ChatListCreateView.post` (PDF upload + ingest) — duplicated by
    `ChatCreateSerializer.create`.
  - `ChatDetailView.post` (ask a question) — duplicated by
    `MessageListCreateAPIView.create`.
  - `ChatReuploadView` (whole class) — duplicated by `ChatReuploadAPIView`.
  - `ChatChunksView` (whole class, hand-rolled `JsonResponse` + manual
    limit/offset parsing) — duplicated by `ChunkListAPIView`, which gets the
    same behavior from DRF's global `LimitOffsetPagination` (`PAGE_SIZE = 20`).
  - Renamed `ChatListCreateView` → `ChatListView`; it no longer creates
    anything, and `CLAUDE.md` already documented it under that name.
  - Dropped the now-unused imports: `JsonResponse`, `redirect`,
    `extract_text_by_page`, `chunk_pages`, `embed_chunks`, `ingest_chunks`,
    `reingest_chunks`, `ask`. `chats/views.py` no longer imports the RAG
    service layer at all.
- `chats/urls.py` — removed the `chat_chunks` and `chat_reupload` routes (both
  unreferenced by any template or test); renamed `chat_list_create` →
  `chat_list`.
- `chats/templates/chats/base.html` — `{% url 'chat_list_create' %}` →
  `{% url 'chat_list' %}` (the only reference to the renamed route).
- `chats/api/views.py` — `ChatListCreateAPIView.create` now uses
  `self.get_serializer(...)` instead of re-instantiating `ChatCreateSerializer`
  directly, so the `get_serializer_class` override is actually honored (it was
  dead for POST). Wrapped three over-long lines to satisfy `ruff format`.

No changes to `chats/api/serializers.py` — see "Critical notes".

## Why

User report: the same work was implemented twice, once in `chats/views.py` and
again across `chats/api/views.py` + `chats/api/serializers.py`. Auditing the
templates confirmed it: `upload.html` and `chat.html` drive every mutation with
`fetch()` against `/chats/api/...`, and `base.html` only ever links to the two
GET pages. The `POST` handlers in `chats/views.py` had no caller — they were
leftovers from the pre-DRF, form-and-redirect UI.

## How

Consumption was traced from the templates outward before deleting anything:

- `base.html` → `{% url 'chat_list' %}`, `{% url 'chat_detail' %}` (GET links).
- `upload.html` → `fetch('/chats/api/', POST)`.
- `chat.html` → `fetch('/chats/api/<id>/messages/', POST)` and
  `fetch('/chats/api/<id>/reupload/', POST)`.

Nothing in the repo referenced `chat_chunks` or `chat_reupload`; the only other
hits were historical planning notes under `.claude/plan/`, which are not code.

Kept deliberately: `ChatMessageSerializer.get_content_html` and the
`render_answer` template filter both call `parser.render_answer`, but this is
not duplication to collapse — the filter renders history on server-side page
load, the serializer renders new answers for the `fetch()` path. Both are thin
adapters over one shared service function.

## Affected modules and behaviors

- URL namespace: `chat_list_create`, `chat_chunks`, `chat_reupload` no longer
  reverse. `chat_detail` is unchanged.
- `/chats/` and `/chats/<pk>/` now return **405** on POST instead of silently
  ingesting or answering. Both views are GET-only, which is what `CLAUDE.md`
  and `README.md` already claimed.
- No models, migrations, RAG services, auth, async tasks, or webhooks touched.

## Intended outcome

One implementation per operation, reached only through `/chats/api/`. Signal
that it worked: the UI still uploads, asks, and re-uploads (all `fetch` targets
resolve, verified below), and `chats/views.py` no longer imports the RAG
service layer.

## Verification

Commands run and their real output:

- `uv run python manage.py check` → `System check identified no issues (0 silenced).`
- `uv run ruff check chats/views.py chats/urls.py chats/api/views.py chats/api/serializers.py`
  → `All checks passed!`
- `uv run ruff format --check <same four files>` → `4 files already formatted`
- URL reverse/resolve script: `chat_list`, `chat_detail`, and all four
  `api_*` names reverse correctly; `chat_list_create`, `chat_chunks` and
  `chat_reupload` raise `NoReverseMatch` as intended; all six live URLs resolve
  to the expected view classes; `ChatListView` and `ChatDetailView` each expose
  only `['get', 'options']`.
- Template render script: `upload.html` and `chat.html` both render, contain
  `href="/chats/"`, retain their `/chats/api/...` fetch targets, and the
  `render_answer` filter still emits a `class="citation"` badge.

Not verified — no database was reachable this session: another project's
container (`langgraph-experiment-db-1`) holds port 9002, so `docker compose up`
for this project failed with `Bind for 0.0.0.0:9002 failed: port is already
allocated`. I did not stop the unrelated container. Consequently no live
request was exercised end-to-end against Postgres, and `manage.py test` was not
run (`chats/tests.py` is an empty stub, so it would assert nothing regardless).
Template rendering was verified with stub context objects instead of ORM rows.

## Critical notes

- No security, auth, migration, or data implications. Purely dead-code removal
  plus one rename.
- Breaking change for any external caller of `/chats/<pk>/chunks/` or
  `/chats/<pk>/reupload/`, and for anything POSTing to the two page URLs. No
  in-repo caller existed. The API equivalents are `/chats/api/<pk>/chunks/` and
  `/chats/api/<pk>/reupload/`. Note the chunks response *shape* differs: the
  deleted view returned `{chat_id, total, limit, offset, chunks}`, DRF returns
  `{count, next, previous, results}`.
- Rollback: `git revert` of this commit restores the handlers; nothing else
  depends on them.
- Known inconsistency, left alone as out of scope: PDF *creation* logic lives
  in `ChatCreateSerializer.create`, while *re-upload* logic sits inline in
  `ChatReuploadAPIView.post` with a hand-rolled `request.FILES.get("pdf")`
  check and a manual 400. Moving it to a `ChatReuploadSerializer` would make
  the two paths symmetric and validate through the framework, but it changes
  the error body from `{"error": ...}` to `{"pdf": [...]}` — a behavior change
  the user did not ask for.
- Follow-up worth considering: `MessageListCreateAPIView.create` recovers the
  new messages with a `last_id` watermark plus a second query, because
  `pipeline.ask()` returns `None`. Having `ask()` return the two created
  `ChatMessage` rows from its `bulk_create` would remove both extra queries.
  Not done here — it touches the service layer, outside the files in scope.
