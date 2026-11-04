# Ask My PDF: Voice Mode Backend Plan v2 (Django)

Each step adds one thing and ends with a test. Finish and test each step before starting the next.

**Rule:** text mode must keep working after every step.

**Saving stays the same:** `pipeline.ask_stream` saves the question and the answer with one `bulk_create` after the full answer streams, as today. The only change to saving is that both rows get the new `mode` value (B2). No changes to the streaming logic.

## Where things are in the code

| Plan term | Code |
|---|---|
| "the document" | `Chat` in `chats/models.py`. One chat = one PDF. There is no document model. |
| `document_id` | the chat's `pk`, in the URL |
| `/ask` | `POST /chats/api/<pk>/messages/stream/`: `MessageStreamAPIView` → `_event_stream` → `pipeline.ask_stream` |
| ask request serializer | `MessageCreateSerializer` (`chats/api/serializers.py`), today only `question` |
| message serializer | `ChatMessageSerializer`. Used by `GET /messages/` **and** by the SSE `done` event. |
| answer prompt | `answer_generation_prompt` (`chats/services/rag/prompts.py`) |
| rewrite step | `get_rewritten_query` (`chats/services/rag/query_rewriter.py`) |
| citation format | `[Page N]`, parsed by `_CITATION_RE` in `chats/services/rag/parser.py` |
| LLM | the shared `llm` in `chats/services/rag/config.py` (`gpt-4.1-mini`), used by both the rewrite and the answer |

Today's ask flow, for reference: `MessageStreamAPIView.post` validates `question` (400) and finds the chat (404) before the stream starts. Then `ask_stream` runs rewrite → `retrieve_chunks(chat_id, rewritten_query)` → `rerank_chunks(original_query, ...)` → `generate_response_stream(original_query, ...)` → `bulk_create`.

## Progress

- [x] B1: Database field (2026-11-04, `aeb0e86`)
- [x] B2: Accept `mode` in the ask request (2026-11-04, `aeb0e86`)
- [x] B3: Voice answer prompt (2026-11-04, `7615cb5`)
- [x] B4: Extract key terms at upload and re-upload (new chats only) (2026-11-04, `839509a`)
- [x] B5: The transcribe endpoint (ElevenLabs) (2026-11-04, `3196437`)
- [x] B6: OpenAI fallback (2026-11-04, `6445853`; offline-verified only, real OpenAI call pending)
- [x] B7: Clean filler words in the rewrite step (2026-11-04, `47679f7`; offline-verified, real LLM output not checked)
- [x] B8: Return `mode` in the message API (2026-11-05)

---

## Step B1: Database field

**What to build**

Add a `mode` field to `ChatMessage` and run a migration:

```python
class ChatMessage(models.Model):
    class Role(models.TextChoices): ...

    class Mode(models.TextChoices):
        TEXT = "text", "Text"
        VOICE = "voice", "Voice"

    ...
    mode = models.CharField(max_length=5, choices=Mode.choices, default=Mode.TEXT)
```

- `python manage.py makemigrations chats` creates `0003_chatmessage_mode`. Then `python manage.py migrate`.

**Why**

- This records whether a turn happened in voice mode. With the default, old messages are marked as text automatically, and nothing else changes yet.
- The name is `mode`, not `input_mode`. The value means "voice mode was on", and in voice mode the user can still type (frontend F1), so "input mode" would be wrong for typed voice-mode questions.
- `ChatMessage` holds both the question and the answer, so both rows get the field. On the answer row it records that the voice prompt (B3) produced it.

**Test**

- Run the migration.
- `python manage.py shell -c "from chats.models import ChatMessage; print(set(ChatMessage.objects.values_list('mode', flat=True)))"` prints `{'text'}` (or `set()` on an empty database).
- Use text chat normally. Everything should work exactly as before.

---

## Step B2: Accept `mode` in the ask request

**What to build**

- `MessageCreateSerializer`: add
  ```python
  mode = serializers.ChoiceField(
      choices=ChatMessage.Mode.choices, default=ChatMessage.Mode.TEXT
  )
  ```
