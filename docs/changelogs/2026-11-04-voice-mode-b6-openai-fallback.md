# 2026-11-04 — Voice mode B6: OpenAI fallback for transcription

Plan: `docs/plan/voice-mood/voice-mode-v2-01-backend-plan.md`, step B6 (ends Stage 2
on the backend). Follows `3196437` (B5).

## What changed

- `chats/services/voice/transcriber.py`
  - New exceptions `TranscriptionUnavailable` (every provider failed) and
    `ElevenLabsQuotaError` (no credits left). Both are raised with a message and
    chained to the provider error (`from e`): `"ElevenLabs has no credits left (HTTP
    402)"`, `"Speech-to-text failed on every provider (last: OpenAI
    InternalServerError)"`. The quota warning log line includes that message. The
    user-facing 503 text stays in the view.
  - `transcribe()`: ElevenLabs first unless `settings.ELEVENLABS_API_KEY` is empty or
    `cache.get("stt:elevenlabs:paused")`; on `ElevenLabsQuotaError` sets that key for
    `ELEVENLABS_PAUSE_SECONDS = 6 * 3600`; on any other exception logs it; then
    `_transcribe_openai`; if that raises → `TranscriptionUnavailable`. Logs
    `STT provider: elevenlabs|openai`.
  - `_transcribe_elevenlabs`: catches `elevenlabs.core.api_error.ApiError`, re-raises
    as `ElevenLabsQuotaError` when `_is_quota_error(e.body)`.
  - `_is_quota_error(body)`: `detail.code == "insufficient_credits"` or legacy
    `detail.status == "quota_exceeded"`; anything not shaped like that → `False`.
  - `_transcribe_openai`: `OpenAI(timeout=15, max_retries=0)` per call;
    `audio.transcriptions.create(model="gpt-transcribe", file=(filename, audio_bytes),
    prompt=f"A question about a PDF titled {title}.", extra_body={"keywords":
    key_terms})` — `extra_body` only when `key_terms` is non-empty; returns
    `text.strip()`.
- `chats/api/views.py` — `TranscribeAPIView` catches `TranscriptionUnavailable` →
  `503 {"detail": "Voice input is unavailable right now, please type your question."}`.
- `pyproject.toml` / `uv.lock` — `openai>=2.44.0` declared explicitly (already
  installed via `langchain-openai`; version unchanged at 2.44.0).
- `CLAUDE.md` — voice service layer (provider order, quota detection, pause flag,
  OpenAI call), transcribe bullet (503 instead of "unhandled → 500"), B6 footer.
- `docs/decisions/DECISIONS.md` — 2026-11-04 entry on quota detection and OpenAI
  fail-fast.
- Plan Progress: B6 ticked as offline-verified.

## Why

The ElevenLabs free credits run out some months; the fallback keeps voice input
working, and the 503 gives the user a way forward when both providers fail.

## How / decisions

- **Quota detection differs from the plan.** The plan's `detail.status ==
  "quota_exceeded"` is from an older help-center article. ElevenLabs' current error
  docs (fetched 2026-11-04) list code `insufficient_credits` (type `payment_required`,
  HTTP 402) and call `status` a legacy field. Both are accepted. The SDK's
  `ScribeQuotaExceededErrorPayload` is for the realtime WebSocket API only, so it does
  not apply.
- **`keywords` shape** verified from OpenAI's speech-to-text guide (fetched
  2026-11-04): a list of strings via `extra_body`; forbidden `<`, `>`, CR, LF (already
  removed by B4 cleaning). Supported formats include all four the endpoint accepts.
- `max_retries=0` on OpenAI too (SDK default 2, timeout 600 s), consistent with the
  B5 fail-fast decision.
- Per-call OpenAI client so a missing `OPENAI_API_KEY` fails the request (→ 503), not
  server startup.

## Affected modules and behaviors

- `POST /chats/api/<pk>/transcribe/`: provider failures are now a fallback or a 503,
  no longer a 500. Response shape on success unchanged.
- The fallback spends OpenAI credit. The endpoint is unauthenticated and has no rate
  limit, so an OpenAI spending limit on the account is the only cap (as the plan says).
- Pause flag in `LocMemCache`: per worker process, cleared by a restart.
- No change to ask, upload, auth, async tasks, webhooks or analytics.

## Intended outcome

Voice input keeps working when ElevenLabs fails or runs out of credits; signal: logs
show `STT provider: openai` during an ElevenLabs outage, ElevenLabs is skipped for
6 h after a credits error, and users see the 503 message only when both fail.

## Verification

Offline only — no paid calls. All scenarios through the real route, view, serializer,
`transcribe()` and both SDKs; httpx answered `api.elevenlabs.io` / `api.openai.com`
locally (other hosts passed through), outgoing requests recorded. Script:
scratchpad `b6_offline.py`.

| Scenario | HTTP | Calls EL/OA | Paused |
|---|---|---|---|
| ElevenLabs 200 | 200 `from elevenlabs` | 1/0 | no |
| ElevenLabs 401 `invalid_api_key` | 200 `from openai` | 1/1 | no |
| ElevenLabs 402 `insufficient_credits` | 200 `from openai` | 1/1 | yes |
| next request while paused | 200 `from openai` | 0/1 | yes |
| ElevenLabs 401 legacy `status: quota_exceeded` | 200 `from openai` | 1/1 | yes |
| ElevenLabs 500, non-JSON body | 200 `from openai` | 1/1 | no |
| `ELEVENLABS_API_KEY=""` | 200 `from openai` | 0/1 | no |
| ElevenLabs 503 + OpenAI 500 | 503 + `detail` message | 1/1 | no |

Log lines matched each path (`ERROR … using OpenAI`, `WARNING … pausing it`,
`INFO: STT provider: …`). OpenAI upload fields: chat 12 → `model=gpt-transcribe`,
`prompt=A question about a PDF titled Quantum test (voice B4).`, 16 `keywords[]`
values; chat 11 → no keyword fields. Exception messages checked offline:
`str(ElevenLabsQuotaError)` = `ElevenLabs has no credits left (HTTP 402)`, cause
`ApiError`; `str(TranscriptionUnavailable)` = `Speech-to-text failed on every provider
(last: OpenAI InternalServerError)`, cause `InternalServerError`. `uv run ruff check .` → only the pre-existing
`chats/tests.py` F401.

**Not verified:** that the real OpenAI API accepts `gpt-transcribe` and reads the
`keywords[]` fields (the SDK's multipart encoding of the list); the real ElevenLabs
credits-exhausted body; real timeout behavior.

## Critical notes

- Set an OpenAI spending limit before exposing the endpoint.
- Worst-case wait when both providers hang: ElevenLabs' and OpenAI's per-phase
  timeouts added (8 s and 15 s per phase).
- Rollback: revert the commit; no migration.
