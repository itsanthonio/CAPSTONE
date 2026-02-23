from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.urls import reverse_lazy
from django.views.generic import CreateView
from django.utils import timezone
from datetime import timedelta
from django.contrib import messages
from .forms import CustomUserCreationForm, RoleBasedLoginForm
from apps.accounts.models import UserProfile


@login_required
def dashboard_router(request):
    """Gatekeeper view that redirects based on user role"""
    try:
        # Check if the user has a profile
        role = request.user.profile.role
        print(f"DEBUG: User {request.user.username} has role: {role}")
    except Exception as e:
        print(f"DEBUG: Profile missing for {request.user.username}: {e}")
        # Create profile if missing
        UserProfile.objects.create(user=request.user, role=UserProfile.Role.ADMIN)
        return redirect('/dashboard/home/')
    
    if role == UserProfile.Role.ADMIN:
        print(f"DEBUG: Redirecting admin {request.user.username} to admin dashboard")
        return redirect('/dashboard/home/')
    elif role == UserProfile.Role.INSPECTOR:
        print(f"DEBUG: Redirecting inspector {request.user.username} to inspector dashboard")
        return redirect('/dashboard/inspector/')
    else:
        print(f"DEBUG: Redirecting non-inspector {request.user.username} to admin dashboard")
        return redirect('/dashboard/home/')


def is_admin(user):
    """Check if user has admin role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.ADMIN


def is_inspector(user):
    """Check if user has inspector role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.INSPECTOR


class CustomLoginView(LoginView):
    form_class = AuthenticationForm
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # Strip the next parameter completely to force role-based redirect
        if 'next' in request.GET:
            print(f"DEBUG: Removing next parameter: {request.GET['next']}")
            # Create a mutable copy and remove next
            request.GET = request.GET.copy()
            del request.GET['next']
        return super().dispatch(request, *args, **kwargs)

    def get_redirect_url(self):
        """Override to completely ignore next parameter"""
        return self.get_success_url()

    def get_success_url(self):
        # Completely ignore any 'next' parameter and use role-based redirect
        print(f"DEBUG CustomLoginView: get_success_url called for {self.request.user}")
        if self.request.user.is_authenticated:
            try:
                profile = self.request.user.profile
                print(f"DEBUG CustomLoginView: role={profile.role}")
                if profile.role == UserProfile.Role.INSPECTOR:
                    print(f"DEBUG CustomLoginView: Redirecting to inspector")
                    return '/dashboard/inspector/'
                else:
                    print(f"DEBUG dashboard_home: Role is {profile.role}, showing admin dashboard")
                    return '/dashboard/home/'
            except UserProfile.DoesNotExist:
                # Create profile if missing - default to ADMIN
                print(f"DEBUG CustomLoginView: No profile, creating ADMIN")
                UserProfile.objects.create(user=self.request.user, role=UserProfile.Role.ADMIN)
                return '/dashboard/home/'
        print(f"DEBUG CustomLoginView: User not authenticated, redirecting to home")
        return '/dashboard/home/'

    def form_valid(self, form):
        # Use Django's built-in login logic
        return super().form_valid(form)


class SignUpView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'registration/signup.html'
    success_url = '/dashboard/home/'

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        
        # Create UserProfile with selected role
        role = form.cleaned_data.get('role', UserProfile.Role.ADMIN)
        organization = form.cleaned_data.get('organization', UserProfile.Organization.OTHER)
        phone_number = form.cleaned_data.get('phone_number', '')
        
        UserProfile.objects.update_or_create(
            user=self.object,
            defaults={
                'role': role,
                'organization': organization,
                'phone_number': phone_number
            }
        )
        
        return response


class CustomLogoutView(LogoutView):
    next_page = '/accounts/login/'