- `MessageStreamAPIView.post`: read `serializer.validated_data["mode"]` and pass it through `_event_stream(chat, question, mode)` to `ask_stream(chat.pk, question, list(chat.messages.all()), mode)`.
- `pipeline.ask_stream`: add a `mode` parameter. In the `bulk_create`, set `mode=mode` on **both** `ChatMessage(...)` rows.
- Don't change any other behavior yet.

**Why**

The frontend needs a way to say "this is a voice-mode question." Later steps use `mode` to pick the voice prompt (B3) and the filler cleaning (B7).

Validation runs in `post()` before the stream starts, so a bad `mode` returns a normal 400 JSON error, like a bad `question` does today.

**Test**

```bash
curl -N -X POST http://127.0.0.1:9001/chats/api/<pk>/messages/stream/ \
  -H 'Content-Type: application/json' \
  -d '{"question": "What is this document about?", "mode": "voice"}'
```

- Without `mode`: both new rows have `mode = "text"`.
- With `"mode": "voice"`: both new rows have `mode = "voice"`.
- With `"mode": "loud"`: `400`, body `{"mode": ["\"loud\" is not a valid choice."]}`, and nothing is saved.
- Check the rows in the database with `manage.py shell`.

---

## Step B3: Voice answer prompt

**What to build**

- In `prompts.py`, add `voice_answer_generation_prompt`. Give it the **same structure** as `answer_generation_prompt`: a system message with `{context}`, then `MessagesPlaceholder(variable_name="chat_history")`, then `("human", "{question}")`. Then the token-budget code works unchanged.
- Replace only the "Formatting rules" part. The voice version says:
  - Answer in a few short, plain spoken sentences.
  - No Markdown at all: no bullet or numbered lists, no tables, no bold, no headings, no code blocks, no links or URLs.
  - Earlier answers in the chat history may use Markdown. Do not copy that style.
- Copy the "Citation rules" part **unchanged**, including the exact `[Page N]` format and the "I couldn't find that in the document." rule. Don't change the format: `render_answer` turns `[Page N]` into badges, and the frontend speech cleaner (F5) removes exactly that pattern.
- In `generate_response.py`:
  - Add a second chain: `voice_answer_chain = voice_answer_generation_prompt | llm | StrOutputParser()`.
  - Add a `mode` parameter to `generate_response_stream`, and pick the prompt and chain from it.
  - `_get_system_prompt_tokens` uses `answer_generation_prompt` directly today. Pass it the chosen prompt, so the history budget counts the right system prompt.
- `pipeline.ask_stream` passes `mode` to `generate_response_stream`.

**Why**

- Spoken answers need a different style.
- Citations stay in the text because the user sees them on screen. The frontend removes them only from the spoken version.
- Length is controlled by the prompt, not by lowering `max_tokens`. `llm` is shared by the rewrite and both answer chains, and `TOKEN_BUFFER` is both its `max_tokens` and the history reserve. Changing it would change text mode too.
- The chat history mixes modes: a chat can have text answers with bullets followed by voice questions. That is why the voice prompt says not to copy the history's style.

**Test**

- Ask the same question in both modes. The voice answer should be shorter, with no tables or lists.
- Try a question that would normally produce a table, like "compare method A and method B."
- Check that the `done` event's assistant `content_html` still shows citation badges.

---

## Step B4: Extract key terms at upload and re-upload

**What to build**

