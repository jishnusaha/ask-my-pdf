from django.db import models
from pgvector.django import VectorField


class Chat(models.Model):
    title = models.CharField(max_length=255)  # PDF filename or user-given title
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class DocumentChunk(models.Model):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="chunks")
    content = models.TextField()
    page_number = models.IntegerField()
    chunk_index = models.IntegerField()
    content_hash = models.CharField(max_length=64, blank=True, default="")
    embedding = VectorField(dimensions=1536)

    class Meta:
        ordering = ["chunk_index"]

    def __str__(self):
        return f"{self.chat.title} — chunk {self.chunk_index}"


class ChatMessage(models.Model):
    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")
    role = models.CharField(max_length=10, choices=Role.choices)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"{self.chat.title} — {self.role}"
