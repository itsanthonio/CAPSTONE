from rest_framework.permissions import BasePermission


class IsSystemAdmin(BasePermission):
    """Grants access only to System Administrators."""
    message = 'System Administrator access required.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            return request.user.profile.role == 'system_admin'
        except Exception:
            return False


class IsAgencyAdmin(BasePermission):
    """Grants access only to Agency Administrators."""
    message = 'Agency Administrator access required.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            return request.user.profile.role == 'agency_admin'
        except Exception:
            return False


class IsAdminRole(BasePermission):
    """Grants access to either System Admin or Agency Admin."""
    message = 'Administrator access required.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        try:
            return request.user.profile.role in ('system_admin', 'agency_admin')
        except Exception:
            return False


class OrgScopedMixin:
    """
    Mixin for DRF ViewSets. Scopes querysets to the requesting user's organisation
    when the user is an Agency Admin. System Admins see everything.

    Subclasses must set `org_field` to the ORM lookup path to Job.organisation, e.g.:
        - Job:          'organisation'
        - DetectedSite: 'job__organisation'
        - Result:       'job__organisation'
        - Alert:        'detected_site__job__organisation'

    Automated jobs (organisation=None, source='automated') are always visible to
    all agency admins because they benefit all organisations equally.
    """
    org_field: str = ''

    def get_queryset(self):
        qs = super().get_queryset()
        user = self.request.user
        if not user.is_authenticated:
            return qs.none()
        try:
            role = user.profile.role
        except Exception:
            return qs.none()
        if role == 'agency_admin':
            org = getattr(user.profile, 'organisation', None)
            if org is None:
                return qs.none()
            from django.db.models import Q
            # Derive the field prefix (e.g. 'job__organisation' → prefix='job__')
            prefix = self.org_field.rsplit('organisation', 1)[0]
            return qs.filter(
                Q(**{self.org_field: org}) |
                Q(**{f'{prefix}organisation__isnull': True, f'{prefix}source': 'automated'})
            )
        return qs  # system_admin and inspector see full set


def is_system_admin(user):
    """Callable for use with @user_passes_test."""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == 'system_admin'


def is_agency_admin(user):
    """Callable for use with @user_passes_test."""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == 'agency_admin'


def is_any_admin(user):
    """Callable for use with @user_passes_test. True for system_admin or agency_admin."""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role in ('system_admin', 'agency_admin')
