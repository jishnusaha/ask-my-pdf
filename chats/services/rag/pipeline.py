from chats.models import ChatMessage

from .query_rewriter import get_rewritten_query
from .embedder import retrieve_chunks
from .reranker import rerank_chunks
from .generate_response import generate_response


def ask(chat_id: int, original_query: str, chat_history: list[ChatMessage]):
    """
    Handles the process of asking a question in a chat session.
    This includes rewriting the query, retrieving relevant document chunks,
    generating a response, and saving the interaction in the database.
    """

    rewritten_query = get_rewritten_query(original_query, chat_history)

    retrieved_chunks = retrieve_chunks(chat_id, rewritten_query)

    reranked_chunks = rerank_chunks(original_query, retrieved_chunks)

    answer = generate_response(original_query, chat_history, reranked_chunks)

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
