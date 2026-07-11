from django.urls import path
from chats.views import ChatDetailView, ChatListCreateView, ChatChunksView, ChatReuploadView

urlpatterns = [
    path("", ChatListCreateView.as_view(), name="chat_list_create"),
    path("<int:pk>/", ChatDetailView.as_view(), name="chat_detail"),
    path("<int:pk>/chunks/", ChatChunksView.as_view(), name="chat_chunks"),
    path("<int:pk>/reupload/", ChatReuploadView.as_view(), name="chat_reupload"),
]
