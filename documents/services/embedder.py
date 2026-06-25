from langchain_openai import OpenAIEmbeddings
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .types import ChunkData, PageData

# initialize once at module level — no need to recreate on every call
splitter = RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=50)

embeddings_model = OpenAIEmbeddings(
    model="text-embedding-3-small",  # 1536 dimensions, cheap
)


def chunk_pages(pages: list[PageData]) -> list[ChunkData]:
    """
    Splits the text of each page into smaller chunks.
    Args:
        pages (list[PageData]): A list of PageData objects, each containing the page number and text.
    Returns:
        list[ChunkData]: A list of ChunkData objects, each containing the page number, chunk index, and text.
    """
    chunks: list[ChunkData] = []

    for page in pages:
        page_chunks = splitter.split_text(page.text)
        for text in page_chunks:
            chunks.append(
                ChunkData(
                    page_number=page.page_number,
                    chunk_index=len(chunks),
                    text=text,
                )
            )

    return chunks


def embed_chunks(chunks: list[ChunkData]):
    """
    Generates embeddings for a list of ChunkData objects.
    Args:
        chunks (list[ChunkData]): A list of ChunkData objects, each containing the page number, chunk index, and text.
    Returns:
        list: A list of embeddings corresponding to the input chunks.
    """
    texts = [chunk.text for chunk in chunks]

    # single batch API call — much cheaper than one call per chunk,
    # langchain handles batching internally, so we don't have to worry about it.
    # default batch size is 1000(may vary)
    vectors = embeddings_model.embed_documents(texts)

    for chunk, vector in zip(chunks, vectors):
        chunk.embedding = vector

    return chunks
