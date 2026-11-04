# Ask My PDF: Voice Mode Plan v2 (Overview)

This version is simpler. The user asks a question, listens to the **full** answer, and only then can ask the next one. There is no interruption.

Files:

1. `voice-mode-v2-00-overview.md`: decisions, the full flow, working order (this file)
2. `voice-mode-v2-01-backend-plan.md`: step-by-step backend plan (Django, this repo `ask-my-pdf`)
3. `voice-mode-v2-02-frontend-plan.md`: step-by-step frontend plan (Next.js, repo `ask-my-pdf-frontend`)

The Django templates in this repo (`chats/templates/`) are **out of scope**. The voice UI is built only in the Next.js frontend.

**Main idea:** voice is a new input/output layer around the existing RAG pipeline, not a new pipeline. Query rewriting, retrieval, reranking, grounding and citations stay the same.

**Overall rule:** text mode must keep working after every step. If a step breaks text chat, fix it before moving on.

---

## Names used in this plan, and where they live in the code

| In this plan | In the code |
|---|---|
| "the document" | a `Chat` row (`chats/models.py`). One chat = one PDF. There is no separate document model. |
| "ask" | `POST /chats/api/<pk>/messages/stream/`, body `{"question": ...}`. It streams Server-Sent Events (`token`, then `done` or `error`). It is the only way to ask. |
| "chat history" | `GET /chats/api/<pk>/messages/` (paginated) |
| "transcribe" | new `POST /chats/api/<pk>/transcribe/` (B5) |
| `mode` | new field on `ChatMessage` and on the ask request, `"text"` or `"voice"` (B1, B2) |
| citations | `[Page N]`, for example `[Page 4]`. Set by the answer prompt, turned into badges by `render_answer`. |
| the LLM | the one shared `config.llm` (`gpt-4.1-mini`) |

---

## Key terms

- **Transcribe (STT):** speech → text. The user's voice becomes words.
- **TTS:** text → speech. The answer becomes audio.
- **Buffer:** a temporary string in frontend memory that collects tokens until a full sentence is ready. Nothing is saved to the database.
- **Turn:** one question and its full spoken answer. A new turn can start only after the current one finishes.

---

## Decisions made

### Turn rule
- The user must listen to the full answer before asking the next question.
- While an answer is being generated or spoken, the mic button and text input are disabled.
- They are enabled again only after the last sentence finishes playing.
- Saving stays as it is today: the question and answer are saved together after the full answer streams. The only change is that both rows get the new `mode` value.

### What `mode` means
- `mode` says whether voice mode was **on** for the turn. It is not "how the text was entered": in voice mode the user can still type.
- It is stored on both rows of the turn. On the question it drives the mic icon in history. On the answer it records that the voice prompt was used.

### Input (user's question)
- Push-to-talk: tap to start, tap to stop. Record one complete audio file.
- Send the file to the batch transcribe endpoint.
- The mic always opens with these settings:
  ```javascript
  navigator.mediaDevices.getUserMedia({
    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
  });
  ```
- Show the transcribed text as the user's message bubble, the same as a typed message.
- **Do not store the audio.** After transcription, the audio is dropped. Only the text is saved.

### Speech-to-text provider
- **Main:** ElevenLabs Scribe v2 (`scribe_v2`), on the free plan.
- **Fallback:** OpenAI `gpt-transcribe`, used when ElevenLabs fails or its free credits run out.
- Both sit behind one `transcribe()` function, so the provider can change without touching the rest of the app.
- Older OpenAI models (`whisper-1`, `gpt-4o-transcribe`, `gpt-4o-mini-transcribe`) are not used: they are deprecated and shut down on February 26, 2027.
- ElevenLabs free plan: non-commercial use only, with a small attribution line in the app. Users' voices go to ElevenLabs/OpenAI, so mention it in the privacy policy.

### Better transcription and retrieval
- **Key terms:** extract a short list of important terms from each PDF at upload **and at re-upload**. Pass them as hints (`keyterms` for ElevenLabs, `keywords` for OpenAI), so document-specific words are spelled correctly. Terms are cleaned when they are saved, so they fit both providers' rules. Chats created before this feature keep an empty list (no backfill) and are transcribed without hints.
- **Filler cleaning:** in voice mode, the rewrite step also removes fillers ("um", "uh", repeats, false starts). The cleaned query is used **only for search** (retrieval and reranking). The original question is saved, shown, and passed to answer generation.

### Output (assistant's answer)
- Voice mode uses its own answer prompt: short plain sentences, no tables, lists, bold, code blocks or links. Same grounding rules. Citations stay in the text in the same `[Page N]` format.
- Citations and markdown are shown on screen but removed from the spoken text.
- Speech uses the **browser's built-in `speechSynthesis`** (free, no server call). All speech goes through one `speak()` function.
- Tokens are buffered into sentences. Each sentence is spoken as soon as it is complete.
- **Text is revealed sentence by sentence, in sync with the audio**, like subtitles.
- Text is always shown in voice mode (citations are visual, mistakes are visible, users can read back, chat history stays consistent).

### Extra
- A ▶ read-aloud button on every saved answer (text mode too). It reuses the saved text, so no audio is stored.

---

## The full flow (example)

User asks: "What does the paper say about dropout?"

1. User turns on voice mode, taps the mic and speaks.
2. User taps stop. The browser holds one complete audio file in memory.
3. Frontend sends the file to `POST /chats/api/<pk>/transcribe/`. Backend sends it to ElevenLabs (or OpenAI if ElevenLabs fails) with the chat's key terms and returns `{"text": "..."}`.
4. Frontend shows the text as the user's bubble. The mic and text input are now locked.
5. Frontend calls `POST /chats/api/<pk>/messages/stream/` with `{"question": "<text>", "mode": "voice"}`.
6. Backend rewrites the question (cleans fillers) for search, retrieves, reranks, and streams `token` events using the voice prompt.
7. Frontend buffers tokens. When a sentence is complete, it removes citations and markdown, then calls `speak()`.
8. When a sentence starts playing, its text (with citations) appears on screen.
9. Backend saves the question and full answer (both with `mode = "voice"`) after the stream ends, as today, and sends the `done` event.
10. When the last sentence finishes playing, the frontend swaps in the saved answer (with citation badges) and unlocks the mic and text input. The user can ask the next question.

---

## Working order

Build backend and frontend together so each piece is testable early:

| Stage | Backend | Frontend | Result |
|---|---|---|---|
| 1 | B1, B2, B3 | F1 | Voice-style answers (still typed) |
| 2 | B4, B5, B6 | F2, F3 | Voice input works (with fallback) |
| 3 | B7 | none | Better retrieval for spoken questions |
| 4 | none | F4, F5, F6, F7 | Voice output works |
| 5 | none | F8 | Turn lock: listen fully before the next question |
| 6 | B8 | F9 | Chat history shows voice questions |
| 7 | none | F10 | Read-aloud button |

---

_Checked on 2026-11-04 against backend `main` @ `94019e0` and frontend `main` @ `0922e16`, by reading the code. Nothing in this plan has been run yet. Provider facts (ElevenLabs keyterm limits, OpenAI `gpt-transcribe` options) were checked against the providers' docs on the same day. The February 26, 2027 shutdown date comes from several secondary sources, not from OpenAI's deprecations page itself._
