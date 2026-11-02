from django.urls import path

from chats.api.views import (
    ChatListCreateAPIView,
    MessageListAPIView,
    MessageStreamAPIView,
    ChatReuploadAPIView,
    ChunkListAPIView,
)

urlpatterns = [
    path("", ChatListCreateAPIView.as_view(), name="api_chat_list_create"),
    path("<int:pk>/messages/", MessageListAPIView.as_view(), name="api_message_list"),
    path(
        "<int:pk>/messages/stream/",
        MessageStreamAPIView.as_view(),
        name="api_message_stream",
    ),
    path("<int:pk>/reupload/", ChatReuploadAPIView.as_view(), name="api_chat_reupload"),
    path("<int:pk>/chunks/", ChunkListAPIView.as_view(), name="api_chat_chunks"),
]
