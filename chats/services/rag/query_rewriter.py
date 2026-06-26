from langchain_core.output_parsers import StrOutputParser

from chats.models import ChatMessage

from .config import llm
from .prompts import query_rewriter_prompt

query_rewrite_chain = query_rewriter_prompt | llm | StrOutputParser()


def get_rewritten_query(original_query: str, chat_history: list[ChatMessage]) -> str:
    """
    Returns the rewritten query for a given chat and original question.
    """
    if not chat_history:
        return original_query

    formatted_history = _format_history(chat_history[-6:])

    return query_rewrite_chain.invoke(
        {"chat_history": formatted_history, "question": original_query}
    )


def _format_history(messages: list[ChatMessage]) -> str:
    """
    Formats the chat history for the query rewriter prompt.
    Each message is prefixed with "User:" or "Assistant:" based on its role.
    """

    formatted_history = []
    for msg in messages:
        role = "User" if msg.role == ChatMessage.Role.USER else "Assistant"
        formatted_history.append(f"{role}: {msg.content}")

    return "\n".join(formatted_history)
