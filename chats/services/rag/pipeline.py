from collections.abc import Iterator

from chats.models import ChatMessage, DocumentChunk

from .query_rewriter import get_rewritten_query
from .embedder import retrieve_chunks
from .reranker import rerank_chunks
from .generate_response import generate_response, generate_response_stream


def ask(chat_id: int, original_query: str, chat_history: list[ChatMessage]):
    """
    Handles the process of asking a question in a chat session.
    This includes rewriting the query, retrieving relevant document chunks,
    generating a response, and saving the interaction in the database.
    """

    reranked_chunks = _retrieve_context(chat_id, original_query, chat_history)

    answer = generate_response(original_query, chat_history, reranked_chunks)

    _save_exchange(chat_id, original_query, answer)


def ask_stream(
    chat_id: int, original_query: str, chat_history: list[ChatMessage]
) -> Iterator[str]:
    """
    Streaming variant of ask(): yields the answer text fragment by fragment,
    then saves the interaction once the answer is complete.

    Nothing is saved if the generator is closed early (client disconnect) or
    the LLM call fails, so no partial answer is ever stored.
    """

    reranked_chunks = _retrieve_context(chat_id, original_query, chat_history)

    parts = []
    for delta in generate_response_stream(
        original_query, chat_history, reranked_chunks
    ):
        parts.append(delta)
        yield delta

    _save_exchange(chat_id, original_query, "".join(parts))


def _retrieve_context(
    chat_id: int, original_query: str, chat_history: list[ChatMessage]
) -> list[DocumentChunk]:
    rewritten_query = get_rewritten_query(original_query, chat_history)

    retrieved_chunks = retrieve_chunks(chat_id, rewritten_query)

    return rerank_chunks(original_query, retrieved_chunks)


def _save_exchange(chat_id: int, original_query: str, answer: str):
    # save the original question and the generated answer as ChatMessages
    ChatMessage.objects.bulk_create(
        [
            ChatMessage(
                chat_id=chat_id, role=ChatMessage.Role.USER, content=original_query
            ),
            ChatMessage(
                chat_id=chat_id, role=ChatMessage.Role.ASSISTANT, content=answer
            ),
        ]
    )
