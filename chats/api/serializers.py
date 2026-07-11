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
