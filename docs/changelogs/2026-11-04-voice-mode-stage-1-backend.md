# 2026-11-04 — Voice mode Stage 1 (backend): `mode` field, `mode` in the ask request, voice answer prompt

Plan: `docs/plan/voice-mood/` (overview, backend plan steps B1–B3; Stage 1 of the
working order). B1 and B2 were committed by the user as `aeb0e86` ("Add mode for
ChatMessage"); B3 and the doc updates land in the commit that carries this file.

## What changed

**B1 — `ChatMessage.mode` (in `aeb0e86`)**
- `chats/models.py` — `ChatMessage.Mode` (`TextChoices`: `TEXT="text"`,
  `VOICE="voice"`) and `mode = CharField(max_length=5, choices=..., default=TEXT)`.
- `chats/migrations/0003_chatmessage_mode.py` — generated `AddField`. SQL adds the
  column with `DEFAULT 'text' NOT NULL` (fills existing rows) and then drops the DB
  default; Django supplies the default in Python.

**B2 — `mode` in the ask request (in `aeb0e86`)**
- `chats/api/serializers.py` — `MessageCreateSerializer.mode =
  ChoiceField(choices=ChatMessage.Mode.choices, default=ChatMessage.Mode.TEXT)`.
- `chats/api/views.py` — `MessageStreamAPIView.post` reads `mode` and passes it via
  `_event_stream(chat, question, mode)` to `ask_stream`.
- `chats/services/rag/pipeline.py` — `ask_stream(..., mode)` (required parameter);
  `mode=mode` on both rows of the `bulk_create`.

**B3 — voice answer prompt (this commit)**
- `chats/services/rag/prompts.py`
  - The answer system message is now concatenated from module constants:
    `_ANSWER_INTRO`, `_TEXT_FORMATTING_RULES` / `_VOICE_FORMATTING_RULES`,
    `_CITATION_RULES_AND_CONTEXT`.
  - `answer_generation_prompt` = intro + text rules + citations. Renders
    byte-for-byte identical to before (checked, see Verification).
  - New `voice_answer_generation_prompt` = intro + voice rules + citations, same
    message structure (system with `{context}`, `chat_history` placeholder, human
    `{question}`).
  - Voice rules: short plain spoken sentences; no Markdown of any kind; if a table
    or list is requested, give the same information in sentences and never refuse
    because of the format; don't copy Markdown style from the history.
- `chats/services/rag/generate_response.py`
  - New `voice_answer_chain = voice_answer_generation_prompt | llm | StrOutputParser()`.
  - `generate_response_stream(..., mode)` picks `(prompt, chain)` together from `mode`
    (`ChatMessage.Mode.VOICE` → voice, anything else → text).
  - `_get_system_prompt_tokens(prompt, pdf_context)` takes the chosen prompt, so the
    history budget counts the right system message.
- `chats/services/rag/pipeline.py` — passes `mode` to `generate_response_stream`.

**`mode` typing (this commit)**
- `chats/api/views.py` — `post()` converts with `ChatMessage.Mode(validated_data["mode"])`.
  DRF's `ChoiceField` returns a plain `str` (`'voice'`) when `mode` is sent, but the
  enum member (`ChatMessage.Mode.TEXT`) when it is defaulted; after the conversion it
  is always the enum. `_event_stream(..., mode: ChatMessage.Mode)`.
- `chats/services/rag/pipeline.py` `ask_stream` and
  `chats/services/rag/generate_response.py` `generate_response_stream`:
  `mode: str` → `mode: ChatMessage.Mode`.
- No behavior change: `TextChoices` subclasses `str`, so comparisons and DB writes are
  the same.

**Docs**
- `CLAUDE.md` — ask data flow, `prompts.py` / `generate_response.py` / `pipeline.py`
  rows, migrations list, streaming-protocol body (`mode`, its 400), Stage 1
  verification footer.
- `docs/decisions/DECISIONS.md` — 2026-11-04 entry (mode flag, shared prompt pieces,
  format fallback line).
- `docs/plan/voice-mood/voice-mode-v2-01-backend-plan.md` — Progress: B1–B3 ticked.

## Why

Voice mode (plan Stage 1): let the frontend mark a turn as voice mode, store it, and
get answers written to be spoken, before any audio work.

## How / decisions

- Shared prompt pieces instead of a copied prompt: the `[Page N]` citation format is
  consumed by `render_answer` and by the frontend speech cleaner (plan F5), so the
  citation rules must stay identical in both prompts.
- Answer length is controlled by the prompt, not `max_tokens` — `config.llm` and
  `TOKEN_BUFFER` are shared with text mode.
- The "never refuse because of the format" line was added after testing: without it,
  "Compare all the topics covered in the document, in a table." in voice mode
  returned "I couldn't find that in the document." on 2 of 2 runs, while text mode
  answered it with a table.

## Affected modules and behaviors

- Ask endpoint `/chats/api/<pk>/messages/stream/`: accepts optional `mode`; invalid
  value → 400 before the stream. Text-mode behavior is unchanged.
- No change to auth, async tasks, webhooks or analytics. `ChatMessageSerializer`
  does not yet expose `mode` (B8), so `GET /messages/` and the SSE `done` event are
  unchanged in shape.
- Templates untouched (out of scope per the plan); `chat.html` sends no `mode` and
  keeps getting text mode.

## Intended outcome

A turn sent with `"mode": "voice"` is stored as voice on both rows and answered in
short plain sentences with `[Page N]` citations; signal: voice answers contain no
Markdown and `content_html` still has citation badges, while text answers are
unchanged.

## Verification

All against the running dev server (`runserver 9001`), chat 11, real OpenAI calls.

- `manage.py migrate` → `Applying chats.0003_chatmessage_mode... OK`.
- B1: text ask after migration → `done`; rows 111/112 `mode=text`.
- B2: no `mode` → `done`, rows 113/114 `text`; `"voice"` → `done`, rows 115/116
  `voice`; `"loud"` → `HTTP 400 {"mode":["\"loud\" is not a valid choice."]}`,
  nothing saved.
- B3:
  - Text prompt rendered with `format_messages` compared to a pre-change capture →
    identical (rechecked after `ruff format` and after the fallback line).
  - Same question, text vs voice: text 58 words with `**bold**`; voice 41–53 words,
    no Markdown; both 1 `[Page N]` and 1 badge in `content_html`.
  - "…in a table." — text: Markdown table; voice (after fix, 2 runs): prose, no
    Markdown, no refusal.
  - Off-document question in voice → "I couldn't find that in the document."
  - `_get_system_prompt_tokens`: text 221, voice 304 (for context `"CTX"`).
- Typing: serializer output checked in `manage.py shell` (`'voice'` → `str`, defaulted
  → `Mode`); `ChatMessage.Mode(...)` maps `'voice'`, `'text'` and `Mode.TEXT` to enum
  members. Real requests after the change: voice → rows 137/138 `voice`; no mode →
  139/140 `text`; `"loud"` → 400. Pylance diagnostics on the three files: no errors
  (Pylance's default basic mode; the project has no type checker configured).
- `uv run ruff check .` → only the pre-existing `chats/tests.py` F401.

Not verified: behavior with long multi-turn voice histories; answer quality on other
PDFs (only chat 11 was used).

## Critical notes

- **Known gap (not fixed):** one voice run of the table question used the document
  but emitted zero `[Page N]` citations. Text mode is also loose here (one citation
  for a whole table). Citation rules were copied unchanged per the plan; tightening
  them would change text mode too — open decision.
- Rollback: revert the B3 commit for the prompt; migration `0003` is reversible
  (`migrate chats 0002`), dropping the column.
- Test rows 111–140 on chat 11 (30 messages) were created by this session's
  verification.
