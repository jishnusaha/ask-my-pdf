# Plan: Re-upload PDF to Existing Chat

## Context

Currently, uploading a PDF always creates a new Chat and discards prior conversation history. If a source PDF is revised, the only option is to start a completely new Chat — losing all ChatMessage records. This feature adds a re-upload endpoint that replaces all `DocumentChunk` records for an existing Chat while preserving its messages. The core efficiency goal is **avoiding re-embedding unchanged chunks**: since OpenAI's `text-embedding-3-small` is called per chunk, re-embedding an entire unchanged document wastes money and time.

## Strategy: Content-Hash Deduplication

Compute a SHA-256 hash of each chunk's text in `chunk_pages()`. On re-upload, compare new hashes against existing ones in the DB. Chunks whose hash matches an existing row get their stored embedding copied — skipping the OpenAI call entirely. Only truly new or modified chunks go to the API.

---

## Implementation Steps

### 1. `chats/models.py` — Add `content_hash` to `DocumentChunk`

```python
content_hash = models.CharField(max_length=64, blank=True, default="")
```

`max_length=64` is exact for a SHA-256 hex digest. `default=""` keeps the field non-nullable while allowing a backfill migration.

---

### 2. Migration — Add field + backfill existing rows

Run `python manage.py makemigrations` to generate `AddField`. Then manually add a `RunPython` step to the generated migration to backfill existing rows:

```python
import hashlib

def backfill_content_hashes(apps, schema_editor):
    DocumentChunk = apps.get_model("chats", "DocumentChunk")
    chunks = list(DocumentChunk.objects.filter(content_hash=""))
    for chunk in chunks:
        chunk.content_hash = hashlib.sha256(chunk.content.encode()).hexdigest()
    DocumentChunk.objects.bulk_update(chunks, ["content_hash"], batch_size=500)

operations = [
    migrations.AddField(...),
    migrations.RunPython(backfill_content_hashes, migrations.RunPython.noop),
]
```

---

### 3. `chats/services/rag/types.py` — Add `content_hash` to `ChunkData`

```python
class ChunkData(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_number: int
    chunk_index: int
    text: str
    content_hash: str = ""   # ← add this
    embedding: Optional[List[float]] = None
```

Default `""` keeps the existing upload path working without any changes to its call sites.

---

### 4. `chats/services/rag/embedder.py` — Two changes

**a) Compute hash in `chunk_pages()`** — right where the final chunk text is produced:

```python
import hashlib

# inside the inner loop:
chunks.append(
    ChunkData(
        page_number=page.page_number,
        chunk_index=len(chunks),
        text=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )
)
```

Why here: `chunk_pages` owns the text-splitting step and is where the final chunk text first exists. Every downstream caller (initial upload, re-upload) gets the hash for free.

**b) Skip pre-embedded chunks in `embed_chunks()`**:

```python
def embed_chunks(chunks: list[ChunkData]):
    to_embed = [chunk for chunk in chunks if chunk.embedding is None]
    if not to_embed:
        return chunks
    texts = [chunk.text for chunk in to_embed]
    vectors = embeddings_model.embed_documents(texts)
    for chunk, vector in zip(to_embed, vectors):
        chunk.embedding = vector
    return chunks
```

Backward-compatible: initial upload always passes chunks with `embedding=None`, so behavior is identical.

---

### 5. `chats/services/rag/ingestor.py` — Two changes

**a) Update `ingest_chunks`** to save `content_hash`:

```python
DocumentChunk(
    ...
    content_hash=chunk.content_hash,   # ← add
)
```

**b) Add `reingest_chunks`**:

```python
def reingest_chunks(chat: Chat, chunks: list[ChunkData]) -> Chat:
    # Build hash → embedding lookup from existing DB rows (outside transaction)
    existing = {
        row["content_hash"]: row["embedding"]
        for row in chat.chunks.values("content_hash", "embedding")
        if row["content_hash"]
    }

    # Copy embeddings for unchanged chunks
    for chunk in chunks:
        if chunk.embedding is None and chunk.content_hash in existing:
            chunk.embedding = existing[chunk.content_hash]

    # Embed only new/changed chunks (relies on embed_chunks skipping non-None)
    embed_chunks(chunks)

    # Atomic delete + insert
    with transaction.atomic():
        chat.chunks.all().delete()
        DocumentChunk.objects.bulk_create([
            DocumentChunk(
                chat=chat,
                content=chunk.text,
                page_number=chunk.page_number,
                chunk_index=chunk.chunk_index,
                content_hash=chunk.content_hash,
                embedding=chunk.embedding,
            )
            for chunk in chunks
        ])

    return chat
```

**Key decisions:**
- Hash lookup and `embed_chunks` (OpenAI network call) happen **outside** the transaction to avoid holding a DB lock during network I/O.
- `chat.chunks.all().delete()` touches only `DocumentChunk` rows — `ChatMessage` records are unaffected, preserving all chat history.
- If `bulk_create` fails, the transaction rolls back and old chunks are restored.

---

### 6. `chats/views.py` — Add `ChatReuploadView`

```python
from chats.services.rag.ingestor import ingest_chunks, reingest_chunks

class ChatReuploadView(View):
    def post(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk)
        file = request.FILES.get("pdf")
        if not file:
            return redirect("chat_detail", pk=pk)

        pages = extract_text_by_page(file)
        chunks = chunk_pages(pages)
        reingest_chunks(chat, chunks)
        return redirect("chat_detail", pk=pk)
```

No GET handler — the form lives on `chat.html`. No title update — the chat retains its original title.

---

### 7. `chats/urls.py` — Register new URL

```python
path("<int:pk>/reupload/", ChatReuploadView.as_view(), name="chat_reupload"),
```

---

### 8. `chats/templates/chats/chat.html` — Add re-upload form

Insert between `<div id="chat-header">` and `<div id="messages">`:

```html
<div id="reupload-section">
    <span>Replace PDF:</span>
    <form method="POST" action="{% url 'chat_reupload' chat.id %}" enctype="multipart/form-data">
        {% csrf_token %}
        <input type="file" name="pdf" accept=".pdf" required>
        <button type="submit">Re-upload</button>
    </form>
</div>
```

Add corresponding CSS in `{% block extra_styles %}`. The Q&A form at the bottom POSTs to the current URL (`/chats/<pk>/`) with no `action=` — it is unaffected.

---

## Implementation Order

1. `types.py` → 2. `models.py` → 3. `embedder.py` → 4. run `makemigrations` + add backfill → 5. `ingestor.py` → 6. `views.py` → 7. `urls.py` → 8. `chat.html`

---

## Verification

1. **Migration**: `python manage.py migrate` → check `DocumentChunk.objects.filter(content_hash="").count()` == 0
2. **Fresh upload**: Upload a new PDF; confirm `content_hash` is populated on all chunks via the chunks API (`/chats/<pk>/chunks/`)
3. **Reupload identical PDF**: Re-upload the same file; add `print(len(to_embed))` in `embed_chunks` and confirm it prints `0` (zero OpenAI calls)
4. **Reupload modified PDF**: Change a few pages; confirm only the changed chunks are embedded
5. **Chat history preserved**: After re-upload, confirm all `ChatMessage` rows are still present
6. **Atomicity**: Inject a failure inside `bulk_create` in `reingest_chunks`; confirm old chunks are intact after the failed request
