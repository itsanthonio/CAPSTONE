from django.contrib import admin
from .models import UserProfile


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'organization', 'receive_email_alerts', 'created_at')
    list_filter = ('role', 'organization')
    search_fields = ('user__username', 'user__email')