from django.urls import path

from chats.api.views import (
    ChatListCreateAPIView,
    MessageListCreateAPIView,
    ChatReuploadAPIView,
    ChunkListAPIView,
)

urlpatterns = [
    path("", ChatListCreateAPIView.as_view(), name="api_chat_list_create"),
    path("<int:pk>/messages/", MessageListCreateAPIView.as_view(), name="api_message_list_create"),
    path("<int:pk>/reupload/", ChatReuploadAPIView.as_view(), name="api_chat_reupload"),
    path("<int:pk>/chunks/", ChunkListAPIView.as_view(), name="api_chat_chunks"),
]
