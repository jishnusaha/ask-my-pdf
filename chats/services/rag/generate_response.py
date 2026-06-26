import tiktoken

from langchain_core.output_parsers import StrOutputParser
from langchain_core.messages import HumanMessage, AIMessage
from chats.models import ChatMessage, DocumentChunk

from .config import llm, MODEL_NAME, TOKEN_BUFFER, TOKEN_LIMIT
from .prompts import answer_generation_prompt

generate_answer_chain = answer_generation_prompt | llm | StrOutputParser()


def generate_response(
    original_query: str,
    chat_history: list[ChatMessage],
    chunks: list[DocumentChunk],
):
    """
    Generates a response to the original query based on the retrieved chunks and chat history.
    """

    pdf_context = "\n\n".join(
        [f"[Page {chunk.page_number}]\n{chunk.content}" for chunk in chunks]
    )
    # calculate budget
    system_tokens = _get_system_prompt_tokens(pdf_context)
    original_query_tokens = _count_tokens(original_query, MODEL_NAME)
    budget = TOKEN_LIMIT - TOKEN_BUFFER - system_tokens - original_query_tokens

    # trim chat history to fit within budget
    trimmed_history = _trim_history(chat_history, budget)

    langchain_messages = _to_langchain_messages(trimmed_history)

    answer = generate_answer_chain.invoke(
        {
            "context": pdf_context,
            "chat_history": langchain_messages,  # MessagesPlaceholder expects a list of LangChain messages
            "question": original_query,
        }
    )
    return answer


def _get_system_prompt_tokens(pdf_context: str) -> int:
    rendered_messages = answer_generation_prompt.format_messages(
        context=pdf_context,
        chat_history=[],  # empty, we just want system message tokens
        question="",  # empty, we just want system message tokens
    )
    return _count_tokens(rendered_messages[0].content, MODEL_NAME)


def _trim_history(chat_history: list[ChatMessage], budget: int) -> list[ChatMessage]:
    trimmed = chat_history[::-1]
    token_counts = [_count_tokens(m.content, MODEL_NAME) for m in trimmed]
    total_tokens = sum(token_counts)
    while trimmed and total_tokens > budget:
        total_tokens -= token_counts.pop()
        trimmed.pop()
    trimmed.reverse()
    return trimmed


def _to_langchain_messages(chat_history: list[ChatMessage]) -> list[dict]:
    """
    Converts a list of ChatMessage instances to a list of LangChain message dictionaries.
    """
    langchain_messages = []
    for msg in chat_history:
        if msg.role == ChatMessage.Role.USER:
            langchain_messages.append(HumanMessage(content=msg.content))
        elif msg.role == ChatMessage.Role.ASSISTANT:
            langchain_messages.append(AIMessage(content=msg.content))
    return langchain_messages


def _count_tokens(text: str, model: str) -> int:
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding(
            "cl100k_base"
        )  # fallback, works for all GPT-4 family
    return len(encoding.encode(text))
