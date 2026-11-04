import logging

from django.conf import settings
from django.core.cache import cache
from elevenlabs import ElevenLabs, SpeechToTextChunkResponseModel
from elevenlabs.core.api_error import ApiError
from openai import OpenAI

logger = logging.getLogger(__name__)

ELEVENLABS_MODEL_ID = "scribe_v2"
# httpx timeout per phase (connect / upload / each wait for data), not a cap on
# the whole request; the SDK default is 240
ELEVENLABS_TIMEOUT_SECONDS = 8

OPENAI_MODEL_ID = "gpt-transcribe"
OPENAI_TIMEOUT_SECONDS = 15  # per phase as well; the SDK default is 600

# set when ElevenLabs reports no credits left, so requests skip it for a while;
# a time limit, not "until next month": credits reset on the billing date.
# The default cache is LocMemCache, one per worker process.
ELEVENLABS_PAUSE_KEY = "stt:elevenlabs:paused"
ELEVENLABS_PAUSE_SECONDS = 6 * 3600


class TranscriptionUnavailable(Exception):
    """Every speech-to-text provider failed."""


class ElevenLabsQuotaError(Exception):
    """ElevenLabs has no credits left."""


def transcribe(
    audio_bytes: bytes, filename: str, key_terms: list[str], title: str
) -> str:
    """
    Turns one recorded question into text. The only entry point for
    speech-to-text: callers never know which provider was used.

    Tries ElevenLabs first (unless its key is empty or it is paused after a
    quota error), then falls back to OpenAI. Raises TranscriptionUnavailable
    when both fail.

    `filename` must carry the real extension (e.g. "audio.webm"): the provider
    detects the audio format from it. `key_terms` are spelling hints (already
    cleaned by key_terms.py); `title` is context for the OpenAI prompt.
    Returns "" when nothing was said.
    """
    if settings.ELEVENLABS_API_KEY and not cache.get(ELEVENLABS_PAUSE_KEY):
        try:
            text = _transcribe_elevenlabs(audio_bytes, filename, key_terms)
            logger.info("STT provider: elevenlabs")
            return text
        except ElevenLabsQuotaError as e:
            cache.set(ELEVENLABS_PAUSE_KEY, True, timeout=ELEVENLABS_PAUSE_SECONDS)
            logger.warning("%s; pausing it and using OpenAI", e)
        except Exception:
            logger.exception("ElevenLabs transcription failed, using OpenAI")

    try:
        text = _transcribe_openai(audio_bytes, filename, key_terms, title)
    except Exception as e:
        logger.exception("OpenAI transcription failed")
        # OpenAI is always the last provider tried; its error is chained as __cause__
        raise TranscriptionUnavailable(
            f"Speech-to-text failed on every provider (last: OpenAI {type(e).__name__})"
        ) from e
    logger.info("STT provider: openai")
    return text


def _transcribe_elevenlabs(
    audio_bytes: bytes, filename: str, key_terms: list[str]
) -> str:
    client = ElevenLabs(
        api_key=settings.ELEVENLABS_API_KEY, timeout=ELEVENLABS_TIMEOUT_SECONDS
    )
    # chats created before key terms existed have [], so send no keyterms at all
    # (keyterm prompting also costs extra)
    hints = {"keyterms": key_terms} if key_terms else {}
    try:
        result = client.speech_to_text.convert(
            file=(filename, audio_bytes),
            model_id=ELEVENLABS_MODEL_ID,
            tag_audio_events=False,  # no "(laughter)" etc. in the question text
            # the SDK retries 5xx/408/409/429 twice with backoff by default; each
            # retry is billed and keeps the user waiting, so fail fast instead
            request_options={"max_retries": 0},
            **hints,
        )
    except ApiError as e:
        if _is_quota_error(e.body):
            raise ElevenLabsQuotaError(
                f"ElevenLabs has no credits left (HTTP {e.status_code})"
            ) from e
        raise
    # the response is a union; a plain single-channel request returns this type
    if not isinstance(result, SpeechToTextChunkResponseModel):
        raise TypeError(f"Unexpected ElevenLabs response type: {type(result).__name__}")
    return result.text.strip()


def _is_quota_error(body) -> bool:
    """
    Out of credits is read from the error body, not the status code (401 can
    also mean a wrong key). ElevenLabs' error docs: `detail.code` is
    "insufficient_credits"; `detail.status` is a legacy field, which older
    responses set to "quota_exceeded".
    """
    detail = body.get("detail") if isinstance(body, dict) else None
    if not isinstance(detail, dict):
        return False
    return (
        detail.get("code") == "insufficient_credits"
        or detail.get("status") == "quota_exceeded"
    )


def _transcribe_openai(
    audio_bytes: bytes, filename: str, key_terms: list[str], title: str
) -> str:
    # created per call: reads OPENAI_API_KEY from the environment, and a missing
    # key then fails this request instead of server startup
    client = OpenAI(timeout=OPENAI_TIMEOUT_SECONDS, max_retries=0)
    # the SDK has no `keywords` parameter yet; OpenAI's guide passes it via
    # extra_body as a list of strings. Omitted for chats without key terms.
    hints = {"extra_body": {"keywords": key_terms}} if key_terms else {}
    result = client.audio.transcriptions.create(
        model=OPENAI_MODEL_ID,
        file=(filename, audio_bytes),
        prompt=f"A question about a PDF titled {title}.",
        **hints,
    )
    return result.text.strip()
