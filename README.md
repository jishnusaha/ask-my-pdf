# AskMyPDF

AI-powered PDF chat application built with Django and LangChain. Upload any PDF and instantly start a conversation with it.

AskMyPDF uses a RAG (Retrieval-Augmented Generation) pipeline to extract, chunk, and embed your document into a vector database, then retrieves the most relevant context to answer your questions accurately — grounded strictly in the content of your PDF.

![Python](https://img.shields.io/badge/Python-3.13-blue)
![Django](https://img.shields.io/badge/Django-6.x-green)
![LangChain](https://img.shields.io/badge/LangChain-1.3.x-orange)
![pgvector](https://img.shields.io/badge/pgvector-PostgreSQL-336791)

---

## Features

- Upload a PDF and process it in one step
- Chat with your PDF using natural language
- Resume previous chats — full conversation history preserved
- Answers grounded strictly in PDF content, no hallucination
- Query rewriting for accurate retrieval on follow-up questions
- Token budget management to handle long conversations safely

---

## How it works

```
PDF Upload
  → extract text page by page              (pypdf)
  → split into overlapping chunks           (LangChain RecursiveCharacterTextSplitter)
  → batch embed chunks                      (OpenAI text-embedding-3-small)
  → store chunks + vectors in DB            (pgvector)

User Question
  → rewrite query using chat history        (resolves pronouns for better retrieval)
  → embed rewritten query
  → similarity search → top 5 chunks        (pgvector cosine distance)
  → trim chat history to fit token budget   (oldest messages dropped first)
  → build prompt with context + history
  → generate answer                         (gpt-4.1-mini)
  → save to chat history
```

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django, Django REST Framework |
| AI / LLM | LangChain, OpenAI (gpt-4.1-mini, text-embedding-3-small) |
| Vector Store | pgvector (PostgreSQL) |
| PDF Parsing | pypdf |
| Frontend | Django Templates, vanilla CSS |
| Package Manager | uv |

---

## Project Structure

```
ask-my-pdf/
├── config/                   # project settings
│   ├── settings.py
│   ├── urls.py
│   └── wsgi.py
├── chats/                    # main app
│   ├── models.py             # Chat, DocumentChunk, ChatMessage
│   ├── views.py              # ChatListCreateView, ChatDetailView
│   ├── urls.py
│   ├── services/
│   │   └── rag/
│   │       ├── __init__.py
│   │       ├── config.py           # model name, token limits, llm instance
│   │       ├── prompts.py          # query rewriter + answer generation prompts
│   │       ├── query_rewriter.py   # query rewriting for retrieval
│   │       ├── generate_response.py # prompt building + LLM call
│   │       ├── pipeline.py         # orchestrates the full ask() flow
│   │       ├── parser.py           # PDF text extraction
│   │       ├── embedder.py         # chunking + embedding + retrieval
│   │       └── ingestor.py         # atomic DB save
│   └── templates/
│       └── chats/
│           ├── base.html
│           ├── upload.html
│           └── chat.html
├── docker-compose.yml
├── .env
└── manage.py
```

---

## Getting Started

### Prerequisites

- Python 3.13+
- Docker (for PostgreSQL with pgvector)
- OpenAI API key

### 1. Clone the repository

```bash
git clone https://github.com/jishnusaha/ask-my-pdf
cd ask-my-pdf
```

### 2. Start PostgreSQL with pgvector

```bash
docker compose up -d
```

`docker-compose.yml`:

```yaml
services:
  db:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: askmypdf
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

volumes:
  pgdata:
```

### 3. Install dependencies

```bash
uv sync
```

### 4. Configure environment

```bash
cp .env.example .env
```

`.env`:

```env
OPENAI_API_KEY=sk-...
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/askmypdf
```

### 5. Run migrations

```bash
python manage.py migrate
```

### 6. Start the server

```bash
python manage.py runserver
```

Visit `http://localhost:8000/chats/`

---

## Data Models

```
Chat
  ├── title
  ├── created_at
  └── is_processed

DocumentChunk
  ├── chat (FK)
  ├── content          ← raw text, source of truth
  ├── page_number
  ├── chunk_index
  └── embedding        ← 1536-dim vector (pgvector)

ChatMessage
  ├── chat (FK)
  ├── role             ← "user" or "assistant"
  ├── content
  └── created_at
```

---

## RAG Pipeline

The pipeline is organized as a clean package under `chats/services/rag/`, each file with a single responsibility:

| File | Responsibility |
|---|---|
| `config.py` | Model name, token limits, shared LLM instance |
| `prompts.py` | `ChatPromptTemplate` for query rewriting and answer generation |
| `query_rewriter.py` | Rewrites follow-up questions into standalone retrieval queries |
| `embedder.py` | Chunks, embeds, and retrieves document chunks via pgvector |
| `generate_response.py` | Builds prompt with token-aware history trimming, calls LLM |
| `ingestor.py` | Atomically saves Chat and all DocumentChunks to DB |
| `parser.py` | Extracts text from PDF page by page using pypdf |
| `pipeline.py` | Orchestrates the full ask() flow |

### Query Rewriting

Follow-up questions like *"what did it say about the deadline?"* are rewritten into standalone queries like *"What are the project deadlines mentioned in the document?"* before retrieval. This resolves pronoun references and dramatically improves vector search accuracy. The rewritten query is used only for retrieval — the original question is preserved in the conversation.

### Token Budget Guard

Before calling the LLM, the pipeline calculates the total token consumption across the system prompt, retrieved context, chat history, and current question. If the total exceeds the model's context window, the oldest chat history messages are dropped first until the prompt fits. Retrieved chunks are preserved as long as possible since they are the primary source of truth.

---

## Environment Variables

| Variable | Description |
|---|---|
| `OPENAI_API_KEY` | Your OpenAI API key |
| `POSTGRES_DB` | PostgreSQL database name |
| `POSTGRES_USER` | PostgreSQL username |
| `POSTGRES_PASSWORD` | PostgreSQL password |
| `POSTGRES_HOST` | PostgreSQL host (e.g. `localhost`) |
| `POSTGRES_PORT` | PostgreSQL port (e.g. `5432`) |
| `ALLOWED_HOSTS` | Comma-separated list of allowed hosts (e.g. `localhost,127.0.0.1`) |
| `CSRF_TRUSTED_ORIGINS` | Trusted origins for CSRF (e.g. `http://localhost:8000`) |

---

## Future Improvement Plans

- [ ] Reranking retrieved chunks with a cross-encoder for better relevance ordering
- [ ] Streaming responses
- [ ] Multi-PDF support per chat
- [ ] Source citation with page numbers
- [ ] User authentication
- [ ] File upload progress indicator
- [ ] Export chat history