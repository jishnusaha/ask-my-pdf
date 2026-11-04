import logging

from django.conf import settings
from elevenlabs import ElevenLabs, SpeechToTextChunkResponseModel

logger = logging.getLogger(__name__)

ELEVENLABS_MODEL_ID = "scribe_v2"
# httpx timeout per phase (connect / upload / each wait for data), not a cap on
# the whole request; the SDK default is 240
ELEVENLABS_TIMEOUT_SECONDS = 8


def transcribe(
    audio_bytes: bytes, filename: str, key_terms: list[str], title: str
) -> str:
    """
    Turns one recorded question into text. The only entry point for
    speech-to-text: callers never know which provider was used.

    `filename` must carry the real extension (e.g. "audio.webm"): the provider
    detects the audio format from it. `key_terms` are spelling hints (already
    cleaned by key_terms.py); `title` is context for the fallback provider.
    Returns "" when nothing was said.
    """
    text = _transcribe_elevenlabs(audio_bytes, filename, key_terms)
    logger.info("STT provider: elevenlabs")
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
    result = client.speech_to_text.convert(
        file=(filename, audio_bytes),
        model_id=ELEVENLABS_MODEL_ID,
        tag_audio_events=False,  # no "(laughter)" etc. in the question text
        # the SDK retries 5xx/408/409/429 twice with backoff by default; each
        # retry is billed and keeps the user waiting, so fail fast instead
        request_options={"max_retries": 0},
        **hints,
    )
    # the response is a union; a plain single-channel request returns this type
    if not isinstance(result, SpeechToTextChunkResponseModel):
        raise TypeError(f"Unexpected ElevenLabs response type: {type(result).__name__}")
    return result.text.strip()
