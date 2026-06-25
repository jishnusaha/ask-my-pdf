import pypdf
from io import BytesIO

from .types import PageData


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
