from django.shortcuts import redirect
from django.urls import resolve
from django.contrib import messages
from apps.accounts.models import UserProfile

class RoleBasedAccessMiddleware:
    """
    Middleware to enforce role-based access control.
    Restricts access to certain URLs based on user roles.
    """
    
    def __init__(self, get_response):
        self.get_response = get_response
        
        # Define URL patterns and their required roles
        self.admin_only_paths = [
            '/analysis/',
            '/dashboard/alerts/',
            '/dashboard/model-insights/',
            '/dashboard/users/',
            '/uploads/',
            '/jobs/',
            '/inference/',
            '/gee/',
            '/preprocessing/',
            '/postprocessing/',
            '/results/',
            '/admin/',
        ]
        
        self.inspector_paths = [
            '/dashboard/inspector/',
            '/accounts/api/assignments/',
        ]
        
        # Public paths that don't require role checks
        self.public_paths = [
            '/accounts/login/',
            '/accounts/logout/',
            '/accounts/signup/',
            '/static/',
            '/media/',
            '/.well-known/',
        ]
        
        # API endpoints that bypass middleware checks
        self.api_paths = [
            '/dashboard/assignment/',  # Assignment status updates
        ]
    
    def __call__(self, request):
        # Skip role checks for unauthenticated users
        if not request.user.is_authenticated:
            return self.get_response(request)
        
        # Skip role checks for public paths
        path = request.path
        for public_path in self.public_paths:
            if path.startswith(public_path):
                return self.get_response(request)
        
        # Skip role checks for API endpoints (they handle their own auth)
        for api_path in self.api_paths:
            if path.startswith(api_path):
                return self.get_response(request)
        
        # Get user role - default to INSPECTOR if no profile (safest default)
        try:
            user_role = request.user.profile.role
        except (UserProfile.DoesNotExist, AttributeError):
            UserProfile.objects.create(user=request.user, role=UserProfile.Role.INSPECTOR)
            user_role = UserProfile.Role.INSPECTOR
        
        # Check if user is trying to access admin-only area
        for admin_path in self.admin_only_paths:
            if path.startswith(admin_path):
                # Only inspectors are blocked from admin areas
                if user_role == UserProfile.Role.INSPECTOR:
                    messages.error(request, "You don't have permission to access this area. Admin access required.")
                    return redirect('/dashboard/inspector/')
                break
        
        return self.get_response(request)
