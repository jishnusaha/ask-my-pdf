import json
import logging

from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.http import StreamingHttpResponse
from django.shortcuts import get_object_or_404

from chats.models import Chat, ChatMessage
from chats.services.rag.parser import extract_text_by_page
from chats.services.rag.embedder import chunk_pages
from chats.services.rag.ingestor import reingest_chunks
from chats.services.rag.key_terms import extract_key_terms
from chats.services.rag.pipeline import ask_stream

from .serializers import (
    ChatSerializer,
    ChatCreateSerializer,
    ChatMessageSerializer,
    MessageCreateSerializer,
    DocumentChunkSerializer,
)

logger = logging.getLogger(__name__)


def _sse(event: str, data) -> str:
    # json.dumps escapes newlines, so each event's data stays on a single line
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class ChatListCreateAPIView(generics.ListCreateAPIView):
    queryset = Chat.objects.all().order_by("-created_at")

    def get_serializer_class(self):
        return ChatCreateSerializer if self.request.method == "POST" else ChatSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        chat = serializer.save()
        return Response(ChatSerializer(chat).data, status=status.HTTP_201_CREATED)


class MessageListAPIView(generics.ListAPIView):
    serializer_class = ChatMessageSerializer

    def get_queryset(self):
        chat = get_object_or_404(Chat, pk=self.kwargs["pk"])
        return chat.messages.all()


class MessageStreamAPIView(APIView):
    def perform_content_negotiation(self, request, force=False):
        # SSE clients send Accept: text/event-stream, which matches no DRF renderer and
        # would 406 before the view runs; forcing falls back to JSON so pre-stream errors still render
        return super().perform_content_negotiation(request, force=True)

    def post(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk)
        serializer = MessageCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        question = serializer.validated_data["question"]
        # ChoiceField returns a plain str when mode is sent and the enum when defaulted
        mode = ChatMessage.Mode(serializer.validated_data["mode"])

        response = StreamingHttpResponse(
            self._event_stream(chat, question, mode), content_type="text/event-stream"
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # stops nginx from buffering the stream
        return response

    def _event_stream(self, chat, question, mode: ChatMessage.Mode):
        last_id = (
            chat.messages.order_by("-id").values_list("id", flat=True).first() or 0
        )
        try:
            for delta in ask_stream(chat.pk, question, list(chat.messages.all()), mode):
                if delta:
                    yield _sse("token", {"delta": delta})
        except Exception:
            logger.exception("Streaming answer failed for chat %s", chat.pk)
            yield _sse(
                "error",
                {"detail": "The answer could not be generated. Please try again."},
            )
            return

        new_messages = chat.messages.filter(id__gt=last_id).order_by("id")
        yield _sse("done", ChatMessageSerializer(new_messages, many=True).data)


class ChatReuploadAPIView(APIView):
    def post(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk)
        file = request.FILES.get("pdf")
        if not file:
            return Response(
                {"error": "No PDF file provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        pages = extract_text_by_page(file)
        chunks = chunk_pages(pages)
        reingest_chunks(chat, chunks)

        # the PDF changed, so its terms must too; [] on failure (old terms would be wrong)
        chat.key_terms = extract_key_terms(chat.title, [p.text for p in pages[:3]])
        chat.save(update_fields=["key_terms"])
        return Response(ChatSerializer(chat).data)


class ChunkListAPIView(generics.ListAPIView):
    serializer_class = DocumentChunkSerializer

    def get_queryset(self):
        chat = get_object_or_404(Chat, pk=self.kwargs["pk"])
        return chat.chunks.all()
