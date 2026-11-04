from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# The rewrite prompts share everything but their last rules: the voice version
# also removes fillers, even from a question that is already standalone.
_REWRITE_INTRO_AND_RULES = """You are a query rewriting assistant. Your job is to rewrite the user's latest 
                question into a standalone search query for retrieving relevant document chunks 
                from a vector database.

                Rules:
                - Resolve any pronouns or references (it, that, they, this) using the chat history
                - The rewritten query should make sense WITHOUT the chat history
                - Keep it concise — one sentence, search-query style
                - Do NOT answer the question, only rewrite it
"""

_TEXT_REWRITE_LAST_RULE = """                - If the question is already standalone, return it as-is
            """

_VOICE_REWRITE_LAST_RULES = """                - The question was spoken: remove filler words (um, uh, like, you know), repeated words
                  and false starts. Keep the meaning.
                - If the question is already standalone, return it as-is, but still without the filler
                  words, repeated words and false starts
            """

_REWRITE_HUMAN = """Chat History:
                {chat_history}

                Latest Question:
                {question}

                Rewritten Query:
            """

query_rewriter_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _REWRITE_INTRO_AND_RULES + _TEXT_REWRITE_LAST_RULE),
        ("human", _REWRITE_HUMAN),
    ]
)

voice_query_rewriter_prompt = ChatPromptTemplate.from_messages(
    [
        ("system", _REWRITE_INTRO_AND_RULES + _VOICE_REWRITE_LAST_RULES),
        ("human", _REWRITE_HUMAN),
    ]
)

SYSTEM_PROMPT = """
    You are a helpful assistant that answers questions based strictly on the provided PDF context.
    If the answer is not found in the context, say "I couldn't find that in the document."
    Do not make up answers.

    Context from the PDF:
    {context}
"""


# The answer system prompt is split so the text and voice prompts share the intro
# and the citation rules word for word; only the formatting rules differ.
# render_answer and the frontend speech cleaner both rely on the exact [Page N] format.
_ANSWER_INTRO = """
                You are a helpful assistant that answers questions based strictly on the provided PDF context.

                The context is made up of blocks, and each block starts with a page marker like "[Page N]".

"""

_TEXT_FORMATTING_RULES = """                Formatting rules:
                - Answer using clear, well-structured Markdown: short paragraphs, **bold** for key terms,
                  and "- " bullet lists where it helps readability.

"""

_VOICE_FORMATTING_RULES = """                Formatting rules:
                - The answer will be read aloud. Answer in a few short, plain spoken sentences.
                - Do not use any Markdown: no bullet or numbered lists, no tables, no bold or italics,
                  no headings, no code blocks, and no links or URLs.
                - If the user asks for a table, a list or another visual format, give the same information
                  in spoken sentences instead. Never refuse or say the answer is missing because of the format.
                - Earlier answers in the chat history may use Markdown. Do not copy that style.

"""

_CITATION_RULES_AND_CONTEXT = """                Citation rules:
                - After every sentence or bullet that uses information from the context, cite the source
                  page inline using the exact format [Page N] (for example: "Revenue grew 12% [Page 4].").
                - If a statement draws on multiple pages, cite each one, e.g. "[Page 2] [Page 5]".
                - Only cite pages that actually appear in the provided context — never invent a page number.
                - If the answer is not found in the context, say "I couldn't find that in the document."
                  and do NOT add any citation.

                Do not make up answers.

                Context from the PDF:
                {context}
            """

answer_generation_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            _ANSWER_INTRO + _TEXT_FORMATTING_RULES + _CITATION_RULES_AND_CONTEXT,
        ),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{question}"),
    ]
)

# same structure as answer_generation_prompt, so the token-budget code works unchanged
voice_answer_generation_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            _ANSWER_INTRO + _VOICE_FORMATTING_RULES + _CITATION_RULES_AND_CONTEXT,
        ),
        MessagesPlaceholder(variable_name="chat_history"),
        ("human", "{question}"),
    ]
)

key_terms_prompt = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You extract key terms from a PDF. A speech-to-text model will use them as hints,
                so it spells document-specific words correctly when a user asks about the PDF out loud.

                Rules:
                - Return the 20 to 40 most important terms: names of people, organizations and products,
                  technical terms, acronyms, and other words a speech recognizer could misspell.
                - Copy each term exactly as it is written in the text.
                - Each term is at most 5 words.
                - Do not include common everyday words.
            """,
        ),
        (
            "human",
            """Title: {title}

                Text from the first pages:
                {text}
            """,
        ),
    ]
)
