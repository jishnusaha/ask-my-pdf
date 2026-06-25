from langchain_openai import ChatOpenAI

from chats.models import ChatMessage
from chats.services.embedder import retrieve_chunks
from langchain_core.output_parsers import StrOutputParser

from .prompts import query_rewriter_prompt

llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.2,  # deterministic output
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
    rewritten_query = get_rewritten_query(original_query, chat_history)

    # retrieve chunks
    chunks = retrieve_chunks(chat_id, rewritten_query)

    context = "\n\n".join(
        [f"[Page {chunk.page_number}]\n{chunk.content}" for chunk in chunks]
    )

    # step 2 — build messages
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


def build_messages(
    question: str, context: str, chat_history: list[ChatMessage]
) -> list[dict]:
    """
    Builds the full message list:
    - system prompt with context
    - previous chat history
    - current question
    """

    SYSTEM_PROMPT = """
        You are a helpful assistant that answers questions based strictly on the provided PDF context.
        If the answer is not found in the context, say "I couldn't find that in the document."
        Do not make up answers.

        Context from the PDF:
        {context}
    """
    messages = [{"role": "system", "content": SYSTEM_PROMPT.format(context=context)}]

    # load previous messages for this chat — resume chat support
    for msg in chat_history:
        messages.append({"role": msg.role, "content": msg.content})

    # append current question
    messages.append({"role": "user", "content": question})

    return messages
