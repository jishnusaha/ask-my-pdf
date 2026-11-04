# Ask My PDF: Voice Mode Frontend Plan v2 (Next.js, `ask-my-pdf-frontend`)

Each step adds one thing and ends with a test. Finish and test each step before starting the next.

**Rule:** text mode must keep working after every step.

**Turn rule:** the user must listen to the full answer before asking the next question. There is no interruption.

## Where things are in the code

| Plan term | Frontend code |
|---|---|
| `/ask` | `streamMessage` in `src/store/services/messageStream.ts` → `POST /chats/api/<id>/messages/stream/`. A plain `fetch` over SSE, not RTK Query. |
| `ask(text)` | `handleSend(question)` in `src/components/chat/ChatView.tsx` |
| user bubble / streamed answer | `pendingQuestion` / `streamedAnswer` state in `ChatView`, drawn by `MessageList` → `MessageBubble` |
| saved history | `useGetChatMessagesQuery(messagesArgs)`; on `done`, `ChatView` patches the saved messages into that cache |
| text input + Send | `src/components/chat/MessageInput.tsx` |
| message type | `IChatMessage` in `src/types/chat.ts` |
| request/response endpoints | `src/store/services/api.ts` (RTK Query) |

Project rules that bind these steps (from the frontend `CLAUDE.md`):
- One feature per component file (for example a new `MicButton.tsx`). Don't move everything into one container.
- Types are `I`-prefixed, string unions are `XType`, fields stay snake_case as the API returns them.
- File-upload mutations take `{ data: FormData }`. Components call `await mutation(args).unwrap()` in try/catch and show the error themselves, using `getErrorMessage`.
- No client-side Markdown. The backend's `render_answer` (`content_html`) is the only renderer.
- There is no test suite. The check is `npm run build` plus `npx eslint .`.

## Progress

- [ ] F1: Voice mode toggle
- [ ] F2: Record audio
- [ ] F3: Send audio to transcribe
- [ ] F4: The speaker module
- [ ] F5: The speech cleaner
- [ ] F6: The sentence buffer
- [ ] F7: Speak and reveal in sync
- [ ] F8: Turn lock (listen fully before the next question)
- [ ] F9: Render history
- [ ] F10: Read-aloud button

---

## Step F1: Voice mode toggle

**What to build**

- Add a "Voice mode" switch to the chat screen (in `ChatHeader` or next to `MessageInput`). Keep its state in `ChatView`. `ChatView` is keyed by `chatId`, so the switch resets when the user opens another chat.
- In `src/types/chat.ts`, add `export type ChatMessageModeType = 'text' | 'voice';`.
- `streamMessage` gets a `mode: ChatMessageModeType` argument and sends `JSON.stringify({ question, mode })`.
- `handleSend(question, mode)` passes it on. When the switch is on, send `mode: "voice"`.
- Everything else stays the same: you still type, and tokens still show one by one.

**Why**

This is the safest first step. You can see the voice-style answers (backend B3) before any audio work.

**Test**

Turn it on and ask a question. The answer should be short, with no tables. Turn it off: answers look as before.

---

## Step F2: Record audio

**What to build**

A mic button in its own file, `src/components/chat/MicButton.tsx`. Tap once to start, tap again to stop. Show it only when voice mode is on, because a spoken question is always sent with `mode: "voice"`.

1. Open the mic with these settings:
   ```javascript
   navigator.mediaDevices.getUserMedia({
     audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true }
   });
   ```
2. Pick a format the browser supports. Try `audio/webm;codecs=opus` first, then `audio/mp4` (Safari). Check with `MediaRecorder.isTypeSupported`. Remember the chosen type: F3 builds the filename from it.
3. Call `recorder.start()` **with no time slice**. The browser then gives one complete file when you stop.
4. On stop, build one `Blob`, then **stop the mic tracks** so the browser's red mic icon turns off.
5. Add a max length (for example 60 seconds) with auto-stop, and a small timer while recording.
6. If the user blocks the mic, show a clear message.

**Why**

This gives a single, complete audio file. Because it's one recording, not separate chunks, the file is always valid.

**Gotchas**