- Add a field to `Chat`: `key_terms = models.JSONField(default=list, blank=True)`. Migration `0004_chat_key_terms`. Chats that already exist keep the default `[]`. **There is no backfill:** old chats simply transcribe without hints.
- New file `chats/services/rag/key_terms.py` (one job per file, like the rest of `rag/`) with:
  ```python
  def extract_key_terms(title: str, texts: list[str]) -> list[str]: ...
  ```
  - Put its prompt (`key_terms_prompt`) in `prompts.py`.
  - Use the shared `llm` from `config.py` (`gpt-4.1-mini`), not a separate `gpt-4o-mini` client. The project uses one OpenAI model for every call (decision log, 2026-11-02). A second model would be a separate decision.
  - Use `llm.with_structured_output(KeyTerms)`, with a small Pydantic model in `types.py` (`terms: list[str]`). Then there is no JSON to parse by hand.
  - Input: the title plus the text of the first few pages, cut to a fixed size (for example the first 8,000 characters), so a huge first page can't raise the cost.
  - Ask for important terms: names, technical words, acronyms. Keep about 20–40, and cut the list at 40.
  - **Clean every term before returning it**, so the same list works for both speech providers:
    - at most 5 words
    - under 50 characters
    - no `<`, `>` or line breaks (`\r`, `\n`)
    - drop empty or duplicate terms
  - Wrap the whole function in `try/except Exception`: log it and return `[]`. **Never fail an upload because of this.**
- **Upload:** `ChatCreateSerializer.create` already has `title` and `pages`. Call `extract_key_terms(title, [p.text for p in pages[:3]])` **before** `ingest_chunks`, outside its transaction, and pass the result in: `ingest_chunks(title, embedded_chunks, key_terms)` → `Chat.objects.create(title=title, key_terms=key_terms)`. This keeps the network call out of the transaction, the same reason `reingest_chunks` embeds before its `transaction.atomic()`.
- **Re-upload:** the PDF changes, so the terms must change too. In `ChatReuploadAPIView.post`, after `reingest_chunks`, set `chat.key_terms = extract_key_terms(chat.title, ...)` from the new `pages`, then `chat.save(update_fields=["key_terms"])`.

**Why**

In B5 and B6, these terms are given to the speech model as hints, so it spells the document's special words correctly.

The two providers have different rules (checked against their docs on 2026-11-04):
- **OpenAI** `gpt-transcribe` (`keywords`): rejects the **whole request** if a keyword contains `<`, `>`, a carriage return or a line feed.
- **ElevenLabs** `scribe_v2` batch (`keyterms`): up to 1,000 keyterms, each at most 50 characters and at most 5 words. Keyterm prompting **costs extra**, so it uses the free credits faster. 20–40 terms is plenty.

Cleaning once, when terms are saved, means one bad term can never break transcription later.

Each upload and re-upload now makes one more LLM call, about 1–2 seconds.

**Test**

- Upload a PDF and print its `key_terms` in `manage.py shell`.
- Check that they look like the right words, and that no term breaks the cleaning rules.
- Re-upload a different PDF to the same chat and check that the terms changed.
- Check that a chat created before this step still has `key_terms == []`.
- Make the call fail (for example, raise inside `extract_key_terms` temporarily). The upload must still succeed with `key_terms == []`.

---

## Step B5: The transcribe endpoint (ElevenLabs)

**Provider decision**

- **Main provider:** ElevenLabs Scribe v2 (`scribe_v2`), on the free plan.
- **Backup provider:** OpenAI `gpt-transcribe` (added in B6).
- Not using `whisper-1`, `gpt-4o-transcribe` or `gpt-4o-mini-transcribe`: they are deprecated and shut down on February 26, 2027.

**What to build**

1. Sign up for ElevenLabs (free plan, no card should be needed) and create an API key. Put it in `.env` as `ELEVENLABS_API_KEY`. In `settings.py`, read it with a default: `ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY", default="")`. Then the server still starts without it, and an empty key means "skip ElevenLabs" (B6). Never put the key in code or logs.
2. Add the official SDK: `uv add elevenlabs`. This is a new dependency. It is the documented way to send `keyterms` (`client.speech_to_text.convert(file=..., model_id="scribe_v2", keyterms=[...])`). The keyterm guide doesn't document the raw HTTP form of `keyterms`, so calling the API by hand would mean guessing the request shape.
3. Write one function that hides the provider from the rest of the app. Speech-to-text is not part of RAG, so give it its own package, `chats/services/voice/transcriber.py`:
   ```python
   def transcribe(audio_bytes: bytes, filename: str, key_terms: list[str], title: str) -> str:
       ...
   ```
   For now, it only calls ElevenLabs (`scribe_v2`) with the key terms as `keyterms`. `title` is the only context we have about the audio, and B6 uses it.
   Old chats (and chats where extraction failed) have `key_terms == []`. Then leave `keyterms` out of the call entirely, instead of sending an empty list.
