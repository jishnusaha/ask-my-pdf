from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

query_rewriter_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You are a query rewriting assistant. Your job is to rewrite the user's latest 
                question into a standalone search query for retrieving relevant document chunks 
                from a vector database.

                Rules:
                - Resolve any pronouns or references (it, that, they, this) using the chat history
                - The rewritten query should make sense WITHOUT the chat history
                - Keep it concise — one sentence, search-query style
                - Do NOT answer the question, only rewrite it
                - If the question is already standalone, return it as-is
            """,
        ),
        (
            "human",
            """Chat History:
                {chat_history}

                Latest Question:
                {question}

                Rewritten Query:
            """,
        ),
    ]
)

SYSTEM_PROMPT = """
    You are a helpful assistant that answers questions based strictly on the provided PDF context.
    If the answer is not found in the context, say "I couldn't find that in the document."
    Do not make up answers.

    Context from the PDF:
    {context}
"""


answer_generation_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """
                You are a helpful assistant that answers questions based strictly on the provided PDF context.
                If the answer is not found in the context, say "I couldn't find that in the document."
                Do not make up answers.

                Context from the PDF:
                {context}
            """,
        ),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{question}"),
    ]
)
