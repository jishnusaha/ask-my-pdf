# 2026-11-05 — Voice mode B8: return `mode` in the message API (backend plan complete)

Plan: `docs/plan/voice-mood/voice-mode-v2-01-backend-plan.md`, step B8 (Stage 6
backend). Follows `47679f7` (B7). This is the last backend step of the voice-mode plan.

## What changed

- `chats/api/serializers.py` — `ChatMessageSerializer.Meta.fields` gains `"mode"`
  (after `"role"`): `["id", "role", "mode", "content", "content_html", "created_at"]`.
- `CLAUDE.md` — streaming protocol: `mode` is now returned (was "not yet returned");
  `done` event field list; B8 verification footer.
- Plan Progress: B8 ticked.

## Why

The frontend (F9) shows a mic icon on voice-mode questions, both when a chat is
reloaded (`GET /messages/`) and right after an answer is saved (SSE `done`, which
replaces the pending bubble).

## How

One serializer backs both outputs (`MessageListAPIView.serializer_class` and
`_event_stream`'s `done`), so one field addition covers both. `ModelSerializer` maps the
model's `choices` to a `ChoiceField`; output values are `"text"` / `"voice"`.

## Affected modules and behaviors

- `GET /chats/api/<pk>/messages/` and the SSE `done` event: one additional field per
  message. Additive; no field removed or renamed.
- `chats/templates/chats/chat.html` reads `done` messages by field name (`role`,
  `content_html`), so it is unaffected (templates are out of scope).
- No change to auth, migrations, async tasks, webhooks or analytics.

## Intended outcome

Every message in the API carries the turn's `mode`; signal: the frontend can show the
mic icon on reload and on `done` without extra requests.

## Verification

No paid calls.

- Real `GET /chats/api/11/messages/?limit=50` on the running server (worker reloaded
  first): message keys `id, role, mode, content, content_html, created_at`; 34
  messages — `(user, text)` 5, `(assistant, text)` 5, `(user, voice)` 12,
  `(assistant, voice)` 12.
- SSE `done` through the real `MessageStreamAPIView` (Django test client), rewrite
  chains / retrieval / rerank / generation faked, real `bulk_create` inside a
  rolled-back transaction: `mode=voice` → both messages `voice`; `mode=text` → both
  `text`; `content_html` only on the assistant row; 0 rows left on chat 12.
- `uv run ruff check .` → only the pre-existing `chats/tests.py` F401.

## Critical notes

- Backend plan B1–B8 is complete. Still unverified with real paid calls: OpenAI
  `gpt-transcribe` accepting the request and the `keywords[]` fields (B6), and the
  real LLM's filler removal (B7). See those changelogs.
- Rollback: revert the commit; no migration.