- **The mic needs a secure page.** `getUserMedia` only works on `https://` or on `localhost`. Locally, open the dev server as `http://localhost:3000` (the backend's CORS setting already allows `localhost`, not `127.0.0.1`). The deployed frontend must use HTTPS.

**Test (dev only)**

Play the Blob back on the page with `URL.createObjectURL`. Remove that playback after testing.

---

## Step F3: Send audio to transcribe

**What to build**

- In `src/types/chat.ts`, add `export interface ITranscribeResponse { text: string; }`.
- In `src/store/services/api.ts`, add a mutation, the same way as `reuploadChatPdf`:
  ```ts
  transcribeAudio: build.mutation<ITranscribeResponse, { chatId: number; data: FormData }>({
    query: ({ chatId, data }) => ({
      url: `/chats/api/${chatId}/transcribe/`,
      method: 'POST',
      body: data,
    }),
  }),
  ```
  It has no `invalidatesTags`: transcribing changes nothing on the server. Export `useTranscribeAudioMutation`.
- Build the body with `data.append('audio', blob, 'audio.webm')` (or `'audio.mp4'`), with an extension that matches the recorded type. The chat id is in the URL, so there is no `document_id` field.
- Show "Transcribing..." while waiting.
- When the response comes back:
  - If `text` is empty, show "I didn't catch that, try again."
  - If it fails, show `getErrorMessage(e)`. It already reads the backend's `detail`:
    - 503 (both speech providers failed): "Voice input is unavailable right now, please type your question." Add a **Try again** button that resends the same Blob, so keep the Blob until it succeeds. Keep the text input working.
  - Otherwise, call `handleSend(text, 'voice')`. That sets `pendingQuestion`, which **is** the user's bubble. Don't add a second bubble.
- Add a small attribution line, required by the ElevenLabs free plan: "Speech recognition by ElevenLabs (elevenlabs.io)". The bottom of `Sidebar` shows it on every page.

**Why**

After this step, the question follows the normal flow. Voice input is working.

The 503 message is a rare safety net: the backend already falls back to OpenAI, so it only shows when both providers fail.

**Test**

- Speak a question. Your words should appear as a bubble, then the answer streams in as text.
- Make the backend return 503 (for example, by breaking both API keys). The message and Try again button should appear.

---

## Step F4: The speaker module

**What to build**

One small file, `src/lib/speaker.ts`, with two functions:
- `speak(text, { onStart, onEnd })`
- `cancelSpeech()`, which calls `speechSynthesis.cancel()`

Inside, use the browser's `speechSynthesis`:

```javascript
function speak(text, { onStart, onEnd } = {}) {
  const u = new SpeechSynthesisUtterance(text);
  u.onstart = onStart;
  u.onend = onEnd;
  u.onerror = onEnd;          // treat an error like the end, so the app never gets stuck
  speechSynthesis.speak(u);   // queued automatically, plays in order
}
```

**Gotchas**

- **Voices load late.** `speechSynthesis.getVoices()` can be empty at first. Wait for the `voiceschanged` event, then pick an English voice. Prefer names with "Natural", "Google" or "Enhanced".
- **iOS needs a tap first.** Speech must be started once from a user tap. In voice mode the user can type, so unlock on **any** tap that starts a voice turn: the mic tap and the Send click. Do it once per page load, with a short silent utterance (`volume = 0`). Check this on a real iPhone.
- **Cancel fires `onerror`.** `cancelSpeech()` ends queued utterances with an error, which calls `onEnd`. That is fine: it unlocks the turn (F8).
- **Long utterances.** Some browsers stop long utterances early. Speaking one sentence at a time (F6) avoids this.

**Why**

The rest of the app only ever calls `speak()` and `cancelSpeech()`. To switch to a server TTS later, change only this one file.

**Test**

A temporary button that calls `speak("Hello, this is a test.")`.

---

## Step F5: The speech cleaner

**What to build**

A function `toSpeechText(text)` in `src/lib/speechText.ts` that:
- removes citations: `/\[Page\s+\d+\]/gi`, the same pattern the backend's `render_answer` uses. The backend never writes `[p. N]`.
- removes markdown symbols (`*`, `#`, `` ` ``, `>`), and list markers at the start of a line (`- `, `* `, `1. `). Voice answers have none, but saved text-mode answers do (F10).
- turns links into plain words
- fixes extra spaces, including the space left before a period after a citation is removed

**Why**

The screen shows the original text with citations. The voice speaks the cleaned version. Keeping this in one function makes it easy to test.

**Test**

There is no test runner in this project. Two options:
- Node's built-in runner, no new dependency: `node --experimental-strip-types --test src/lib/speechText.test.ts` (works on Node 22.16). The file under test must not use `@/` imports.
- Or add Vitest. That is a new dev dependency, so add a decision log entry.

Cases:

| Input | Output |
|---|---|
| `**Dropout** helps [Page 4].` | `Dropout helps.` |
| `Revenue grew [Page 2] [Page 5].` | `Revenue grew.` |
| `- **Speed:** fast [Page 3]` | `Speed: fast` |
| `See [the paper](https://x.com) on p. 2.` | `See the paper on p. 2.` |

---

## Step F6: The sentence buffer

**What to build**

A small pure helper in `src/lib/speechText.ts` (next to the cleaner, so the same test file covers it). In voice mode, as each token arrives:

1. Add it to a buffer string.
2. Check for a sentence end: `.`, `!` or `?` **followed by a space or new line**, with at least about 20 characters in the buffer. A new line also counts as an end.
3. When a sentence is complete, add it to a list, then remove it from the buffer. One token can hold more than one sentence end, so keep checking until none is left.
4. When `done` arrives, treat whatever is left as the last sentence.

**Example**

Tokens: `"Dropout"` `" randomly"` `" turns"` `" off"` `" neurons"` `"."` `" This"` ...

- After `"."` and the following space, the buffer holds "Dropout randomly turns off neurons." That's a sentence, so add it to the list and clear the buffer.
- "This..." starts filling the buffer again.

**Why**

- Waiting for the space after the period avoids splitting numbers like "3.5".
- The minimum length avoids splitting a short start like "E.g. ..." It does **not** help in the middle of a long sentence, so also skip a small list of abbreviations (`e.g.`, `i.e.`, `et al.`, `vs.`, `Fig.`).
- The prompt puts `[Page N]` before the period, but the model sometimes writes "...grew. [Page 4]". Then the citation starts the next sentence. That is fine on screen, and F7 skips speaking a sentence that is empty after cleaning.

**Test**

Don't play audio yet. Log the sentences to the console as they're found, and check that the splits look right. Add the splitter cases to the F5 test file.

---

## Step F7: Speak and reveal in sync

**What to build**

In voice mode, stop showing tokens directly. Keep the full text in a ref (memory, not on screen). For each sentence from F6:

1. Call `speak(toSpeechText(sentence), { onStart })`. If the cleaned text is empty (a sentence that was only a citation), don't speak it. Reveal it right away instead.
2. In `onStart`, add that sentence to `streamedAnswer`, so it appears on screen with its citations.

Display:
- `streamedAnswer` is shown as plain text (no client-side Markdown). Voice answers have no Markdown, so this looks right. `[Page N]` shows as plain text until the turn ends. Then the saved message's `content_html`, with citation badges, replaces it.
- `isThinking` stays on while `streamedAnswer === null`. In voice mode that now lasts until the first sentence **starts playing**, so there is no empty gap.

**Hold the `done` messages until the voice finishes.** Today, on `done`, `ChatView` runs `flushSync(setSentMessageIds)` and then patches the saved messages into the cache. `isPendingSaved` then hides the streamed bubbles in the same render. In voice mode, `done` arrives long before the audio ends, so the full answer would appear at once and end the subtitles. So:
- Move that `flushSync` + `updateQueryData` (+ fallback `invalidateTags`) block into one function, for example `commitSaved(saved)`.
- Text mode: call it on `done`, exactly as now.
- Voice mode: keep `saved` and call `commitSaved` when the turn ends (F8).

If `speechSynthesis` doesn't exist in the browser (`!('speechSynthesis' in window)`), fall back to showing tokens normally.

If the stream fails mid-answer, the existing `catch` already drops the partial answer (the backend saved nothing). Also call `cancelSpeech()` there, so the voice stops.

**Why**

This is where the main experience comes together. The voice plays sentence by sentence, and the text appears like subtitles. The first sentence plays while later ones are still being generated, so the user hears audio quickly.

**Test**

- Ask a question and watch each sentence appear as it's spoken.
- Check that citations show on screen but aren't read aloud.
- Check that the full saved answer (with badges) appears only after the last sentence ends.

---

## Step F8: Turn lock (listen fully before the next question)

**What to build**

Replace `isSending` in `ChatView` with one state value for the current turn (`TurnStateType`):

| State | Meaning | Mic and text input |
|---|---|---|
| `idle` | Waiting for a question | Enabled |
| `recording` | User is speaking | Mic shows "stop" |
| `transcribing` | Waiting for the transcribe endpoint | Disabled |
| `answering` | Answer is streaming and/or speaking | Disabled |

The turn ends only when **both** are true:
1. The stream sent `done`.
2. The **last** sentence's `onEnd` fired.

At the end of the turn, call `commitSaved(saved)` (F7), then go back to `idle`.

`MessageInput`'s `disabled` prop blocks only the Send button and Enter today. The `<input>` itself stays editable. Pass `disabled` to the `<input>` too, so the text input really is locked, and focus it again when the turn unlocks.

Show a small status while locked, like "Speaking…", so the user knows why the mic is disabled.

**Gotchas**

- **Don't unlock on `done` alone.** The text finishes much faster than the audio. If you unlock on `done`, the user can start a new question while the old answer is still playing.
- **Safety timer.** Some browsers sometimes skip `onend`. After `done`, start a timer based on the remaining text length (for example, about 1 second per 15 characters, plus 5 seconds). If it runs out, end the turn anyway, so the app never stays stuck.
- **Errors unlock too.** If the stream sends an `error` event or the request fails, show the error and go back to `idle`.
- **Leaving the page.** Next keeps hidden routes mounted (`cacheComponents`, React `<Activity>`), so going to the upload page doesn't unmount `ChatView`. Put `cancelSpeech()` in a `useEffect` cleanup. It runs both when `ChatView` unmounts (another chat, since `ChatView` is keyed by `chatId`) and when the route is hidden.
  - The stream is **not** aborted on leave (frontend decision, 2026-11-02), so tokens keep arriving. Set a "cancelled" ref in the cleanup, and stop speaking new sentences once it is set. Otherwise the voice starts again after `cancelSpeech()`.
  - When the turn is cancelled, call `commitSaved` as soon as `done` arrives, so the saved answer is there when the user comes back. The backend saves it as today.
- **Text mode:** the turn ends on `done`, exactly as now.

**Why**

This enforces the turn rule in one place. Because the user always hears the full answer, the saved answer always matches what they heard, so no extra backend logic is needed.

**Test**

- Ask a long question. Check that the mic and text box stay disabled until the voice finishes, not when the text finishes.
- Turn off the speakers' sound and confirm the lock still releases at the end.
- Break the backend mid-answer and check that the app goes back to `idle` with an error.
- Switch chats during an answer and check that the voice stops and doesn't start again. Go back and check that the saved answer is there.
- Go to the upload page during an answer and check the same.

---

## Step F9: Render history

**What to build**

After backend B8:
- Add `mode: ChatMessageModeType` to `IChatMessage`.
- `MessageBubble` gets a `mode` prop. Show a small mic icon on user bubbles where `mode === 'voice'`. `MessageList` passes `message.mode`, and the current turn's mode for the pending bubble.
- Show answers in full, as now.

**Why**

The user can see which questions were asked in voice mode. Since every answer was heard in full, no special handling is needed for answers.

The `done` event uses the same serializer, so a new voice question keeps its icon when the saved copy replaces the pending bubble, with no reload.

**Test**

Reload a chat with both typed and voice-mode questions and check the icons. Ask a new voice question and check that the icon stays after the answer is saved.

---

## Step F10: Read-aloud button

**What to build**

- A ▶ button on every **saved** assistant message (from `messages`, not the streaming bubble). Pass an `onReadAloud` callback and a `disabled` flag from `ChatView` through `MessageList` to `MessageBubble`.
- Speak `message.content` (the raw saved text), **not** `content_html`. Split it into sentences with the same helper from F6, run each through `toSpeechText` (F5), and speak them with `speak()` (F4). Text-mode answers have bullets and bold. Each list item ends with a new line, so it becomes its own sentence, and the cleaner removes the markers.
- Disable the button (and the mic and text input) while any answer or replay is playing, using the same turn state from F8.
- Works for text-mode answers too.

**Why**

It reuses everything already built, with no backend work and no stored audio. The ▶ tap is itself a user tap, so iOS speech works.

**Test**

Open an old text-mode chat, play an answer with a bullet list, and check that the mic stays disabled until it finishes and that no `*`, `-` or "Page 4" is read aloud.

---

_Checked on 2026-11-04 against frontend `main` @ `0922e16` and backend `main` @ `94019e0`, by reading the code. Nothing here has been run, and nothing has been tested in a browser. The iOS unlock and `onerror`-on-cancel behavior should be confirmed on real devices._
