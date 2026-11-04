# 2026-11-04 — Voice mode B5: the transcribe endpoint (ElevenLabs), plus a B4 cleaning fix

Plan: `docs/plan/voice-mood/voice-mode-v2-01-backend-plan.md`, step B5 (Stage 2).
Follows `839509a` (B4).

## What changed

**B5 — transcribe endpoint**
- `pyproject.toml` / `uv.lock` — new dependency `elevenlabs>=2.76.0` (added by the
  user with `uv add elevenlabs`; resolved 2.76.0).
- `config/settings.py` — `ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY", default="")`.
- `chats/services/voice/__init__.py` (new, empty, matching `rag/`).
- `chats/services/voice/transcriber.py` (new)
  - `transcribe(audio_bytes, filename, key_terms, title) -> str` — single entry
    point; ElevenLabs only for now; logs `STT provider: elevenlabs`. `title` is
    unused until B6.
  - `_transcribe_elevenlabs` — `ElevenLabs(api_key=settings.ELEVENLABS_API_KEY,
    timeout=8)`, `speech_to_text.convert(file=(filename, audio_bytes),
    model_id="scribe_v2", tag_audio_events=False, request_options={"max_retries": 0},
    keyterms=...)`; `keyterms` omitted when `key_terms` is empty. Raises `TypeError`
    unless the result is `SpeechToTextChunkResponseModel`; returns `text.strip()`.
- `chats/api/serializers.py` — `TranscribeSerializer`: `audio = FileField()`;
  `validate_audio` rejects > 5 MB and extensions other than `webm`/`mp4`/`m4a`/`wav`;
  `validate` adds `filename = "audio.<ext>"`. Module helper `_extension()`.
- `chats/api/views.py` — `TranscribeAPIView.post`: `get_object_or_404` → serializer
  (400) → `transcribe(audio.read(), filename, chat.key_terms, chat.title)` →
  `200 {"text": ...}`.
- `chats/api/urls.py` — `<int:pk>/transcribe/` → `api_chat_transcribe`.

**B4 fix — key-term cleaning**
- `chats/services/rag/key_terms.py` — `FORBIDDEN_CHARS = frozenset("<>{}[]\\")`
  replaces the `<`/`>` check. The ElevenLabs SDK docstring for `keyterms` says
  `<`, `>`, `{`, `}`, `[`, `]`, `\` are not supported; the plan listed only `<`, `>`.

**Docs**
- `CLAUDE.md` — transcribe route, new "Voice service layer" section, transcribe
  behavior bullet, env vars (`ELEVENLABS_API_KEY`, and the previously undocumented
  `CORS_ALLOWED_ORIGINS`), `key_terms.py` character list, B5 verification footer.
- `README.md` — `.env` example: `CORS_ALLOWED_ORIGINS`, `ELEVENLABS_API_KEY`.
- `docs/decisions/DECISIONS.md` — 2026-11-04 entry (fail fast, fixed filename,
  forbidden chars, no `no_verbatim`).
- Plan Progress: B5 ticked.

## Why

Voice input: the frontend (F2/F3) records one question and needs its text. After
this endpoint the question follows the normal ask path.

## How / decisions

- **No SDK retries.** The SDK retries 5xx/408/409/429 twice with backoff by default
  (checked in `elevenlabs/core/http_client.py`); each retry is billed and the user
  waits. B6's fallback is the recovery path instead.
- **`timeout=8` is per phase**, not whole-request (httpx). The plan assumed a total
  cap; one real call took 16.5 s with it set. A hard total deadline would need
  extra code (not added).
- **Fixed filename to the provider** (`audio.<ext>`): the provider needs only the
  extension; the client's filename is not forwarded. Beyond the plan, low cost.
- `tag_audio_events=False` explicitly: the SDK does not document the server default,
  and tags like `(laughter)` would end up in the question.
- `no_verbatim` (ElevenLabs filler removal) deliberately not used — see the decision
  log.

## Affected modules and behaviors

- New public endpoint `POST /chats/api/<pk>/transcribe/`, unauthenticated like the
  rest of the API: anyone who can reach the server can spend ElevenLabs credits.
- **Provider errors are unhandled until B6:** an ElevenLabs error, an empty key, or
  an unexpected response type is an uncaught exception → 500.
- No change to ask, upload, auth, async tasks, webhooks or analytics.

## Intended outcome

A recorded question posted to `/transcribe/` returns its text, spelled with the
chat's key terms; signal: correct transcripts of jargon (e.g. `QAOA`), validation
errors as 400s, and no audio stored anywhere.

## Verification

Cost-aware: no OpenAI calls in this step; two short ElevenLabs calls.

- Key loads: `settings.ELEVENLABS_API_KEY` non-empty (51 chars, no surrounding
  whitespace; value not printed).
- B4 fix, offline: `_clean_terms` drops `f[x]`, `{set}`, `back\slash`, `a<b`, `x>y`;
  keeps `CI/CD pipelines`, `Shor's algorithm`. No stored chat holds a forbidden char.
- Route: `reverse('api_chat_transcribe', args=[12])` → `/chats/api/12/transcribe/` →
  `TranscribeAPIView`.
- Real requests to the running server (no provider call reached):
  unknown chat → 404; `.ogg` → 400; 6 MB `.wav` → 400; empty file → 400
  (`"The submitted file is empty."`); no `audio` field → 400; no extension → 400;
  `GET` → 405.
- Two real ElevenLabs calls through the real route/view (Django test client), outgoing
  request observed at the httpx layer:
  - chat 12 (16 key terms set directly in the DB), `say`-generated 3.2 s wav →
    `200 {"text": "What is Shor's algorithm and QAOA?"}`, 16 `keyterms` fields sent,
    filename `audio.wav`, 16.5 s.
  - chat 11 (`key_terms == []`), 1.8 s m4a → `200 {"text": "What is this document
    about?"}`, 0 `keyterms` fields, 2.9 s.
- Retries, offline (httpx returns fake 503, no network): `max_retries=0` → 1 attempt,
  `ApiError 503`; SDK default → 3 attempts.
- `uv run ruff check .` → only the pre-existing `chats/tests.py` F401.

Not verified: a real browser `webm`/`mp4` recording; silence → `""`; the cause of the
16.5 s call (retry vs slow phases — not measured, to save credits); nginx
`client_max_body_size`; the hint-vs-no-hint comparison and the "no key term appears
unprompted" test from the plan (skipped to save credits).

## Critical notes

- Chat 12's `key_terms` were set by hand for the test (16 quantum terms; its chunks
  are the quantum PDF).
- Production behind nginx needs `client_max_body_size` ≥ 5 MB (default 1 MB → 413).
- Each transcribe request holds a sync worker while waiting for ElevenLabs; use the
  threaded gunicorn setup from `CLAUDE.md`.
- Rollback: revert the commit; no migration in this step. Removing the dependency
  needs `uv remove elevenlabs`.
