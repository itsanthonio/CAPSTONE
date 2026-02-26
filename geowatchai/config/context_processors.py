from django.conf import settings as django_settings


def app_settings(request):
    """Inject app-level settings into every template context."""
    return {
        'settings': {
            'APP_NAME': getattr(django_settings, 'APP_NAME', 'GalamseyWatch AI'),
            'ENVIRONMENT': getattr(django_settings, 'ENVIRONMENT', ''),
            'MAP_DEFAULT_CENTER': getattr(django_settings, 'MAP_DEFAULT_CENTER', [-1.6244, 6.6885]),
            'MAP_DEFAULT_ZOOM': getattr(django_settings, 'MAP_DEFAULT_ZOOM', 7),
        }
    }
