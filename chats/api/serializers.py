import os

from rest_framework import serializers

from chats.models import Chat, ChatMessage, DocumentChunk
from chats.services.rag.parser import render_answer, extract_text_by_page
from chats.services.rag.embedder import chunk_pages, embed_chunks
from chats.services.rag.ingestor import ingest_chunks
from chats.services.rag.key_terms import extract_key_terms


class ChatSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chat
        fields = ["id", "title", "created_at"]
        read_only_fields = ["id", "created_at"]


class ChatMessageSerializer(serializers.ModelSerializer):
    content_html = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ["id", "role", "mode", "content", "content_html", "created_at"]

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
        # LLM call, kept outside ingest_chunks' transaction; returns [] on failure
        key_terms = extract_key_terms(title, [p.text for p in pages[:3]])
        return ingest_chunks(title, embedded_chunks, key_terms)


class TranscribeSerializer(serializers.Serializer):
    MAX_AUDIO_BYTES = 5 * 1024 * 1024
    # checked by extension, not content_type (browsers send e.g.
    # "audio/webm;codecs=opus"); no ogg: the OpenAI fallback doesn't list it
    ALLOWED_EXTENSIONS = ("webm", "mp4", "m4a", "wav")

    audio = serializers.FileField()

    def validate_audio(self, audio):
        if audio.size > self.MAX_AUDIO_BYTES:
            raise serializers.ValidationError("Audio file must be 5 MB or smaller.")
        if _extension(audio.name) not in self.ALLOWED_EXTENSIONS:
            raise serializers.ValidationError(
                "Unsupported audio format. Use webm, mp4, m4a or wav."
            )
        return audio

    def validate(self, attrs):
        # the provider detects the format from the filename; send a fixed name
        # with the validated extension instead of the client's filename
        attrs["filename"] = f"audio.{_extension(attrs['audio'].name)}"
        return attrs


def _extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lstrip(".").lower()


class MessageCreateSerializer(serializers.Serializer):
    question = serializers.CharField(min_length=1, trim_whitespace=True)
    mode = serializers.ChoiceField(
        choices=ChatMessage.Mode.choices, default=ChatMessage.Mode.TEXT
    )