4. Create a new endpoint `POST /chats/api/<pk>/transcribe/`:
   - `TranscribeAPIView(APIView)` in `chats/api/views.py`, route name `api_chat_transcribe` in `chats/api/urls.py`. The trailing slash is required, like the other API routes.
   - Multipart body, one file field: `audio`. The chat id is in the URL, so there is no `document_id` field.
   1. `get_object_or_404(Chat, pk=pk)`: 404 if the chat doesn't exist.
   2. Validate with a `TranscribeSerializer` (`audio = serializers.FileField()`): size at most 5 MB, and an allowed **extension**: `webm`, `mp4`, `m4a`, `wav`. Errors return a normal 400 JSON. Check the extension, not only `content_type`: browsers send values like `audio/webm;codecs=opus`, and the providers detect the format from the filename. Don't allow ogg: OpenAI's speech-to-text guide doesn't list it, so the fallback in B6 could fail. The frontend never records ogg anyway.
   3. Read `chat.key_terms` and `chat.title`.
   4. Call `transcribe(audio.read(), audio.name, chat.key_terms, chat.title)`.
   5. Return `200 {"text": "..."}`, with the text stripped.

Don't save the audio anywhere. Read it, send it, and let it go. Note: Django keeps uploads up to 2.5 MB (`FILE_UPLOAD_MAX_MEMORY_SIZE`) in memory. Bigger ones go to a temporary file that Django deletes after the request. 60 seconds of browser audio is well under 1 MB, so normally nothing touches the disk.

**Why**

- This turns the user's voice into text. After this, the question follows the normal text path.
- The `transcribe()` function means the endpoint never knows which provider is used. Adding the fallback (B6) or switching providers later only changes this one function.

**Gotchas**

- **The filename extension matters.** Pass the file on with its name (like `audio.webm` or `audio.mp4`), matching the real type.
- **nginx limits upload size.** The default `client_max_body_size` is only 1 MB. Raise it (for example to 10 MB), or uploads fail with a 413 error.
- **Silence.** If the user recorded nothing, the text may be empty. Return `200 {"text": ""}` so the frontend can say "I didn't catch that."
- **Set a timeout** on the ElevenLabs call (about 8 seconds), so a slow response doesn't keep the user waiting. Set it on the SDK client (check the SDK for the exact option name).
- **Workers.** Like the ask endpoint, each transcribe request holds a sync worker while it waits for the provider. With the B6 fallback, the worst case is both timeouts added together. Use the same threaded gunicorn setup that `CLAUDE.md` recommends.

**Free plan rules (ElevenLabs)**

- 10,000 credits per month, shared across ElevenLabs products. Roughly 2.5 hours of speech-to-text (estimate), which is about 900 questions of 10 seconds each. Keyterms cost extra, so the real number is lower.
- Credits reset each billing cycle (based on your signup date, not the 1st of the month). Unused credits don't carry over.
- **Non-commercial only.** No ads, paid plans or selling access. If the app ever earns money, upgrade to Starter ($6/month).
- **Attribution:** add a small line in the app, like "Speech recognition by ElevenLabs (elevenlabs.io)". See the frontend plan.
- **Privacy:** users' voices are sent to ElevenLabs (and OpenAI in B6). Mention this in the privacy policy.

**Test**

```bash
curl -X POST http://127.0.0.1:9001/chats/api/<pk>/transcribe/ -F "audio=@question.m4a"
```

