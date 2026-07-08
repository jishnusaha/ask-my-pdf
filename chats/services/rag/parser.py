import html
import re

import pypdf
from io import BytesIO

from .types import PageData

# [Page 3]  ->  captures the page number
_CITATION_RE = re.compile(r"\[Page\s+(\d+)\]", re.IGNORECASE)
# **bold**  and  `inline code`
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_CODE_RE = re.compile(r"`([^`]+?)`")
# list markers
_UL_RE = re.compile(r"^[-*]\s+(.*)")
_OL_RE = re.compile(r"^\d+\.\s+(.*)")


def extract_text_by_page(file) -> list[PageData]:
    """
    Extracts text from a PDF file page by page.

    Args:
        file (File): The PDF file to extract text from.

    Returns:
        list[PageData]: A list of PageData objects, each containing the page number and text.
    """
    reader = pypdf.PdfReader(BytesIO(file.read()))
    pages = []
    for page_number, page in enumerate(reader.pages):
        text = page.extract_text()

        pages.append(
            PageData(
                page_number=page_number + 1,
                text=text,
            )
        )
    return pages


def _render_inline(text: str) -> str:
    """Escape a single line and convert inline markdown + page citations to HTML."""
    text = html.escape(text)
    text = _CODE_RE.sub(r"<code>\1</code>", text)
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _CITATION_RE.sub(
        lambda m: (
            f'<span class="citation" title="Source: page {m.group(1)}">'
            f'<span class="citation-icon">\U0001F4C4</span>Page {m.group(1)}</span>'
        ),
        text,
    )
    return text


def render_answer(text: str) -> str:
    """
    Convert a raw assistant answer (lightweight Markdown + "[Page N]" citations)
    into safe HTML for display in the chat template.

    Supports paragraphs, unordered/ordered lists, **bold**, `code`, and turns
    every "[Page N]" reference into a styled citation badge.
    """
    lines = (text or "").replace("\r\n", "\n").split("\n")

    parts: list[str] = []
    paragraph: list[str] = []
    list_type: str | None = None  # "ul" or "ol"

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f"<p>{'<br>'.join(paragraph)}</p>")
            paragraph.clear()

    def close_list() -> None:
        nonlocal list_type
        if list_type:
            parts.append(f"</{list_type}>")
            list_type = None

    for raw_line in lines:
        line = raw_line.strip()

        if not line:
            flush_paragraph()
            close_list()
            continue

        ul_match = _UL_RE.match(line)
        ol_match = _OL_RE.match(line)

        if ul_match or ol_match:
            flush_paragraph()
            wanted = "ul" if ul_match else "ol"
            if list_type != wanted:
                close_list()
                parts.append(f"<{wanted}>")
                list_type = wanted
            item = (ul_match or ol_match).group(1)
            parts.append(f"<li>{_render_inline(item)}</li>")
        else:
            close_list()
            paragraph.append(_render_inline(line))

    flush_paragraph()
    close_list()
    return "".join(parts)