def _get_dashboard_stats():
    """Query real stats from DetectedSite, Alert, and ModelRun."""
    try:
        from apps.detections.models import DetectedSite, Alert, ModelRun
        from apps.jobs.models import Job
        from django.db.models import Count, Avg, Sum, Q
        from django.db.models.functions import TruncDate

        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)
        sixty_days_ago  = now - timedelta(days=60)
        seven_days_ago  = now - timedelta(days=7)
        fourteen_days_ago = now - timedelta(days=14)

        # --- Detected sites ---
        total_sites    = DetectedSite.objects.count()
        illegal_sites  = DetectedSite.objects.filter(legal_status='illegal').count()
        sites_this_week = DetectedSite.objects.filter(
            detection_date__gte=seven_days_ago.date()
        ).count()
        sites_last_week = DetectedSite.objects.filter(
            detection_date__gte=fourteen_days_ago.date(),
            detection_date__lt=seven_days_ago.date()
        ).count()

        # --- Alerts ---
        open_alerts    = Alert.objects.filter(status='open').count()
        critical_alerts = Alert.objects.filter(status='open', severity='critical').count()
        high_alerts    = Alert.objects.filter(status='open', severity='high').count()
        alerts_this_month = Alert.objects.filter(created_at__gte=thirty_days_ago).count()
        alerts_last_month = Alert.objects.filter(
            created_at__gte=sixty_days_ago,
            created_at__lt=thirty_days_ago
        ).count()

        alerts_change_pct = 0
        if alerts_last_month > 0:
            alerts_change_pct = round(
                ((alerts_this_month - alerts_last_month) / alerts_last_month) * 100
            )

        # --- High-risk zones: sites with recurrence > 1 or critical alerts ---
        high_risk = DetectedSite.objects.filter(
            Q(recurrence_count__gt=1) | Q(alerts__severity='critical')
        ).distinct().count()

        # --- Total area ---
        area_result = DetectedSite.objects.filter(
            legal_status='illegal'
        ).aggregate(total=Sum('area_hectares'))
        total_area_ha = round(area_result['total'] or 0, 1)

        # --- Jobs ---
        total_jobs     = Job.objects.count()
        completed_jobs = Job.objects.filter(status='completed').count()
        failed_jobs    = Job.objects.filter(status='failed').count()

        # --- Detection trend: daily counts for last 30 days (Python-level grouping) ---
        from collections import defaultdict
        illegal_by_day = defaultdict(int)
        legal_by_day   = defaultdict(int)
        recent_all = DetectedSite.objects.filter(
            created_at__gte=thirty_days_ago
        ).values('legal_status', 'created_at')
        for site in recent_all:
            day = site['created_at'].date()
            if site['legal_status'] == 'illegal':
                illegal_by_day[day] += 1
            else:
                legal_by_day[day] += 1
        trend_labels, trend_illegal, trend_legal = [], [], []
        for i in range(29, -1, -1):
            d = (now - timedelta(days=i)).date()
            trend_labels.append(d.strftime('%d %b'))
            trend_illegal.append(illegal_by_day.get(d, 0))
            trend_legal.append(legal_by_day.get(d, 0))

        # --- Top regions by detection count (all-time) ---
        top_regions = list(
            DetectedSite.objects
            .filter(region__isnull=False)
            .values('region__name')
            .annotate(
                total=Count('id'),
                illegal=Count('id', filter=Q(legal_status='illegal')),
            )
            .order_by('-total')[:6]
        )
        if top_regions:
            max_total = top_regions[0]['total']
            for r in top_regions:
                r['illegal_pct'] = round((r['illegal'] / r['total']) * 100) if r['total'] > 0 else 0
                r['bar_pct'] = round((r['total'] / max_total) * 100) if max_total > 0 else 0

        # --- Recent sites for activity feed (last 5 by scan time) ---
        recent_sites = list(
            DetectedSite.objects.select_related('region')
            .order_by('-created_at')[:5]
            .values(
                'id', 'detection_date', 'created_at', 'legal_status',
                'confidence_score', 'area_hectares',
                'region__name', 'recurrence_count'
            )
        )
        for s in recent_sites:
            s['confidence_pct'] = round(s['confidence_score'] * 100, 1)
            s['id'] = str(s['id'])

        return {
            'total_detected_sites': total_sites,
            'illegal_sites': illegal_sites,
            'open_alerts': open_alerts,
            'critical_alerts': critical_alerts,
            'high_alerts': high_alerts,
            'high_risk_zones': high_risk,
            'alerts_this_month': alerts_this_month,
            'total_area_ha': total_area_ha,
            'total_jobs': total_jobs,
            'completed_jobs': completed_jobs,
            'failed_jobs': failed_jobs,
            'recent_sites': recent_sites,
            'top_regions': top_regions,
            'trend_labels': trend_labels,
            'trend_illegal': trend_illegal,
            'trend_legal': trend_legal,
            'trends': {
                'sites_change': sites_this_week,
                'alerts_change': alerts_change_pct,
            },
            'has_data': total_sites > 0,
        }
    except Exception:
        # Models not yet migrated or DB empty — return safe defaults
        return {
            'total_detected_sites': 0,
            'illegal_sites': 0,
            'open_alerts': 0,
            'critical_alerts': 0,
            'high_alerts': 0,
            'high_risk_zones': 0,
            'alerts_this_month': 0,
            'total_area_ha': 0,
            'total_jobs': 0,
            'completed_jobs': 0,
            'failed_jobs': 0,
            'recent_sites': [],
            'top_regions': [],
            'trend_labels': [],
            'trend_illegal': [],
            'trend_legal': [],
            'trends': {'sites_change': 0, 'alerts_change': 0},
            'has_data': False,
        }


