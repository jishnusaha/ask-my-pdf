# 2026-11-05 — README and CLAUDE.md brought up to date with the voice-mode backend

Follows `bd1d873` (B8, last backend step). Docs only; no code change.

## What changed

**README.md** (it predated all voice-mode work)
- Features: voice-mode bullet (backend API; UI in the Next.js frontend).
- How it works: key-term extraction on upload and re-upload; a "Voice Question" flow
  (record → `/transcribe/` → ask with `mode = "voice"`); `mode` effects on rewrite,
  rerank, prompt and saving.
- Tech stack: speech-to-text row (ElevenLabs Scribe v2, OpenAI gpt-transcribe);
  frontend row mentions the separate Next.js app.
- Project structure: `TranscribeSerializer`, `TranscribeAPIView`, `rag/types.py`
  (missing before voice mode too), `rag/key_terms.py`, `services/voice/`, `docs/plan/`.
- Prerequisites: optional ElevenLabs key.
- Data models: `Chat.key_terms`, `ChatMessage.mode`.
- RAG pipeline table: `prompts.py`, `types.py`, `key_terms.py`, `query_rewriter.py`,
  `pipeline.py`.
- Query rewriting, reranking, streaming sections: voice-mode behavior, `mode` in the
  body, `done` field list, 400 for an invalid `mode`.
- New "Voice Mode (backend)" section, including deployment notes (ElevenLabs free
  plan non-commercial + attribution, recordings sent to third parties, no rate limit,
  nginx `client_max_body_size`).
- Environment variables table: added `SECRET_KEY`, `DEBUG` (both missing before),
  `CORS_ALLOWED_ORIGINS`, `ELEVENLABS_API_KEY`.
- Mermaid flow: key-term steps and a voice branch.
- API endpoints: `mode` on the stream row, `/transcribe/` row, pagination note.
- Roadmap: voice mode (backend) checked.

**CLAUDE.md**
- Architecture summary: voice package, the separate Next.js frontend, pointer to the
  plan and its Progress list.
- Ask data flow: rewrite skipped on the first text question vs always in voice mode;
  rerank query differs by mode.
- Route table: optional `mode` on `/messages/stream/`.
- Paginated GETs note: the Next.js frontend reads `GET /messages/`; CORS
  (`CorsMiddleware`, `CORS_ALLOWED_ORIGINS`, added in `018a895`).
- Unauthenticated-API note: ElevenLabs credit too, and no rate limit.

## Why

User request: check whether README.md and CLAUDE.md needed updates after the
voice-mode backend (B1–B8) and update them where necessary.

## How

Every added claim was checked against the code at `bd1d873`: `chats/models.py`
fields, `chats/api/*` class names, the `chats/services/` file tree, `config/settings.py`
(`DEBUG` default, `REST_FRAMEWORK` has no throttle classes, `CorsMiddleware` first),
`git show 018a895`. The Next.js frontend's use of `GET /messages/` comes from
`docs/plan/voice-mood/voice-mode-v2-02-frontend-plan.md`, not from its code (that repo
was not read).

## Affected modules and behaviors

None — documentation only.

## Verification

- `grep` for stale phrases ("only for retrieval", "Django Templates, vanilla CSS |",
  "not yet returned", "is not built yet", "ElevenLabs only") in both files → none.
- Not verified: that the edited Mermaid diagram renders (no renderer run).

## Critical notes

- The README's voice-mode deployment notes (non-commercial plan, privacy, spending
  limit, nginx) come from the voice-mode plan and B5/B6 changelogs.
