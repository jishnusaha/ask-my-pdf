from django.shortcuts import render, get_object_or_404
from django.views import View

from chats.models import Chat


def sidebar_context():
    """shared context for sidebar — used in every view"""
    return {"all_chats": Chat.objects.all().order_by("-created_at")}


class ChatListView(View):
    def get(self, request):
        context = sidebar_context()
        context["active_chat_id"] = None
        return render(request, "chats/upload.html", context)


class ChatDetailView(View):
    def get(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk)
        context = sidebar_context()
        context["chat"] = chat
        context["messages"] = chat.messages.all()
        context["active_chat_id"] = chat.id
        return render(request, "chats/chat.html", context)
