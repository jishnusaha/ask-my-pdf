from django.urls import path, include

from chats.views import ChatListView, ChatDetailView

urlpatterns = [
    path("", ChatListView.as_view(), name="chat_list"),
    path("<int:pk>/", ChatDetailView.as_view(), name="chat_detail"),
    path("api/", include("chats.api.urls")),
]
