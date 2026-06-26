from django.shortcuts import render, redirect, get_object_or_404
from django.views import View
from chats.models import Chat
from chats.services.rag.parser import extract_text_by_page
from chats.services.rag.embedder import chunk_pages, embed_chunks
from chats.services.rag.ingestor import ingest_chunks
from chats.services.rag.pipeline import ask


def sidebar_context():
    """shared context for sidebar — used in every view"""
    return {"all_chats": Chat.objects.all().order_by("-created_at")}


class ChatListCreateView(View):
    def get(self, request):
        context = sidebar_context()
        context["active_chat_id"] = None
        return render(request, "chats/upload.html", context)

    def post(self, request):
        file = request.FILES.get("pdf")
        title = request.POST.get("title", "").strip() or file.name

        pages = extract_text_by_page(file)
        chunks = chunk_pages(pages)
        embedded_chunks = embed_chunks(chunks)
        chat = ingest_chunks(title, embedded_chunks)

        return redirect("chat_detail", pk=chat.id)


class ChatDetailView(View):
    def get(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk)
        context = sidebar_context()
        context["chat"] = chat
        context["messages"] = chat.messages.all()
        context["active_chat_id"] = chat.id
        return render(request, "chats/chat.html", context)

    def post(self, request, pk):
        chat = get_object_or_404(Chat, pk=pk)
        question = request.POST.get("question", "").strip()

        if not question:
            return redirect("chat_detail", pk=pk)

        ask(pk, question, list(chat.messages.all()))

        return redirect("chat_detail", pk=pk)
