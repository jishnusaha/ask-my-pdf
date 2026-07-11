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
    ChatSerializer,
    ChatCreateSerializer,
    ChatMessageSerializer,
    MessageCreateSerializer,
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