def dashboard_home(request):
    """Admin-only dashboard home with automatic redirect for inspectors"""
    print(f"DEBUG dashboard_home: user={request.user}, authenticated={request.user.is_authenticated}")
    
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            print(f"DEBUG dashboard_home: profile.role={profile.role}")
            if profile.role == UserProfile.Role.INSPECTOR:
                print(f"DEBUG dashboard_home: Inspector detected, redirecting to inspector dashboard")
                return redirect('/dashboard/inspector/')
        except UserProfile.DoesNotExist:
            print(f"DEBUG dashboard_home: No profile, creating ADMIN profile")
            UserProfile.objects.create(user=request.user, role=UserProfile.Role.ADMIN)
    
    # Any non-inspector gets admin dashboard
    try:
        if request.user.profile.role == UserProfile.Role.INSPECTOR:
            print(f"DEBUG dashboard_home: Inspector redirecting to inspector dashboard")
            return redirect('/dashboard/inspector/')
    except UserProfile.DoesNotExist:
        pass
    
    print(f"DEBUG dashboard_home: Rendering admin dashboard")
    try:
        stats = _get_dashboard_stats()
    except Exception as e:
        # Fallback stats if there's an error
        stats = {
            'total_sites': 0,
            'illegal_sites': 0,
            'sites_this_week': 0,
            'total_alerts': 0,
            'alerts_this_week': 0,
            'pending_assignments': 0,
            'completed_assignments': 0,
            'avg_processing_time': 0,
            'top_regions': [],
            'trend_labels': [],
            'trend_illegal': [],
            'trend_legal': [],
            'trends': {'sites_change': 0, 'alerts_change': 0},
            'has_data': False,
        }
    
    return render(request, 'dashboard/dashboard.html', {'stats': stats})


@user_passes_test(is_admin)
def dashboard_alerts(request):
    """Admin-only alerts view"""
    return render(request, 'dashboard/alerts.html')


@user_passes_test(is_admin)
def dashboard_model_insights(request):
    """Admin-only model insights view"""
    return render(request, 'dashboard/model_insights.html')


@user_passes_test(is_admin)
def dashboard_settings(request):
    """Admin-only settings view"""
    return render(request, 'dashboard/settings.html')


def is_inspector_or_admin(user):
    """Check if user has inspector or admin role"""
    if not user.is_authenticated:
        return False
    if not hasattr(user, 'profile'):
        return False
    return user.profile.role in [UserProfile.Role.INSPECTOR, UserProfile.Role.ADMIN]


@user_passes_test(is_inspector_or_admin)
def inspector_dashboard(request):
    """Inspector-specific dashboard"""
    try:
        from apps.accounts.models import InspectorAssignment
        from apps.detections.models import Alert
        
        # Get inspector's assignments
        assignments = InspectorAssignment.objects.filter(
            inspector=request.user.profile
        ).order_by('-created_at')
        
        return render(request, 'dashboard/inspector.html', {
            'assignments': assignments
        })
    except Exception as e:
        return render(request, 'dashboard/inspector.html', {
            'assignments': [],
            'error': str(e)
        })