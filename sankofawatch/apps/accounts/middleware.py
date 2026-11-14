"""Tracks when each logged-in user was last active (powers the Online/Offline badges)."""
from django.core.cache import cache
from django.utils import timezone

# Write to the DB at most once per this many seconds per user
TOUCH_INTERVAL = 60
# A user counts as "online" if seen within this many seconds
ONLINE_WINDOW = 5 * 60


class LastSeenMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        user = getattr(request, 'user', None)
        if user is not None and user.is_authenticated:
            key = f'lastseen:{user.pk}'
            try:
                if not cache.get(key):
                    from .models import UserProfile
                    UserProfile.objects.filter(user_id=user.pk).update(last_seen=timezone.now())
                    cache.set(key, 1, TOUCH_INTERVAL)
            except Exception:
                pass  # never break a request over presence tracking
        return response
