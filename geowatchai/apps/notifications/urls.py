from django.urls import path
from . import views

urlpatterns = [
    path('',           views.notification_list,      name='notification-list'),
    path('mark-read/', views.notification_mark_read, name='notification-mark-read'),
]
