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
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.views.generic import RedirectView
from apps.dashboard.views import SignUpView, CustomLoginView, CustomLogoutView

def root_view(request):
    """Root view that redirects based on user role"""
    if request.user.is_authenticated:
        try:
            from apps.accounts.models import UserProfile
            profile = request.user.profile
            if profile.role == UserProfile.Role.INSPECTOR:
                from django.shortcuts import redirect
                return redirect('/dashboard/inspector/')
        except UserProfile.DoesNotExist:
            pass
    # Default to dashboard home for admins or non-authenticated users
    from django.shortcuts import redirect
    return redirect('/dashboard/home/')

urlpatterns = [
    path('admin/', admin.site.urls),
    # Custom auth URLs (must be before django.contrib.auth.urls)
    path('accounts/login/', CustomLoginView.as_view(), name='login'),
    path('accounts/logout/', CustomLogoutView.as_view(), name='logout'),
    path('accounts/signup/', SignUpView.as_view(), name='signup'),
    # Django auth URLs (for password reset, etc.)
    path('accounts/', include('django.contrib.auth.urls')),
    path('', root_view, name='root'),
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
    path('accounts/api/', include('apps.accounts.urls')),
    # Test page
    path('test-map/', lambda request: render(request, 'test_map.html'), name='test_map'),
]

# Serve static and media files during development
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
