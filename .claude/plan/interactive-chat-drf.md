# Plan: Make Chat Page Interactive with DRF APIs

## Context

The current Django app uses traditional form POST → redirect flows for three operations: PDF upload (creates a chat), sending a chat message, and PDF re-upload. None of these give the user any feedback during processing — the page is blank until the redirect completes. The goal is to:

1. Add Django REST Framework and expose proper API endpoints using DRF generic views
2. Replace all form POST submissions with `fetch()` calls in the frontend
3. Show loading states, append messages to the DOM without full-page reloads, and give success/error feedback for re-uploads

Existing HTML template views (GET handlers) are kept unchanged. The APIs handle all mutations. There is no user authentication.

---

## Directory Structure

All API-related code lives in a dedicated `chats/api/` package:

```
chats/
  api/
    __init__.py
    serializers.py
    views.py
    urls.py
```

---

## URL Structure

The API routes live under `/chats/api/` by including `chats.api.urls` from `chats/urls.py`. The `chats/api/urls.py` paths have no `chats/` prefix — the parent already provides it.

| Method | URL | View | Purpose |
|---|---|---|---|
| GET | `/chats/api/` | `ChatListCreateAPIView` | List chats |
| POST | `/chats/api/` | `ChatListCreateAPIView` | Upload PDF → create chat |
| GET | `/chats/api/<pk>/messages/` | `MessageListCreateAPIView` | List messages |
| POST | `/chats/api/<pk>/messages/` | `MessageListCreateAPIView` | Ask question → returns [user, assistant] |
| POST | `/chats/api/<pk>/reupload/` | `ChatReuploadAPIView` | Replace PDF |
| GET | `/chats/api/<pk>/chunks/` | `ChunkListAPIView` | Paginated chunks |

---

## Files to Create

### `chats/api/__init__.py`
Empty.

### `chats/api/serializers.py`

```python
from rest_framework import serializers
from chats.models import Chat, ChatMessage, DocumentChunk
from chats.services.rag.parser import render_answer, extract_text_by_page
from chats.services.rag.embedder import chunk_pages, embed_chunks
from chats.services.rag.ingestor import ingest_chunks


class ChatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chat
        fields = ["id", "title", "created_at"]
        read_only_fields = ["id", "created_at"]


class ChatMessageSerializer(serializers.ModelSerializer):
    content_html = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ["id", "role", "content", "content_html", "created_at"]

    def get_content_html(self, obj):
        if obj.role == "assistant":
            return render_answer(obj.content or "")
        return None


class DocumentChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentChunk
        fields = ["id", "content", "page_number", "chunk_index", "content_hash"]


class ChatCreateSerializer(serializers.Serializer):
    pdf = serializers.FileField()
    title = serializers.CharField(max_length=255, required=False, allow_blank=True)

    def create(self, validated_data):
        file = validated_data["pdf"]
        title = validated_data.get("title", "").strip() or file.name
        pages = extract_text_by_page(file)
        chunks = chunk_pages(pages)
        embedded_chunks = embed_chunks(chunks)
        return ingest_chunks(title, embedded_chunks)


class MessageCreateSerializer(serializers.Serializer):
    question = serializers.CharField(min_length=1, trim_whitespace=True)
```

### `chats/api/views.py`

```python
from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.shortcuts import get_object_or_404

from chats.models import Chat
from chats.services.rag.parser import extract_text_by_page
from chats.services.rag.embedder import chunk_pages
from chats.services.rag.ingestor import reingest_chunks
from chats.services.rag.pipeline import ask

from .serializers import (
    ChatSerializer, ChatCreateSerializer,
    ChatMessageSerializer, MessageCreateSerializer,
    DocumentChunkSerializer,
)


class ChatListCreateAPIView(generics.ListCreateAPIView):
    queryset = Chat.objects.all().order_by("-created_at")

    def get_serializer_class(self):
        return ChatCreateSerializer if self.request.method == "POST" else ChatSerializer

    def create(self, request, *args, **kwargs):
        serializer = ChatCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chat = serializer.save()
        return Response(ChatSerializer(chat).data, status=status.HTTP_201_CREATED)


class MessageListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ChatMessageSerializer

    def get_chat(self):
        return get_object_or_404(Chat, pk=self.kwargs["pk"])

    def get_queryset(self):
        return self.get_chat().messages.all()

    def create(self, request, *args, **kwargs):
        chat = self.get_chat()
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question"]

        last_id = chat.messages.order_by("-id").values_list("id", flat=True).first() or 0
        ask(chat.pk, question, list(chat.messages.all()))
        new_messages = chat.messages.filter(id__gt=last_id).order_by("id")

        return Response(ChatMessageSerializer(new_messages, many=True).data, status=status.HTTP_201_CREATED)


class ChatReuploadAPIView(APIView):
    def post(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk)
        file = request.FILES.get("pdf")
        if not file:
            return Response({"error": "No PDF file provided."}, status=status.HTTP_400_BAD_REQUEST)

        pages = extract_text_by_page(file)
        chunks = chunk_pages(pages)
        reingest_chunks(chat, chunks)
        return Response(ChatSerializer(chat).data)


class ChunkListAPIView(generics.ListAPIView):
    serializer_class = DocumentChunkSerializer

    def get_queryset(self):
        chat = get_object_or_404(Chat, pk=self.kwargs["pk"])
        return chat.chunks.all()
```

