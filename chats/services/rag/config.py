from langchain_openai import ChatOpenAI

RERANK_THRESHOLD = 3
TOKEN_LIMIT = 128000
TOKEN_BUFFER = 2000
MODEL_NAME = "gpt-4.1-mini"

llm = ChatOpenAI(
    model=MODEL_NAME,
    temperature=0.2,
    max_tokens=TOKEN_BUFFER,
)
