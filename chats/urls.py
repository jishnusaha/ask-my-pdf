from django.urls import path
from chats.views import ChatDetailView, ChatListCreateView

urlpatterns = [
    path("", ChatListCreateView.as_view(), name="chat_list_create"),
    path("<int:pk>/", ChatDetailView.as_view(), name="chat_detail"),
]
