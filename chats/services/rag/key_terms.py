import logging

from .config import llm
from .prompts import key_terms_prompt
from .types import KeyTerms

logger = logging.getLogger(__name__)

MAX_INPUT_CHARS = 8000  # caps the cost when the first pages are huge
MAX_TERMS = 40
# limits shared by both speech-to-text providers: ElevenLabs allows at most
# 5 words and 50 characters per keyterm; OpenAI rejects the whole request if
# a keyword contains "<", ">" or a line break
MAX_TERM_WORDS = 5
MAX_TERM_CHARS = 50

key_terms_chain = key_terms_prompt | llm.with_structured_output(KeyTerms)


def extract_key_terms(title: str, texts: list[str]) -> list[str]:
    """
    Returns up to MAX_TERMS important terms (names, technical words, acronyms)
    from the title and the given page texts, cleaned so they are valid hints
    for both speech-to-text providers.

    Never raises: on any failure it logs and returns [], so an upload never
    fails because of key terms.
    """
    try:
        text = "\n\n".join(texts)[:MAX_INPUT_CHARS]
        result = key_terms_chain.invoke({"title": title, "text": text})
        return _clean_terms(result.terms)
    except Exception:
        logger.exception("Key term extraction failed for %r", title)
        return []


def _clean_terms(terms: list[str]) -> list[str]:
    """
    Normalizes whitespace (which also removes line breaks), then drops terms
    that are empty, contain "<" or ">", are too long, or repeat an earlier
    term (case-insensitive). Keeps the first MAX_TERMS.
    """
    cleaned = []
    seen = set()
    for term in terms:
        term = " ".join(term.split())
        if not term or "<" in term or ">" in term:
            continue
        if len(term) > MAX_TERM_CHARS or len(term.split()) > MAX_TERM_WORDS:
            continue
        if term.casefold() in seen:
            continue
        seen.add(term.casefold())
        cleaned.append(term)
    return cleaned[:MAX_TERMS]
