from chats.models import Chat, DocumentChunk
from django.db import transaction

from .embedder import embed_chunks
from .types import ChunkData


def ingest_chunks(title: str, chunks: list[ChunkData]) -> Chat:

    with transaction.atomic():
        chat = Chat.objects.create(title=title)

        # instead of creating each DocumentChunk individually, we can use bulk_create for efficiency
        # in individual creation, each DocumentChunk would result in a separate database query
        # which can be slow for large numbers of chunks
        DocumentChunk.objects.bulk_create(
            [
                DocumentChunk(
                    chat=chat,
                    content=chunk.text,
                    page_number=chunk.page_number,
                    chunk_index=chunk.chunk_index,
                    content_hash=chunk.content_hash,
                    embedding=chunk.embedding,
                )
                for chunk in chunks
            ]
        )

    return chat


def reingest_chunks(chat: Chat, chunks: list[ChunkData]) -> Chat:
    # Build hash → embedding map from existing rows (outside transaction to avoid
    # holding a DB lock across the OpenAI network call below)
    existing = {
        row["content_hash"]: row["embedding"]
        for row in chat.chunks.values("content_hash", "embedding")
        if row["content_hash"]
    }

    # Pre-populate embeddings for chunks whose text hasn't changed
    for chunk in chunks:
        if chunk.embedding is None and chunk.content_hash in existing:
            chunk.embedding = existing[chunk.content_hash]

    # Embed only the chunks that still have no embedding (new or changed text)
    embed_chunks(chunks)

    # Atomically replace all chunks — metadata (page_number, chunk_index) is
    # always freshly derived from the new PDF, never copied from old rows
    with transaction.atomic():
        chat.chunks.all().delete()
        DocumentChunk.objects.bulk_create(
            [
                DocumentChunk(
                    chat=chat,
                    content=chunk.text,
                    page_number=chunk.page_number,
                    chunk_index=chunk.chunk_index,
                    content_hash=chunk.content_hash,
                    embedding=chunk.embedding,
                )
                for chunk in chunks
            ]
        )

    return chat
