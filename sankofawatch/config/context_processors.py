from django.conf import settings as django_settings


def app_settings(request):
    """Inject app-level settings into every template context."""
    return {
        'settings': {
            'APP_NAME': getattr(django_settings, 'APP_NAME', 'SankofaWatch'),
            'ENVIRONMENT': getattr(django_settings, 'ENVIRONMENT', ''),
            'MAP_DEFAULT_CENTER': getattr(django_settings, 'MAP_DEFAULT_CENTER', [-1.6244, 6.6885]),
            'MAP_DEFAULT_ZOOM': getattr(django_settings, 'MAP_DEFAULT_ZOOM', 7),
            # Session idle timeout in seconds — consumed by the JS idle watcher
            'SESSION_IDLE_TIMEOUT': getattr(django_settings, 'SESSION_IDLE_TIMEOUT', 1800),
        }
    }
