from chats.models import Chat, DocumentChunk
from django.db import transaction

from chats.services.types import ChunkData


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
                    embedding=chunk.embedding,
                )
                for chunk in chunks
            ]
        )

    return chat