- Record a short audio file on your phone and upload it. Check the text.
- Try a word from `key_terms` with and without the hint to see the difference.
- Say a sentence that contains none of the key terms, and check that none of them appear in the result.
- Transcribe on a chat created before B4 (`key_terms == []`). It must work, with no `keyterms` sent.
- A `.ogg` file or a file over 5 MB returns 400. A chat id that doesn't exist returns 404.

---

## Step B6: OpenAI fallback

**What to build**

Change `transcribe()` so it falls back to OpenAI when ElevenLabs fails:

1. Try ElevenLabs first, unless the key is empty or the pause flag is set.
2. If it works, return the text.
3. If it fails (credits used up, timeout or server error), call OpenAI `gpt-transcribe`.
4. If OpenAI also fails, raise `TranscriptionUnavailable`. The endpoint catches it and returns `503 {"detail": "Voice input is unavailable right now, please type your question."}`. Use the `detail` key: the frontend's error helper reads it.

```python
from django.conf import settings
from django.core.cache import cache

PAUSE_KEY = "stt:elevenlabs:paused"


def transcribe(audio_bytes, filename, key_terms, title):
    if settings.ELEVENLABS_API_KEY and not cache.get(PAUSE_KEY):
        try:
            text = transcribe_elevenlabs(audio_bytes, filename, key_terms)
            logger.info("STT provider: elevenlabs")
            return text
        except ElevenLabsQuotaError:
            cache.set(PAUSE_KEY, True, timeout=6 * 3600)  # skip for 6 hours
            logger.warning("ElevenLabs credits used up, using OpenAI")
        except Exception:
            logger.exception("ElevenLabs failed, using OpenAI")
    try:
        text = transcribe_openai(audio_bytes, filename, key_terms, title)
    except Exception as e:
        logger.exception("OpenAI transcription failed")
        raise TranscriptionUnavailable from e
    logger.info("STT provider: openai")
    return text
```

For the OpenAI call:
- The `openai` package is already installed (it comes with `langchain-openai`; `uv.lock` has 2.44.0). This code imports it directly, so add `openai` to `pyproject.toml` explicitly. It reads `OPENAI_API_KEY` from the environment, like the rest of the app.
- Create the client with a timeout, for example `OpenAI(timeout=15)`.
  ```python
  client.audio.transcriptions.create(
      model="gpt-transcribe",
      file=(filename, audio_bytes),
      prompt=f"A question about a PDF titled {title}.",
      extra_body={"keywords": key_terms},
  )
  ```
- Send `extra_body` only when `key_terms` is not empty (old chats have none).
- `keywords` is passed with `extra_body`, as in OpenAI's own Python example. Check that example for the exact value shape before coding.
- `title` is often just the filename (upload falls back to `file.name` when no title is given), for example `paper.pdf`. That is still useful context.
- Leave out `languages` (the model detects it). If you add it later, don't also send `language`: for `gpt-transcribe`, `languages` replaces it.

**Why**

- The free ElevenLabs credits will run out some months. The fallback keeps voice input working.
- **The pause flag:** without it, every question would first fail on ElevenLabs, then go to OpenAI, wasting about a second each time. The flag skips ElevenLabs for 6 hours, then tries again. A time limit is used instead of "wait until next month" because credits reset on the billing date, not the 1st.
- No `CACHES` setting exists, so Django uses `LocMemCache`, one per process. If you run several gunicorn workers, each worker learns about the empty credits separately. That still works but wastes a few requests. Use Redis for the cache if it is ever added.
- **The 503 case is rare.** It happens only when both providers fail, for example when the OpenAI spending limit is reached, the server loses internet, or an API key is wrong. The message gives the user a way forward instead of a broken screen.

**Gotchas**

- **Detect the quota error from the body, not the status code.** ElevenLabs reports `quota_exceeded` under "Error Code 400 or 401" in its help center. A 401 also means a wrong key. So `transcribe_elevenlabs` should raise `ElevenLabsQuotaError` only when the error body's `detail.status` is `"quota_exceeded"`. Every other error goes to the generic branch. Check the real body once by forcing the error.
- **Set a spending limit on your OpenAI account.** The fallback costs money (about 75 cents per 1,000 ten-second questions). A limit protects you if someone spams the endpoint. The API has no authentication and this plan adds no rate limit (left for later), so this limit is the only cap on spending for now.
- **Log which provider was used** for each request (shown above), so you can see how often the fallback runs.

