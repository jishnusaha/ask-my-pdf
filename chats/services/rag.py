from langchain_openai import ChatOpenAI

from chats.models import ChatMessage
from chats.services.embedder import retrieve_chunks


llm = ChatOpenAI(
    model="gpt-4o",
    temperature=0.2,  # deterministic output
)


def ask(chat_id: int, question: str):
    """
    Embeds the question and finds top-k most similar chunks for this chat.
    Then, it generates an answer based on the retrieved chunks and saves it as a ChatMessage.
    """
    # step 1 — retrieve
    chunks = retrieve_chunks(chat_id, question)

    context = "\n\n".join(
        [f"[Page {chunk.page_number}]\n{chunk.content}" for chunk in chunks]
    )

    # step 2 — build messages
    messages = build_messages(chat_id, question, context)

    # step 3 — call LLM
    response = llm.invoke(messages)
    answer = response.content

    # step 4 — save both messages atomically
    ChatMessage.objects.bulk_create(
        [
            ChatMessage(chat_id=chat_id, role=ChatMessage.Role.USER, content=question),
            ChatMessage(
                chat_id=chat_id, role=ChatMessage.Role.ASSISTANT, content=answer
            ),
        ]
    )


def build_messages(chat_id: int, question: str, context: str) -> list[dict]:
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
    history = ChatMessage.objects.filter(chat_id=chat_id).order_by("created_at")
    for msg in history:
        messages.append({"role": msg.role, "content": msg.content})

    # append current question
    messages.append({"role": "user", "content": question})

    return messages