### `chats/api/urls.py`

Paths here are relative to `/chats/api/` (the parent prefix):

```python
from django.urls import path
from chats.api.views import (
    ChatListCreateAPIView, MessageListCreateAPIView,
    ChatReuploadAPIView, ChunkListAPIView,
)

urlpatterns = [
    path("", ChatListCreateAPIView.as_view(), name="api_chat_list_create"),
    path("<int:pk>/messages/", MessageListCreateAPIView.as_view(), name="api_message_list_create"),
    path("<int:pk>/reupload/", ChatReuploadAPIView.as_view(), name="api_chat_reupload"),
    path("<int:pk>/chunks/", ChunkListAPIView.as_view(), name="api_chat_chunks"),
]
```

---

## Files to Modify

### `pyproject.toml`
Add `"djangorestframework>=3.15.0"` to `dependencies`, then run `uv sync`.

### `config/settings.py`
1. Add `"rest_framework"` to `INSTALLED_APPS` (after `"chats"`)
2. Append pagination-only DRF config:
```python
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.LimitOffsetPagination",
    "PAGE_SIZE": 20,
}
```

### `chats/urls.py`
Add `api/` include — no change to `config/urls.py` needed:
```python
from django.urls import path, include
from chats.views import ChatDetailView, ChatListCreateView, ChatChunksView, ChatReuploadView

urlpatterns = [
    path("", ChatListCreateView.as_view(), name="chat_list_create"),
    path("<int:pk>/", ChatDetailView.as_view(), name="chat_detail"),
    path("<int:pk>/chunks/", ChatChunksView.as_view(), name="chat_chunks"),
    path("<int:pk>/reupload/", ChatReuploadView.as_view(), name="chat_reupload"),
    path("api/", include("chats.api.urls")),
]
```

### `chats/templates/chats/upload.html`
- Replace `<form method="POST">` with a plain div; keep `{% csrf_token %}` only to ensure session cookie is set (not needed for fetch since DRF views are CSRF-exempt)
- On click: build `FormData`, POST to `/chats/api/`, show "Processing PDF…" status, redirect to `/chats/<id>/` on success
- On error: show error text, re-enable the button

### `chats/templates/chats/chat.html`

**1. Question input area** — remove `<form>` wrapper, use plain `<input id="question-input">` + `<button id="send-btn">`

**2. Reupload section** — remove `<form method="POST">`, use `<input id="reupload-input">` + `<button id="reupload-btn">` + `<span id="reupload-status">`

**3. Script block** — no CSRF handling needed since DRF views are CSRF-exempt:
- `appendMessage(msg)` — builds `.message.{role} > .bubble + .time`, uses `innerHTML` only for assistant `content_html` (server-sanitized), `textContent` for user messages (XSS-safe)
- `appendThinking()` — inserts temporary "Thinking…" bubble, returns element for later removal
- Send question: POST JSON (`Content-Type: application/json`) to `/chats/api/<pk>/messages/`, show thinking → append [user, assistant] on success; Enter key triggers send
- Reupload: POST `FormData` to `/chats/api/<pk>/reupload/`, show status, clear file input on success

---

## Current State of Implementation

Already done:
- `chats/api/__init__.py` created
- `chats/api/serializers.py` created
- `chats/api/views.py` created
- `chats/api/urls.py` created

Still to do:
- `pyproject.toml` — add `djangorestframework>=3.15.0` + `uv sync`
- `config/settings.py` — add `rest_framework` to INSTALLED_APPS + REST_FRAMEWORK config
- `chats/urls.py` — add `path("api/", include("chats.api.urls"))`
- `chats/templates/chats/upload.html` — interactive upload
- `chats/templates/chats/chat.html` — interactive messaging + reupload

---

## Verification

1. `python manage.py check` — zero errors
2. Browse to `/chats/api/` — DRF browsable API loads
3. Upload PDF via upload page — button shows "Processing…", redirects to chat page on success
4. Ask a question on chat page — "Thinking…" bubble appears, replaced by user + assistant messages without page reload
5. Re-upload a PDF — status shows "PDF replaced successfully."
6. Reload chat page — all messages still present