**Test**

- Temporarily use a wrong ElevenLabs API key. Transcription should still work through OpenAI, the log should show the fallback, and the pause flag should **not** be set.
- Remove `ELEVENLABS_API_KEY`. Requests should go straight to OpenAI.
- Force the quota error (or mock it). Check that the pause flag is set and the next request goes straight to OpenAI.
- Break both keys. The endpoint should return 503 with the `detail` message.
- Optional: run 15–20 test recordings through both providers and compare mistakes and response times.

---

## Step B7: Clean filler words in the rewrite step

**What to build**

- Add a `mode` parameter to `get_rewritten_query(original_query, chat_history, mode)`.
- **Make it always run in voice mode.** Today it returns the original question unchanged when `chat_history` is empty, so the first question is never rewritten. In voice mode, call the LLM even with no history. Text mode keeps today's behavior.
- Add a voice version of the rewrite prompt (`voice_query_rewriter_prompt` in `prompts.py`) with one more rule: "Also remove filler words, repeated words and false starts. Keep the meaning." The text prompt says "If the question is already standalone, return it as-is", so the voice version must say that fillers are still removed in that case.
- In `pipeline.ask_stream`:
  - Retrieval already uses the rewritten query: `retrieve_chunks(chat_id, rewritten_query)`.
  - **Reranking does not.** Today `rerank_chunks(original_query, ...)` scores chunks against the original question, in both modes. In voice mode, pass the cleaned query to the reranker too, because fillers also add noise to the cross-encoder scores. Text mode stays as it is.
  - Answer generation and saving already use `original_query`. No change.

**Why**

- Fillers like "um" and "uh" add noise to the search vector and to the rerank score.
- The LLM writing the answer understands fillers fine, so it can see the original.
- The first question can have fillers too, so the rewrite must run even without history.
- Cost: the first voice question now makes one more LLM call (about 0.5–1 second) before retrieval, which delays the first audio a little.

**Example**

- Original (saved and shown): "um so what about the, uh, the second method they compared"
- Cleaned (used for search only): "What is the second method compared in the paper?"

**Test**

- Send "um so what's the, uh, the main idea" in voice mode, as the first question of a new chat.
- Log both versions. Retrieval and reranking should use the clean one, and the database should save the original.
- In text mode, the first question should still skip the rewrite.

---

## Step B8: Return `mode` in the message API

**What to build**

Add `"mode"` to `ChatMessageSerializer.Meta.fields`.

This one serializer is used by both `GET /chats/api/<pk>/messages/` and the SSE `done` event, so both get the field.

**Why**

The frontend uses it to show a small mic icon on voice-mode questions, both when a chat is reloaded and right after a new answer is saved.

**Test**

- Reload a chat that has both text and voice-mode questions, and check the JSON of `GET /chats/api/<pk>/messages/`.
- Ask a voice-mode question and check that both messages in the `done` event have `"mode": "voice"`.

---

## Docs to update when this is built

- `CLAUDE.md`: the new `/transcribe/` route, `mode` in the ask request and responses, `Chat.key_terms`, the new `ELEVENLABS_API_KEY` env var (optional), and the new dependencies.
- `README.md` env example.

---

_Checked on 2026-11-04 against `main` @ `94019e0`, by reading the code. Nothing here has been run. ElevenLabs keyterm limits and model id come from ElevenLabs' keyterm prompting guide. The `gpt-transcribe` options (`keywords` through `extra_body`, `prompt`, `languages`, supported formats) come from OpenAI's speech-to-text guide. The `quota_exceeded` status comes from ElevenLabs' help center. The exact SDK timeout option, the `keywords` value shape, and the quota error body are not yet verified._
