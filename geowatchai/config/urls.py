"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import mimetypes
import os
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.decorators import login_required
from django.http import FileResponse, Http404, HttpResponseForbidden
from apps.dashboard.views import (
    SignUpView, CustomLoginView, CustomLogoutView,
    password_reset_request, password_reset_pin_entry, password_reset_new_password,
    impact_page,
)


def public_site_images(request, path):
    """Serve ML site imagery publicly — no auth required."""
    media_root = os.path.realpath(settings.MEDIA_ROOT)
    full_path  = os.path.realpath(os.path.join(media_root, 'site_images', path))
    if not full_path.startswith(os.path.join(media_root, 'site_images') + os.sep):
        return HttpResponseForbidden()
    if not os.path.isfile(full_path):
        raise Http404
    content_type, _ = mimetypes.guess_type(full_path)
    return FileResponse(open(full_path, 'rb'), content_type=content_type or 'application/octet-stream')


@login_required
def protected_media(request, path):
    """Serve media files only to authenticated users.
    Prevents unauthenticated access to evidence photos and other uploads.
    Evidence photos (inspections/<assignment_id>/...) are only served to the
    owning inspector, system admins, and agency admins."""
    media_root = os.path.realpath(settings.MEDIA_ROOT)
    full_path  = os.path.realpath(os.path.join(media_root, path))
    # Block path traversal (e.g. ../../etc/passwd)
    if not full_path.startswith(media_root + os.sep) and full_path != media_root:
        return HttpResponseForbidden()
    if not os.path.isfile(full_path):
        raise Http404

    # Ownership check for evidence photos
    parts = path.replace('\\', '/').split('/')
    if parts[0] == 'inspections' and len(parts) >= 2:
        assignment_id = parts[1]
        try:
            from apps.accounts.models import InspectorAssignment, UserProfile
            profile = request.user.profile
            if profile.role not in ('system_admin', 'agency_admin'):
                # Inspector may only access their own assignment's photos
                InspectorAssignment.objects.get(id=assignment_id, inspector=profile)
        except Exception:
            return HttpResponseForbidden()

    content_type, _ = mimetypes.guess_type(full_path)
    return FileResponse(
        open(full_path, 'rb'),
        content_type=content_type or 'application/octet-stream',
    )

urlpatterns = [
    path('admin/', admin.site.urls),
    # Custom auth URLs (must be before django.contrib.auth.urls)
    path('accounts/login/', CustomLoginView.as_view(), name='login'),
    path('accounts/logout/', CustomLogoutView.as_view(), name='logout'),
    path('accounts/signup/', SignUpView.as_view(), name='signup'),
    # PIN-based password reset (overrides Django's built-in link-based reset)
    path('accounts/password_reset/', password_reset_request, name='password_reset'),
    path('accounts/password_reset/pin/', password_reset_pin_entry, name='password_reset_pin_entry'),
    path('accounts/password_reset/new/', password_reset_new_password, name='password_reset_new_password'),
    # Django auth URLs (password change, etc.)
    path('accounts/', include('django.contrib.auth.urls')),
    path('', impact_page, name='root'),
    path('dashboard/', include('apps.dashboard.urls')),
    path('analysis/', include('analysis.urls')),
    path('uploads/', include('uploads.urls')),
    # HLS Pipeline apps
    path('jobs/', include('apps.jobs.urls')),
    path('inference/', include('apps.inference.urls')),
    path('gee/', include('apps.gee.urls')),
    path('preprocessing/', include('apps.preprocessing.urls')),
    path('postprocessing/', include('apps.postprocessing.urls')),
    path('results/', include('apps.results.urls')),
    # API endpoints
    path('api/', include('apps.api.urls')),
    path('api/notifications/', include('apps.notifications.urls')),
    path('accounts/api/', include('apps.accounts.urls')),
    path('scanning/', include('apps.scanning.urls')),
    # Public ML site imagery (no auth required)
    path('media/site_images/<path:path>', public_site_images, name='public_site_images'),
    # Authenticated media serving (replaces unauthenticated static() in DEBUG too)
    path('media/<path:path>', protected_media, name='protected_media'),
]

# Serve static files during development from source directory (media served via protected_media view above)
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATICFILES_DIRS[0])
