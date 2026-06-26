from sentence_transformers import CrossEncoder

from chats.models import DocumentChunk

from .config import RERANK_THRESHOLD

# model is loaded into memory once when this module is first imported
# subsequent calls reuse the same instance — no reloading
model = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")


def rerank_chunks(query: str, chunks: list[DocumentChunk]) -> list[DocumentChunk]:
    if not chunks:
        return []
    pairs = [(query, chunk.content) for chunk in chunks]
    scores = model.predict(pairs)
    scored_chunks = sorted(zip(scores, chunks), reverse=True)
    filtered_chunks = [
        chunk for score, chunk in scored_chunks if score >= RERANK_THRESHOLD
    ]
    if not filtered_chunks:
        # if no chunks meet the threshold, return the top chunk
        return [scored_chunks[0][1]]
    return filtered_chunks
