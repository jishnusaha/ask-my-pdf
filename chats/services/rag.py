import tiktoken
from langchain_openai import ChatOpenAI
from langchain_core.output_parsers import StrOutputParser

from chats.models import ChatMessage
from chats.services.embedder import retrieve_chunks

from .prompts import query_rewriter_prompt

TOKEN_LIMIT = 128000
TOKEN_BUFFER = 2000  # reserve for LLM response

MODEL_NAME = "gpt-4.1-mini"

llm = ChatOpenAI(
    model=MODEL_NAME,
    temperature=0.2,  # deterministic output
    max_tokens=TOKEN_BUFFER,
)


def ask(chat_id: int, original_query: str):
    """
    Embeds the question and finds top-k most similar chunks for this chat.
    Then, it generates an answer based on the retrieved chunks and saves it as a ChatMessage.
    """

    chat_history = list(
        ChatMessage.objects.filter(chat_id=chat_id).order_by("created_at")
    )

    # will only used for retrieving relevant chunks from the vector database
    if chat_history:
        rewritten_query = get_rewritten_query(original_query, chat_history)
    else:
        rewritten_query = original_query

    # retrieve chunks
    chunks = retrieve_chunks(chat_id, rewritten_query)

    context = "\n\n".join(
        [f"[Page {chunk.page_number}]\n{chunk.content}" for chunk in chunks]
    )

    messages = build_messages(original_query, context, chat_history)

    # step 3 — call LLM
    response = llm.invoke(messages)
    answer = response.content

    # step 4 — save both messages atomically
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


def get_rewritten_query(original_query: str, chat_history: list[ChatMessage]) -> str:
    """
    Returns the rewritten query for a given chat and original question.
    """

    formatted_history = format_chat_history_for_query_rewriter(chat_history[-6:])
    chain = query_rewriter_prompt | llm | StrOutputParser()
    return chain.invoke({"chat_history": formatted_history, "question": original_query})


def format_chat_history_for_query_rewriter(messages: list[ChatMessage]) -> str:
    """
    Formats the chat history for the query rewriter prompt.
    Each message is prefixed with "User:" or "Assistant:" based on its role.
    """

    formatted_history = []
    for msg in messages:
        role = "User" if msg.role == ChatMessage.Role.USER else "Assistant"
        formatted_history.append(f"{role}: {msg.content}")

    return "\n".join(formatted_history)


def count_tokens(text: str, model: str) -> int:
    try:
        encoding = tiktoken.encoding_for_model(model)
    except KeyError:
        encoding = tiktoken.get_encoding(
            "cl100k_base"
        )  # fallback, works for all GPT-4 family
    return len(encoding.encode(text))


def build_messages(
    question: str, context: str, chat_history: list[ChatMessage]
) -> list[dict]:

    SYSTEM_PROMPT = """
        You are a helpful assistant that answers questions based strictly on the provided PDF context.
        If the answer is not found in the context, say "I couldn't find that in the document."
        Do not make up answers.

        Context from the PDF:
        {context}
    """

    system_content = SYSTEM_PROMPT.format(context=context)
    budget = TOKEN_LIMIT - TOKEN_BUFFER
    budget -= count_tokens(system_content, MODEL_NAME)
    budget -= count_tokens(question, MODEL_NAME)

    # trim oldest history messages first until we fit in budget
    trimmed_history = chat_history[::-1]  # reverse to pop from the end (oldest first)

    # calculate token count first
    token_counts = [count_tokens(m.content, MODEL_NAME) for m in trimmed_history]
    total_tokens = sum(token_counts)
    while trimmed_history:
        if total_tokens <= budget:
            break

        total_tokens -= token_counts.pop()  # remove the corresponding token count
        trimmed_history.pop()  # drop oldest message

    trimmed_history.reverse()  # restore original order

    messages = [{"role": "system", "content": system_content}]
    for msg in trimmed_history:
        messages.append({"role": msg.role, "content": msg.content})
    messages.append({"role": "user", "content": question})

    return messages
