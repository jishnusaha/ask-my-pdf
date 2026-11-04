from collections.abc import Iterator

from chats.models import ChatMessage

from .query_rewriter import get_rewritten_query
from .embedder import retrieve_chunks
from .reranker import rerank_chunks
from .generate_response import generate_response_stream


def ask_stream(
    chat_id: int,
    original_query: str,
    chat_history: list[ChatMessage],
    mode: ChatMessage.Mode,
) -> Iterator[str]:
    """
    Handles the process of asking a question in a chat session.
    This includes rewriting the query, retrieving relevant document chunks,
    streaming the generated answer fragment by fragment, and saving the
    interaction in the database once the answer is complete.

    Nothing is saved if the generator is closed early (client disconnect) or
    the LLM call fails, so no partial answer is ever stored.
    """

    rewritten_query = get_rewritten_query(original_query, chat_history, mode)

    retrieved_chunks = retrieve_chunks(chat_id, rewritten_query)

    # voice: score against the filler-free query too (fillers add noise to the
    # cross-encoder); text: unchanged, scored against the original question.
    # Answer generation and saving always use the original question.
    rerank_query = rewritten_query if mode == ChatMessage.Mode.VOICE else original_query
    reranked_chunks = rerank_chunks(rerank_query, retrieved_chunks)

    parts = []
    for delta in generate_response_stream(
        original_query, chat_history, reranked_chunks, mode
    ):
        parts.append(delta)
        yield delta

    # save the original question and the generated answer as ChatMessages;
    # both rows of the turn carry the same mode
    ChatMessage.objects.bulk_create(
        [
            ChatMessage(
                chat_id=chat_id,
                role=ChatMessage.Role.USER,
                mode=mode,
                content=original_query,
            ),
            ChatMessage(
                chat_id=chat_id,
                role=ChatMessage.Role.ASSISTANT,
                mode=mode,
                content="".join(parts),
            ),
        ]
    )
