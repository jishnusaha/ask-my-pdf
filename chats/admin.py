from django.contrib import admin

from .models import Chat, ChatMessage, DocumentChunk

admin.site.register(Chat)
admin.site.register(ChatMessage)
admin.site.register(DocumentChunk)
