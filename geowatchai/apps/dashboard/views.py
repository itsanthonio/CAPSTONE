import logging
from django.shortcuts import render, redirect, get_object_or_404

logger = logging.getLogger(__name__)
from django.contrib.auth import login, authenticate, get_user_model
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse_lazy, reverse
from django.views.generic import CreateView
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.http import JsonResponse
from django.conf import settings as django_settings
from django.core.mail import send_mail
from django.core.cache import cache
from django.template.loader import render_to_string
from datetime import timedelta, date
from django.db.models import Q
from django.contrib import messages
import os
import random
import threading
from .forms import CustomUserCreationForm, RoleBasedLoginForm
from apps.accounts.models import UserProfile

User = get_user_model()


def impact_page(request):
    """Public landing/impact page — no login required."""
    try:
        from apps.detections.models import DetectedSite, Alert
        from apps.jobs.models import Job
        from apps.scanning.models import ScanTile
        from django.db.models import Sum, Count

        total_area_ha = round(
            DetectedSite.objects.filter(legal_status='illegal')
            .aggregate(total=Sum('area_hectares'))['total'] or 0, 1
        )
        illegal_sites = DetectedSite.objects.filter(legal_status='illegal').count()
        total_detections = DetectedSite.objects.count()
        alerts_resolved = Alert.objects.filter(
            status__in=['resolved', 'dismissed']
        ).count()
        regions_covered = (
            DetectedSite.objects.filter(region__isnull=False)
            .values('region').distinct().count()
        )
        total_jobs = Job.objects.filter(status='completed').count()
        scan_tiles_total = ScanTile.objects.filter(is_active=True).count()
        scan_tiles_scanned = ScanTile.objects.filter(is_active=True, last_scanned_at__isnull=False).count()
        scan_coverage_pct = round(scan_tiles_scanned / scan_tiles_total * 100, 1) if scan_tiles_total else 0
        stats = {
            'total_area_ha': total_area_ha,
            'illegal_sites': illegal_sites,
            'total_detections': total_detections,
            'alerts_resolved': alerts_resolved,
            'regions_covered': regions_covered,
            'total_jobs': total_jobs,
            'scan_coverage_pct': scan_coverage_pct,
        }
    except Exception:
        stats = {
            'total_area_ha': 0,
            'illegal_sites': 0,
            'total_detections': 0,
            'alerts_resolved': 0,
            'regions_covered': 0,
            'total_jobs': 0,
            'scan_coverage_pct': 0,
        }
    return render(request, 'impact.html', {'stats': stats})


@login_required
def dashboard_router(request):
    """Gatekeeper view that redirects based on user role"""
    try:
        # Check if the user has a profile
        role = request.user.profile.role
        logger.debug(f": User {request.user.username} has role: {role}")
    except Exception as e:
        logger.debug(f": Profile missing for {request.user.username}: {e}")
        # Create profile if missing — default to INSPECTOR (safest fallback)
        UserProfile.objects.create(user=request.user, role=UserProfile.Role.INSPECTOR)
        return redirect('/dashboard/home/')
    
    if role == UserProfile.Role.ADMIN:
        logger.debug(f": Redirecting admin {request.user.username} to admin dashboard")
        return redirect('/dashboard/home/')
    elif role == UserProfile.Role.INSPECTOR:
        logger.debug(f": Redirecting inspector {request.user.username} to inspector dashboard")
        return redirect('/dashboard/inspector/')
    else:
        logger.debug(f": Redirecting non-inspector {request.user.username} to admin dashboard")
        return redirect('/dashboard/home/')


def is_admin(user):
    """Check if user has admin role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.ADMIN


def is_inspector(user):
    """Check if user has inspector role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.INSPECTOR


def is_inspector_or_admin(user):
    """Check if user has inspector or admin role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role in (
        UserProfile.Role.ADMIN, UserProfile.Role.INSPECTOR
    )


def _get_client_ip(request):
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR', '0.0.0.0')


_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_LOCKOUT_SECONDS = 600   # 10 minutes


class CustomLoginView(LoginView):
    form_class = AuthenticationForm
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # Rate-limit: block IPs that have exceeded the threshold
        if request.method == 'POST':
            ip = _get_client_ip(request)
            if cache.get(f'login_fail_{ip}', 0) >= _LOGIN_MAX_ATTEMPTS:
                form = AuthenticationForm(request)
                form.add_error(
                    None,
                    'Too many failed login attempts. Please wait 10 minutes before trying again.'
                )
                return self.render_to_response(self.get_context_data(form=form))
        # Strip the next parameter completely to force role-based redirect
        if 'next' in request.GET:
            logger.debug(f": Removing next parameter: {request.GET['next']}")
            # Create a mutable copy and remove next
            request.GET = request.GET.copy()
            del request.GET['next']
        return super().dispatch(request, *args, **kwargs)

    def get_redirect_url(self):
        """Override to completely ignore next parameter"""
        return self.get_success_url()

    def get_success_url(self):
        # Completely ignore any 'next' parameter and use role-based redirect
        logger.debug(f" CustomLoginView: get_success_url called for {self.request.user}")
        if self.request.user.is_authenticated:
            try:
                profile = self.request.user.profile
                logger.debug(f" CustomLoginView: role={profile.role}")
                if profile.role == UserProfile.Role.INSPECTOR:
                    logger.debug(f" CustomLoginView: Redirecting to inspector")
                    return '/dashboard/inspector/'
                else:
                    logger.debug(f" dashboard_home: Role is {profile.role}, showing admin dashboard")
                    return '/dashboard/home/'
            except UserProfile.DoesNotExist:
                # Create profile if missing — default to INSPECTOR (safest fallback)
                logger.debug(f" CustomLoginView: No profile, creating INSPECTOR")
                UserProfile.objects.create(user=self.request.user, role=UserProfile.Role.INSPECTOR)
                return '/dashboard/home/'
        logger.debug(f" CustomLoginView: User not authenticated, redirecting to home")
        return '/dashboard/home/'

    def form_valid(self, form):
        response = super().form_valid(form)   # calls login(), creates session
        if self.request.POST.get('remember_me'):
            self.request.session.set_expiry(1209600)   # 14 days
        else:
            self.request.session.set_expiry(0)         # ends on browser close
        return response

    def form_invalid(self, form):
        # Increment rate-limit counter for this IP
        ip = _get_client_ip(self.request)
        key = f'login_fail_{ip}'
        attempts = cache.get(key, 0) + 1
        cache.set(key, attempts, timeout=_LOGIN_LOCKOUT_SECONDS)
        remaining = _LOGIN_MAX_ATTEMPTS - attempts
        if remaining > 0:
            form.add_error(
                None,
                f'Invalid credentials. {remaining} attempt{"s" if remaining != 1 else ""} remaining before lockout.'
            )
        # Give a specific, helpful message when the account is unverified
        username = self.request.POST.get('username', '')
        try:
            u = User.objects.get(username=username)
            if not u.is_active:
                form.add_error(None, 'Please verify your email address before signing in.')
        except User.DoesNotExist:
            pass
        return super().form_invalid(form)


class SignUpView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'registration/signup.html'
    success_url = '/dashboard/home/'

    def form_valid(self, form):
        # Save user but keep inactive until email is confirmed
        user = form.save(commit=False)
        user.is_active = False
        user.save()

        # Self-registration always creates INSPECTOR accounts.
        # Admin accounts are created by existing admins only.
        role = UserProfile.Role.INSPECTOR
        organization = form.cleaned_data.get('organization', UserProfile.Organization.OTHER)
        phone_number = form.cleaned_data.get('phone_number', '')

        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                'role': role,
                'organization': organization,
                'phone_number': phone_number
            }
        )

        # Generate activation PIN and send in background
        pin = str(random.randint(100000, 999999))
        cache.set(f'activation_pin_{user.email.lower()}', {'pin': pin, 'user_pk': str(user.pk)}, timeout=86400)
        threading.Thread(
            target=_send_activation_pin_email, args=(user, pin), daemon=True
        ).start()
        return redirect(f'/dashboard/activation-pin/?email={user.email}')


class CustomLogoutView(LogoutView):
    next_page = '/'


def _get_dashboard_stats(velocity_weeks=8, trend_days=30):
    """Query real stats from DetectedSite, Alert, and ModelRun. Cached for 5 minutes.

    velocity_weeks: lookback window for the detection velocity sparkline (2–52).
    trend_days: period for the detection trend chart (7, 30, 90, or 365).
    """
    velocity_weeks = max(2, min(int(velocity_weeks), 52))
    trend_days = int(trend_days) if int(trend_days) in (7, 30, 90, 365) else 30
    cache_key = f'dashboard_stats_{velocity_weeks}_{trend_days}'
    cached = cache.get(cache_key)
    if cached is not None:
        return cached
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

        # --- Automated scan stats (lightweight) ---
        from apps.scanning.models import AutoScanConfig, ScanTile
        scan_config = AutoScanConfig.get()
        auto_jobs_today = Job.objects.filter(source='automated', created_at__date=now.date()).count()
        auto_detections_today = DetectedSite.objects.filter(
            job__source='automated', job__created_at__date=now.date()
        ).count()
        scan_tiles_total   = ScanTile.objects.filter(is_active=True).count()
        scan_tiles_scanned = ScanTile.objects.filter(is_active=True, last_scanned_at__isnull=False).count()
        scan_coverage_pct  = round(scan_tiles_scanned / scan_tiles_total * 100, 1) if scan_tiles_total else 0

        # --- Detection trend (configurable period: 7d / 30d / 90d / 12m) ---
        trend_start = now - timedelta(days=trend_days)
        trend_labels, trend_illegal, trend_legal = [], [], []
        if trend_days <= 90:
            from django.db.models.functions import TruncDate as _TruncDate
            trend_rows = (
                DetectedSite.objects
                .filter(created_at__gte=trend_start)
                .annotate(day=_TruncDate('created_at'))
                .values('day', 'legal_status')
                .annotate(cnt=Count('id'))
                .order_by('day')
            )
            illegal_by_day, legal_by_day = {}, {}
            for row in trend_rows:
                d = row['day']
                if row['legal_status'] == 'illegal':
                    illegal_by_day[d] = row['cnt']
                else:
                    legal_by_day[d] = row['cnt']
            for i in range(trend_days - 1, -1, -1):
                d = (now - timedelta(days=i)).date()
                trend_labels.append(d.strftime('%d %b'))
                trend_illegal.append(illegal_by_day.get(d, 0))
                trend_legal.append(legal_by_day.get(d, 0))
        else:
            # 365 days → monthly granularity
            from django.db.models.functions import TruncMonth as _TruncMonth
            monthly_rows = (
                DetectedSite.objects
                .filter(detection_date__gte=trend_start.date())
                .annotate(month=_TruncMonth('detection_date'))
                .values('month', 'legal_status')
                .annotate(cnt=Count('id'))
                .order_by('month')
            )
            illegal_by_month, legal_by_month = {}, {}
            for row in monthly_rows:
                m = row['month']
                if row['legal_status'] == 'illegal':
                    illegal_by_month[m] = row['cnt']
                else:
                    legal_by_month[m] = row['cnt']
            for i in range(11, -1, -1):
                mo = now.month - i
                y = now.year
                while mo <= 0:
                    mo += 12
                    y -= 1
                key = date(y, mo, 1)
                trend_labels.append(key.strftime('%b %Y'))
                trend_illegal.append(illegal_by_month.get(key, 0))
                trend_legal.append(legal_by_month.get(key, 0))

        # --- Top regions by detection count (all-time) ---
        top_regions = list(
            DetectedSite.objects
            .filter(region__isnull=False)
            .values('region__id', 'region__name')
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
            DetectedSite.objects.select_related('region', 'job__created_by')
            .order_by('-created_at')[:5]
            .values(
                'id', 'detection_date', 'created_at', 'legal_status',
                'confidence_score', 'area_hectares',
                'region__name', 'recurrence_count',
                'job__created_by__username',
            )
        )
        for s in recent_sites:
            s['confidence_pct'] = round(s['confidence_score'] * 100, 1)
            s['id'] = str(s['id'])

        # --- Average confidence of illegal detections ---
        from django.db.models import Avg
        avg_conf_result = DetectedSite.objects.filter(
            legal_status='illegal'
        ).aggregate(avg=Avg('confidence_score'))
        avg_confidence_pct = round((avg_conf_result['avg'] or 0) * 100, 1)

        # --- Detection velocity: illegal site count per week (configurable window) ---
        from django.db.models.functions import TruncWeek as _TruncWeek
        velocity_week_count = velocity_weeks   # already validated above
        velocity_start = now - timedelta(weeks=velocity_week_count)
        velocity_rows = (
            DetectedSite.objects
            .filter(legal_status='illegal', created_at__gte=velocity_start)
            .annotate(week=_TruncWeek('created_at'))
            .values('week')
            .annotate(cnt=Count('id'))
            .order_by('week')
        )
        velocity_by_week = {row['week'].strftime('%Y-W%W'): row['cnt'] for row in velocity_rows}
        velocity_labels, velocity_data = [], []
        for i in range(velocity_week_count - 1, -1, -1):
            week_start = now - timedelta(weeks=i)
            key = week_start.strftime('%Y-W%W')
            velocity_labels.append('W' + week_start.strftime('%W'))
            velocity_data.append(velocity_by_week.get(key, 0))

        # ── Concession expiry (live — feeds dashboard warning banner) ─────────
        from apps.detections.models import LegalConcession
        _today        = now.date()
        _thirty_ahead = _today + timedelta(days=30)
        expiring_concessions = LegalConcession.objects.filter(
            is_active=True, valid_to__isnull=False,
            valid_to__gte=_today, valid_to__lte=_thirty_ahead,
        ).order_by('valid_to').values('concession_name', 'license_number', 'valid_to')[:10]
        expired_concessions_count = LegalConcession.objects.filter(
            is_active=True, valid_to__isnull=False, valid_to__lt=_today,
        ).count()
        expiring_concessions = list(expiring_concessions)

        result = {
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
            'avg_confidence_pct': avg_confidence_pct,
            'velocity_labels': velocity_labels,
            'velocity_data': velocity_data,
            'trends': {
                'sites_change': sites_this_week,
                'alerts_change': alerts_change_pct,
            },
            'has_data': total_sites > 0,
            # Auto scan
            'scan_enabled':          scan_config.is_enabled,
            'scan_within_window':    scan_config.is_within_window(),
            'auto_jobs_today':       auto_jobs_today,
            'auto_detections_today': auto_detections_today,
            'scan_coverage_pct':     scan_coverage_pct,
            'scan_tiles_scanned':    scan_tiles_scanned,
            'scan_tiles_total':      scan_tiles_total,
            # Trend period
            'trend_days':            trend_days,
            # Concession expiry
            'expiring_concessions':      expiring_concessions,
            'expired_concessions_count': expired_concessions_count,
        }
        cache.set(cache_key, result, 300)  # 5-minute cache
        return result
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
            'avg_confidence_pct': 0,
            'velocity_labels': [],
            'velocity_data': [],
            'trends': {'sites_change': 0, 'alerts_change': 0},
            'has_data': False,
            'scan_enabled': False,
            'scan_within_window': False,
            'auto_jobs_today': 0,
            'auto_detections_today': 0,
            'scan_coverage_pct': 0,
            'scan_tiles_scanned': 0,
            'scan_tiles_total': 0,
            'trend_days': 30,
            'expiring_concessions':      [],
            'expired_concessions_count': 0,
        }


@login_required
def dashboard_home(request):
    """Admin-only dashboard home with automatic redirect for inspectors"""
    logger.debug(f"dashboard_home: user={request.user}, authenticated={request.user.is_authenticated}")
    
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            logger.debug(f" dashboard_home: profile.role={profile.role}")
            if profile.role == UserProfile.Role.INSPECTOR:
                logger.debug(f" dashboard_home: Inspector detected, redirecting to inspector dashboard")
                return redirect('/dashboard/inspector/')
        except UserProfile.DoesNotExist:
            logger.debug(f" dashboard_home: No profile, creating ADMIN profile")
            UserProfile.objects.create(user=request.user, role=UserProfile.Role.ADMIN)
    
    # Any non-inspector gets admin dashboard
    try:
        if request.user.profile.role == UserProfile.Role.INSPECTOR:
            logger.debug(f" dashboard_home: Inspector redirecting to inspector dashboard")
            return redirect('/dashboard/inspector/')
    except UserProfile.DoesNotExist:
        pass
    
    logger.debug(f"dashboard_home: Rendering admin dashboard")
    try:
        try:
            _vw = int(request.GET.get('velocity_weeks', 8))
        except (ValueError, TypeError):
            _vw = 8
        try:
            _td = int(request.GET.get('trend_days', 30))
            if _td not in (7, 30, 90, 365):
                _td = 30
        except (ValueError, TypeError):
            _td = 30
        stats = _get_dashboard_stats(velocity_weeks=_vw, trend_days=_td)
    except Exception as e:
        _vw = 8
        _td = 30
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
            'trend_days': 30,
            'trends': {'sites_change': 0, 'alerts_change': 0},
            'has_data': False,
        }

    return render(request, 'dashboard/dashboard.html', {
        'stats': stats,
        'velocity_weeks': _vw,
        'trend_days': _td,
    })


@login_required
def dashboard_chart_data(request):
    """JSON endpoint returning chart-only data — used by AJAX selectors (no page reload)."""
    try:
        vw = int(request.GET.get('velocity_weeks', 8))
    except (ValueError, TypeError):
        vw = 8
    try:
        td = int(request.GET.get('trend_days', 30))
        if td not in (7, 30, 90, 365):
            td = 30
    except (ValueError, TypeError):
        td = 30
    stats = _get_dashboard_stats(velocity_weeks=vw, trend_days=td)
    return JsonResponse({
        'trend_labels':    stats.get('trend_labels', []),
        'trend_illegal':   stats.get('trend_illegal', []),
        'trend_legal':     stats.get('trend_legal', []),
        'trend_days':      stats.get('trend_days', 30),
        'velocity_data':   stats.get('velocity_data', []),
        'velocity_labels': stats.get('velocity_labels', []),
        'velocity_weeks':  vw,
    })


@user_passes_test(is_admin)
def dashboard_alerts(request):
    """Admin-only alerts view"""
    from apps.detections.models import Alert
    from django.db.models import Count

    rows = Alert.objects.values('status', 'severity').annotate(cnt=Count('id'))
    by_status, by_severity = {}, {}
    for row in rows:
        by_status[row['status']] = by_status.get(row['status'], 0) + row['cnt']
        by_severity[row['severity']] = by_severity.get(row['severity'], 0) + row['cnt']

    summary = {
        'total':        sum(by_status.values()),
        'open':         by_status.get('open', 0),
        'acknowledged': by_status.get('acknowledged', 0),
        'dispatched':   by_status.get('dispatched', 0),
        'resolved':     by_status.get('resolved', 0),
        'dismissed':    by_status.get('dismissed', 0),
        'critical':     by_severity.get('critical', 0),
        'high':         by_severity.get('high', 0),
    }
    return render(request, 'dashboard/alerts.html', {'summary': summary})


@user_passes_test(is_admin)
def dashboard_audit(request):
    """Audit trail page — filterable log of all significant system actions."""
    from apps.detections.models import AuditLog

    qs = AuditLog.objects.select_related('user').order_by('-timestamp')

    # Filters
    action_filter = request.GET.get('action', '').strip()
    user_filter   = request.GET.get('user', '').strip()
    date_from     = request.GET.get('date_from', '').strip()
    date_to       = request.GET.get('date_to', '').strip()

    if action_filter:
        qs = qs.filter(action=action_filter)
    if user_filter == '__system__':
        qs = qs.filter(user__isnull=True)
    elif user_filter:
        qs = qs.filter(user__username=user_filter)
    if date_from:
        qs = qs.filter(timestamp__date__gte=date_from)
    if date_to:
        qs = qs.filter(timestamp__date__lte=date_to)

    # Pagination
    from django.core.paginator import Paginator
    paginator = Paginator(qs, 50)
    page_num  = request.GET.get('page', 1)
    page_obj  = paginator.get_page(page_num)

    # Distinct action types for the filter dropdown
    action_choices = (
        AuditLog.objects.values_list('action', flat=True)
        .distinct().order_by('action')
    )
    # Source users from UserProfile so new admins/inspectors appear even before
    # they've performed any auditable actions
    user_choices = (
        UserProfile.objects.select_related('user')
        .values_list('user__username', flat=True)
        .order_by('user__username')
    )

    # AJAX request — return JSON so the frontend can update the table in-place
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        entries_data = []
        for entry in page_obj:
            entries_data.append({
                'timestamp_date': entry.timestamp.strftime('%d %b %Y'),
                'timestamp_time': entry.timestamp.strftime('%H:%M:%S'),
                'user':      entry.user.username if entry.user else None,
                'action':    entry.action,
                'object_id': entry.object_id or None,
                'detail':    entry.detail or {},
            })
        return JsonResponse({
            'entries':      entries_data,
            'page':         page_obj.number,
            'total_pages':  page_obj.paginator.num_pages,
            'total':        page_obj.paginator.count,
            'has_previous': page_obj.has_previous(),
            'has_next':     page_obj.has_next(),
            'previous_page': page_obj.previous_page_number() if page_obj.has_previous() else None,
            'next_page':    page_obj.next_page_number() if page_obj.has_next() else None,
        })

    return render(request, 'dashboard/audit.html', {
        'page_obj':      page_obj,
        'action_choices': action_choices,
        'user_choices':  user_choices,
        'filters': {
            'action':    action_filter,
            'user':      user_filter,
            'date_from': date_from,
            'date_to':   date_to,
        },
    })


@login_required
def dashboard_kpis(request):
    """JSON endpoint returning headline KPI numbers — polled every 60 s for live refresh."""
    stats = _get_dashboard_stats()
    return JsonResponse({
        'total_detected_sites':  stats.get('total_detected_sites', 0),
        'illegal_sites':         stats.get('illegal_sites', 0),
        'open_alerts':           stats.get('open_alerts', 0),
        'critical_alerts':       stats.get('critical_alerts', 0),
        'high_alerts':           stats.get('high_alerts', 0),
        'high_risk_zones':       stats.get('high_risk_zones', 0),
        'total_area_ha':         stats.get('total_area_ha', 0),
        'completed_jobs':        stats.get('completed_jobs', 0),
        'failed_jobs':           stats.get('failed_jobs', 0),
        'total_jobs':            stats.get('total_jobs', 0),
        'alerts_this_month':     stats.get('alerts_this_month', 0),
        'alerts_change':         stats.get('trends', {}).get('alerts_change', 0),
        'sites_change':          stats.get('trends', {}).get('sites_change', 0),
        'auto_jobs_today':       stats.get('auto_jobs_today', 0),
        'auto_detections_today': stats.get('auto_detections_today', 0),
        'scan_coverage_pct':     stats.get('scan_coverage_pct', 0),
    })


@user_passes_test(is_admin)
def dashboard_report(request):
    """Printable/downloadable overview report for stakeholders.

    Accepts optional ?start_date=YYYY-MM-DD&end_date=YYYY-MM-DD.
    Defaults to the last 30 days.
    """
    from apps.detections.models import DetectedSite, Alert
    from apps.accounts.models import InspectorAssignment
    from django.db.models import Count, Sum, Q

    now = timezone.now()

    # ── Parse date range ────────────────────────────────────────────────────
    try:
        period_start = date.fromisoformat(request.GET['start_date'])
    except (KeyError, ValueError):
        period_start = (now - timedelta(days=30)).date()
    try:
        period_end = date.fromisoformat(request.GET['end_date'])
    except (KeyError, ValueError):
        period_end = now.date()

    # Clamp so start ≤ end
    if period_start > period_end:
        period_start, period_end = period_end, period_start

    # ── Period-scoped site stats ─────────────────────────────────────────────
    sites_qs = DetectedSite.objects.filter(
        detection_date__gte=period_start,
        detection_date__lte=period_end,
    )
    total_sites    = sites_qs.count()
    illegal_sites  = sites_qs.filter(legal_status='illegal').count()
    total_area_ha  = round(
        sites_qs.filter(legal_status='illegal')
        .aggregate(t=Sum('area_hectares'))['t'] or 0, 1
    )
    high_risk = sites_qs.filter(
        Q(recurrence_count__gt=1) | Q(alerts__severity='critical')
    ).distinct().count()

    # Top regions for the period
    top_regions = list(
        sites_qs.filter(region__isnull=False)
        .values('region__id', 'region__name')
        .annotate(total=Count('id'), illegal=Count('id', filter=Q(legal_status='illegal')))
        .order_by('-total')[:6]
    )
    if top_regions:
        max_total = top_regions[0]['total']
        for r in top_regions:
            r['illegal_pct'] = round((r['illegal'] / r['total']) * 100) if r['total'] else 0
            r['bar_pct'] = round((r['total'] / max_total) * 100) if max_total else 0

    # Recent sites in period
    recent_sites = list(
        sites_qs.select_related('region')
        .order_by('-detection_date')[:10]
        .values(
            'id', 'detection_date', 'legal_status',
            'confidence_score', 'area_hectares',
            'region__name', 'recurrence_count',
        )
    )
    for s in recent_sites:
        s['confidence_pct'] = round(s['confidence_score'] * 100, 1)
        s['id'] = str(s['id'])

    # ── Period-scoped alert stats ────────────────────────────────────────────
    alerts_qs = Alert.objects.filter(
        created_at__date__gte=period_start,
        created_at__date__lte=period_end,
    )
    open_alerts     = alerts_qs.filter(status='open').count()
    critical_alerts = alerts_qs.filter(status='open', severity='critical').count()
    high_alerts     = alerts_qs.filter(status='open', severity='high').count()

    alerts_by_severity = {
        row['severity']: row['cnt']
        for row in alerts_qs.values('severity').annotate(cnt=Count('id'))
    }
    alerts_by_status = {
        row['status']: row['cnt']
        for row in alerts_qs.values('status').annotate(cnt=Count('id'))
    }

    # ── Period-scoped inspector stats ───────────────────────────────────────
    assign_qs = InspectorAssignment.objects.filter(
        completed_at__date__gte=period_start,
        completed_at__date__lte=period_end,
    )
    resolved_assignments = assign_qs.filter(status='resolved').count()
    confirmed_mining     = assign_qs.filter(outcome='mining_confirmed').count()
    false_positives      = assign_qs.filter(outcome='false_positive').count()

    # Per-inspector performance breakdown
    from django.db.models import ExpressionWrapper, DurationField, Avg, F as _F
    _assign_period_qs = InspectorAssignment.objects.filter(
        assigned_at__date__gte=period_start,
        assigned_at__date__lte=period_end,
    )
    inspector_stats = list(
        _assign_period_qs
        .values('inspector__user__username')
        .annotate(
            total=Count('id'),
            confirmed=Count('id', filter=Q(outcome='mining_confirmed')),
            fp=Count('id', filter=Q(outcome='false_positive')),
            inconclusive=Count('id', filter=Q(outcome='inconclusive')),
            pending=Count('id', filter=Q(status='pending')),
        )
        .order_by('-total')
    )
    # Compute average response time per inspector (only resolved with both timestamps)
    _resolved_timed = (
        _assign_period_qs
        .filter(status='resolved', completed_at__isnull=False)
        .annotate(duration=ExpressionWrapper(_F('completed_at') - _F('assigned_at'), output_field=DurationField()))
        .values('inspector__user__username')
        .annotate(avg_dur=Avg('duration'))
    )
    _avg_map = {r['inspector__user__username']: r['avg_dur'] for r in _resolved_timed}
    for row in inspector_stats:
        dur = _avg_map.get(row['inspector__user__username'])
        row['avg_days'] = round(dur.days + dur.seconds / 86400, 1) if dur else None

    # ── Scan jobs in period ──────────────────────────────────────────────────
    from apps.jobs.models import Job as ScanJob
    jobs_qs     = ScanJob.objects.filter(
        created_at__date__gte=period_start,
        created_at__date__lte=period_end,
    )
    total_jobs     = jobs_qs.count()
    completed_jobs = jobs_qs.filter(status='completed').count()
    failed_jobs    = jobs_qs.filter(status='failed').count()

    recent_jobs = list(
        jobs_qs.select_related('created_by')
        .order_by('-created_at')[:10]
        .values(
            'id', 'status', 'created_at', 'completed_at',
            'total_detections', 'illegal_count',
            'created_by__username',
        )
    )
    for j in recent_jobs:
        j['id'] = str(j['id'])[:8].upper()

    # ── 30-day detection trend for sparkline (within selected period) ────────
    from django.db.models.functions import TruncDate as _TD
    trend_rows = (
        sites_qs
        .annotate(day=_TD('detection_date'))
        .values('day', 'legal_status')
        .annotate(cnt=Count('id'))
        .order_by('day')
    )
    illegal_by_day, legal_by_day = {}, {}
    for row in trend_rows:
        d = row['day']
        if row['legal_status'] == 'illegal':
            illegal_by_day[d] = row['cnt']
        else:
            legal_by_day[d] = row['cnt']

    delta_days = (period_end - period_start).days
    trend_labels, trend_illegal, trend_legal = [], [], []
    for i in range(delta_days + 1):
        d = period_start + timedelta(days=i)
        trend_labels.append(d.strftime('%d %b'))
        trend_illegal.append(illegal_by_day.get(d, 0))
        trend_legal.append(legal_by_day.get(d, 0))

    stats = {
        'total_detected_sites': total_sites,
        'illegal_sites':        illegal_sites,
        'open_alerts':          open_alerts,
        'critical_alerts':      critical_alerts,
        'high_alerts':          high_alerts,
        'high_risk_zones':      high_risk,
        'total_area_ha':        total_area_ha,
        'total_jobs':           total_jobs,
        'completed_jobs':       completed_jobs,
        'failed_jobs':          failed_jobs,
        'top_regions':          top_regions,
        'recent_sites':         recent_sites,
        'trend_labels':         trend_labels,
        'trend_illegal':        trend_illegal,
        'trend_legal':          trend_legal,
    }

    return render(request, 'dashboard/report.html', {
        'stats':              stats,
        'alerts_by_severity': alerts_by_severity,
        'alerts_by_status':   alerts_by_status,
        'resolved_assignments': resolved_assignments,
        'confirmed_mining':   confirmed_mining,
        'false_positives':    false_positives,
        'inspector_stats':    inspector_stats,
        'recent_jobs':        recent_jobs,
        'report_date':        now,
        'period_start':       period_start,
        'period_end':         period_end,
    })


@user_passes_test(is_admin)
def dashboard_report_pdf(request):
    """Generate a server-side PDF of the report using xhtml2pdf.
    Accepts the same start_date / end_date params as dashboard_report.
    """
    import io
    from django.http import HttpResponse
    from django.template.loader import render_to_string

    # Reuse the same context-building logic — forward request to dashboard_report
    # then re-render as PDF
    report_response = dashboard_report(request)
    html_string = report_response.content.decode('utf-8')

    try:
        from xhtml2pdf import pisa
        pdf_buffer = io.BytesIO()
        pisa_status = pisa.CreatePDF(html_string, dest=pdf_buffer)
        if pisa_status.err:
            return HttpResponse('PDF generation failed.', status=500)
        pdf_buffer.seek(0)
        filename = f"GalamseyWatch_Report_{date.today().isoformat()}.pdf"
        response = HttpResponse(pdf_buffer, content_type='application/pdf')
        response['Content-Disposition'] = f'attachment; filename="{filename}"'
        return response
    except ImportError:
        return HttpResponse('xhtml2pdf is not installed.', status=500)


@user_passes_test(is_admin)
def dashboard_model_insights(request):
    """Admin-only model insights view — live metrics driven by inspector field reports."""
    import json
    import calendar
    from collections import defaultdict
    from apps.accounts.models import InspectorAssignment
    from apps.detections.models import DetectedSite

    # ── Confidence distribution (all detections) ─────────────────────────────
    scores = list(
        DetectedSite.objects.filter(confidence_score__isnull=False)
        .values_list('confidence_score', flat=True)
    )
    conf_total = len(scores)
    conf_bins = [0, 0, 0, 0, 0]   # 50-60, 60-70, 70-80, 80-90, 90-100
    for s in scores:
        if s >= 0.5:
            idx = min(int((s - 0.5) / 0.1), 4)
            conf_bins[idx] += 1

    # ── All field-verified outcomes, oldest first ─────────────────────────────
    verified = list(
        InspectorAssignment.objects.filter(
            outcome__in=['mining_confirmed', 'false_positive'],
            completed_at__isnull=False,
        ).order_by('completed_at')
    )

    total_verified = len(verified)
    total_tp = sum(1 for a in verified if a.outcome == 'mining_confirmed')
    total_fp = sum(1 for a in verified if a.outcome == 'false_positive')
    has_field_data = total_verified > 0

    # ── Live Precision — rolling last N verified ──────────────────────────────
    PRECISION_WINDOW = django_settings.MODEL_PRECISION_WINDOW
    _precision_fallback = django_settings.MODEL_TEST_METRICS['precision_fallback']
    recent = verified[-PRECISION_WINDOW:]
    if recent:
        w_tp = sum(1 for a in recent if a.outcome == 'mining_confirmed')
        w_fp = sum(1 for a in recent if a.outcome == 'false_positive')
        live_precision = round(w_tp / (w_tp + w_fp) * 100, 1) if (w_tp + w_fp) else _precision_fallback
    else:
        live_precision = _precision_fallback   # test-set fallback

    # ── Live Accuracy — EMA starting from test-set baseline ──────────────────
    ALPHA = 0.05            # each report carries ~5% weight
    BASE_ACCURACY = django_settings.MODEL_BASE_ACCURACY
    ema = BASE_ACCURACY
    for a in verified:
        ema = ALPHA * (1.0 if a.outcome == 'mining_confirmed' else 0.0) + (1 - ALPHA) * ema
    live_accuracy = round(ema * 100, 1)

    # ── Monthly chart data — last 12 months ──────────────────────────────────
    now = timezone.now()
    months = []
    for i in range(11, -1, -1):
        # Step back month by month
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    # Bucket verified assignments into months
    monthly_buckets = defaultdict(lambda: {'tp': 0, 'fp': 0})
    for a in verified:
        key = (a.completed_at.year, a.completed_at.month)
        if a.outcome == 'mining_confirmed':
            monthly_buckets[key]['tp'] += 1
        else:
            monthly_buckets[key]['fp'] += 1

    monthly_labels     = []
    monthly_precision  = []
    monthly_accuracy   = []
    running_ema        = BASE_ACCURACY  # already read from settings above

    for y, m in months:
        monthly_labels.append(f"{calendar.month_abbr[m]} '{str(y)[2:]}")
        bucket = monthly_buckets.get((y, m), {'tp': 0, 'fp': 0})
        m_tp, m_fp = bucket['tp'], bucket['fp']

        # Apply EMA for every outcome in this month (chronological nudges)
        for _ in range(m_tp):
            running_ema = ALPHA * 1.0 + (1 - ALPHA) * running_ema
        for _ in range(m_fp):
            running_ema = ALPHA * 0.0 + (1 - ALPHA) * running_ema

        monthly_accuracy.append(round(running_ema * 100, 1))
        monthly_precision.append(
            round(m_tp / (m_tp + m_fp) * 100, 1) if (m_tp + m_fp) else None
        )

    return render(request, 'dashboard/model_insights.html', {
        # Live metrics
        'live_precision':   live_precision,
        'live_accuracy':    live_accuracy,
        'has_field_data':   has_field_data,
        'total_verified':   total_verified,
        'total_tp':         total_tp,
        'total_fp':         total_fp,
        # Static test-set metrics (from settings)
        'model_metrics':    django_settings.MODEL_TEST_METRICS,
        # Confidence distribution
        'conf_bins':        conf_bins,
        'conf_total':       conf_total,
        # Chart series (JSON strings)
        'monthly_labels':    json.dumps(monthly_labels),
        'monthly_precision': json.dumps(monthly_precision),
        'monthly_accuracy':  json.dumps(monthly_accuracy),
    })


@user_passes_test(is_inspector_or_admin)
def dashboard_settings(request):
    """Settings view for all authenticated users"""
    # Get or create user preferences
    from apps.accounts.models import UserPreferences
    preferences, created = UserPreferences.objects.get_or_create(user=request.user)
    
    context = {
        'preferences': preferences,
        'settings': {
            'APP_NAME': 'GalamseyWatch',
            'APP_VERSION': '2.1.0',
            'ENVIRONMENT': 'Development'
        }
    }
    return render(request, 'dashboard/settings.html', context)


@user_passes_test(is_inspector_or_admin)
def inspector_dashboard(request):
    """Inspector-specific dashboard"""
    try:
        from apps.accounts.models import InspectorAssignment, EvidencePhoto
        from apps.detections.models import Alert

        thirty_days_ago = timezone.now() - timedelta(days=30)
        profile = request.user.profile

        # Load PENDING + last-30-days RESOLVED; use select_related to avoid N+1
        assignments = (
            InspectorAssignment.objects
            .filter(
                inspector=profile,
            )
            .filter(
                Q(status=InspectorAssignment.Status.PENDING)
                | Q(
                    status=InspectorAssignment.Status.RESOLVED,
                    completed_at__gte=thirty_days_ago,
                )
            )
            .select_related('inspector__user')
            .prefetch_related('evidence_photo_set')
            .order_by('-created_at')
        )

        assignment_data = []
        alert_ids = [a.alert_id for a in assignments]
        alerts_map = {
            a.id: a
            for a in Alert.objects.filter(id__in=alert_ids)
            .select_related('detected_site', 'detected_site__region', 'detected_site__job')
            .prefetch_related('detected_site__timelapse_frames')
        }

        for assignment in assignments:
            alert = alerts_map.get(assignment.alert_id)
            if alert:
                site = alert.detected_site
                centroid_lng = site.centroid.x if site and site.centroid else None
                centroid_lat = site.centroid.y if site and site.centroid else None
                timelapse = list(
                    site.timelapse_frames.order_by('year').values(
                        'year', 'thumbnail_url', 'acquisition_period'
                    )
                ) if site else []

                # Prefer EvidencePhoto records; fall back to legacy JSONField paths
                evidence_photos = list(assignment.evidence_photo_set.all())
                if evidence_photos:
                    photo_urls = [django_settings.MEDIA_URL + ep.file.name for ep in evidence_photos]
                else:
                    photo_urls = [
                        django_settings.MEDIA_URL + p for p in (assignment.evidence_photos or [])
                    ]

                patch_images = None
                job = getattr(site, 'job', None) if site else None
                if job:
                    _imgs = {
                        'false_color':         django_settings.MEDIA_URL + job.img_false_color         if job.img_false_color else None,
                        'prediction_mask':     django_settings.MEDIA_URL + job.img_prediction_mask     if job.img_prediction_mask else None,
                        'probability_heatmap': django_settings.MEDIA_URL + job.img_probability_heatmap if job.img_probability_heatmap else None,
                        'overlay':             django_settings.MEDIA_URL + job.img_overlay             if job.img_overlay else None,
                    }
                    if any(_imgs.values()):
                        patch_images = _imgs

                assignment_data.append({
                    'assignment': assignment,
                    'alert': alert,
                    'site': site,
                    'centroid_lng': centroid_lng,
                    'centroid_lat': centroid_lat,
                    'timelapse_frames': timelapse,
                    'photo_urls': photo_urls,
                    'patch_images': patch_images,
                })
            else:
                assignment_data.append({
                    'assignment': assignment,
                    'alert': None,
                    'site': None,
                    'centroid_lng': None,
                    'centroid_lat': None,
                    'timelapse_frames': [],
                    'photo_urls': [],
                    'patch_images': None,
                })

        pending_count = sum(1 for d in assignment_data if d['assignment'].status == 'pending')
        verified_count = sum(
            1 for d in assignment_data
            if d['assignment'].status == 'resolved'
            and d['assignment'].outcome in ('mining_confirmed', 'false_positive')
        )
        inconclusive_count = sum(
            1 for d in assignment_data
            if d['assignment'].status == 'resolved'
            and d['assignment'].outcome == 'inconclusive'
        )

        return render(request, 'dashboard/inspector.html', {
            'assignments': assignment_data,
            'pending_count': pending_count,
            'verified_count': verified_count,
            'inconclusive_count': inconclusive_count,
            'is_available': profile.is_available,
        })
    except Exception as e:
        return render(request, 'dashboard/inspector.html', {
            'assignments': [],
            'pending_count': 0,
            'verified_count': 0,
            'inconclusive_count': 0,
            'is_available': True,
            'error': str(e)
        })


@login_required
def submit_field_report(request, assignment_id):
    """Inspector submits their field verification report (outcome, visit date, notes, photos)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        from apps.accounts.models import InspectorAssignment, EvidencePhoto
        from apps.detections.models import Alert, DetectedSite

        assignment = InspectorAssignment.objects.get(id=assignment_id, inspector=request.user.profile)

        # Prevent re-submission of a definitively resolved assignment
        # (inconclusive stays PENDING so the inspector can resubmit)
        if (assignment.status == InspectorAssignment.Status.RESOLVED
                and assignment.outcome in ('mining_confirmed', 'false_positive')):
            return JsonResponse(
                {'error': 'Field report already submitted for this assignment.'},
                status=409
            )

        outcome = request.POST.get('outcome', '').strip()
        visit_date_str = request.POST.get('visit_date', '').strip()
        notes = request.POST.get('notes', '').strip()

        valid_outcomes = [o[0] for o in InspectorAssignment.Outcome.choices]
        if outcome not in valid_outcomes:
            return JsonResponse({'error': 'Invalid outcome'}, status=400)

        # Parse visit date
        visit_date = None
        if visit_date_str:
            try:
                from datetime import date as date_cls
                visit_date = date_cls.fromisoformat(visit_date_str)
            except ValueError:
                return JsonResponse({'error': 'Invalid visit date format'}, status=400)

        # Save uploaded photos and create EvidencePhoto records
        import hashlib
        photos = request.FILES.getlist('evidence_photos')
        photo_paths = list(assignment.evidence_photos or [])
        if photos:
            upload_dir = os.path.join(django_settings.MEDIA_ROOT, 'inspections', str(assignment_id))
            os.makedirs(upload_dir, exist_ok=True)
            for photo in photos:
                safe_name = os.path.basename(photo.name).replace(' ', '_')
                dest = os.path.join(upload_dir, safe_name)
                content = photo.read()
                sha256 = hashlib.sha256(content).hexdigest()
                with open(dest, 'wb') as f:
                    f.write(content)
                rel_path = f'inspections/{assignment_id}/{safe_name}'
                photo_paths.append(rel_path)
                try:
                    EvidencePhoto.objects.create(
                        assignment=assignment,
                        file=rel_path,
                        sha256_hash=sha256,
                        original_name=photo.name,
                    )
                except Exception:
                    pass

        # Update assignment
        assignment.outcome = outcome
        assignment.visit_date = visit_date
        assignment.notes = notes
        assignment.evidence_photos = photo_paths
        # Inconclusive stays PENDING so the inspector can come back and resubmit
        if outcome in ('mining_confirmed', 'false_positive'):
            assignment.status = InspectorAssignment.Status.RESOLVED
            if not assignment.completed_at:
                assignment.completed_at = timezone.now()
        assignment.save()

        # Update Alert
        try:
            alert = Alert.objects.get(id=assignment.alert_id)
            if outcome == 'inconclusive':
                # Increment inconclusive counter; auto-escalate if limit reached
                alert.inspection_count = (alert.inspection_count or 0) + 1
                alert.status = Alert.AlertStatus.OPEN
                alert.resolved_at = None

                inconclusive_limit = getattr(django_settings, 'ALERT_INCONCLUSIVE_ESCALATION_COUNT', 3)
                if alert.inspection_count >= inconclusive_limit:
                    alert.severity = Alert.Severity.CRITICAL
                    alert.escalated_at = timezone.now()
                    try:
                        from apps.detections.models import AuditLog
                        AuditLog.objects.create(
                            user=None,
                            action='alert.escalated',
                            object_id=str(alert.id),
                            detail={
                                'reason': 'inconclusive_limit_reached',
                                'inspection_count': alert.inspection_count,
                                'inspector': request.user.username,
                            },
                        )
                    except Exception:
                        pass
            else:
                alert.status = Alert.AlertStatus.RESOLVED
                alert.resolved_at = timezone.now()
            outcome_label = dict(InspectorAssignment.Outcome.choices).get(outcome, outcome)
            alert.resolution_notes = (
                f"Field report by {request.user.username} on "
                f"{visit_date or 'unknown date'}: {outcome_label}. {notes}"
            )
            alert.save()

            # Audit the field report submission
            try:
                from apps.detections.models import AuditLog
                AuditLog.objects.create(
                    user=request.user,
                    action='assignment.field_report',
                    object_id=str(assignment.id),
                    detail={
                        'outcome': outcome,
                        'alert_id': str(alert.id),
                        'inspector': request.user.username,
                        'visit_date': str(visit_date) if visit_date else None,
                    },
                )
            except Exception:
                pass

            # Update DetectedSite status to match outcome
            site = alert.detected_site
            if outcome == 'mining_confirmed':
                site.status = DetectedSite.Status.CONFIRMED_ILLEGAL
            elif outcome == 'false_positive':
                site.status = DetectedSite.Status.FALSE_POSITIVE
            # inconclusive: leave site status unchanged
            if outcome != 'inconclusive':
                site.reviewed_by = request.user
                site.reviewed_at = timezone.now()
                site.review_notes = notes
                site.save()
        except Alert.DoesNotExist:
            pass

        try:
            import threading
            from apps.notifications.services import send_field_report_received
            threading.Thread(
                target=send_field_report_received,
                args=(assignment, assignment.alert),
                daemon=True
            ).start()
        except Exception:
            pass

        cache.delete_many([f'dashboard_stats_{w}_{td}' for w in range(2, 53) for td in (7, 30, 90, 365)])
        return JsonResponse({
            'success': True,
            'outcome': outcome,
            'message': f'Field report submitted: {dict(InspectorAssignment.Outcome.choices).get(outcome)}'
        })

    except InspectorAssignment.DoesNotExist:
        return JsonResponse({'error': 'Assignment not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ---------------------------------------------------------------------------
# Region summary page
# ---------------------------------------------------------------------------

@login_required
@user_passes_test(is_admin)
def region_list(request):
    """List all active monitoring regions with summary stats."""
    from apps.detections.models import Region
    from django.db.models import Count, Q

    regions = list(
        Region.objects.filter(is_active=True)
        .annotate(
            total_detections=Count('detections', distinct=True),
            illegal_detections=Count(
                'detections',
                filter=Q(detections__legal_status='illegal'),
                distinct=True,
            ),
            open_alerts=Count(
                'detections__alerts',
                filter=Q(detections__alerts__status='open'),
                distinct=True,
            ),
        )
        .order_by('-total_detections')
    )

    return render(request, 'dashboard/region_list.html', {
        'regions': regions,
    })


@login_required
@user_passes_test(is_admin)
def region_detail(request, region_id):
    """Per-region summary: detection stats, alert breakdown, inspector assignments, 30-day trend, and full detection cards."""
    from apps.detections.models import Region, DetectedSite, Alert
    from apps.accounts.models import InspectorAssignment
    from django.db.models import Count, Sum, Q, Avg
    from django.db.models.functions import TruncDate
    from datetime import timedelta

    region = get_object_or_404(Region, id=region_id)

    sites_qs = DetectedSite.objects.filter(region=region)

    total_sites   = sites_qs.count()
    illegal_sites = sites_qs.filter(legal_status='illegal').count()
    total_area_ha = round(
        sites_qs.filter(legal_status='illegal')
        .aggregate(t=Sum('area_hectares'))['t'] or 0, 1
    )
    avg_conf = round(
        (sites_qs.aggregate(a=Avg('confidence_score'))['a'] or 0) * 100, 1
    )
    recurrent = sites_qs.filter(recurrence_count__gt=1).count()

    # Alerts for this region
    alerts_qs       = Alert.objects.filter(detected_site__region=region)
    open_alerts     = alerts_qs.filter(status='open').count()
    critical_alerts = alerts_qs.filter(status='open', severity='critical').count()

    # Inspector assignments via alerts
    assignments_qs    = InspectorAssignment.objects.filter(alert__detected_site__region=region)
    total_assignments = assignments_qs.count()
    confirmed_mining  = assignments_qs.filter(outcome='mining_confirmed').count()
    false_positives   = assignments_qs.filter(outcome='false_positive').count()

    # Assigned inspectors (from Region.assigned_inspectors M2M)
    assigned_inspectors = list(region.assigned_inspectors.all())

    # 30-day detection trend
    thirty_ago = timezone.now().date() - timedelta(days=29)
    trend_rows = (
        sites_qs
        .filter(detection_date__gte=thirty_ago)
        .annotate(day=TruncDate('detection_date'))
        .values('day', 'legal_status')
        .annotate(cnt=Count('id'))
        .order_by('day')
    )
    illegal_by_day, legal_by_day = {}, {}
    for row in trend_rows:
        d = row['day']
        if row['legal_status'] == 'illegal':
            illegal_by_day[d] = row['cnt']
        else:
            legal_by_day[d] = row['cnt']

    trend_labels, trend_illegal, trend_legal = [], [], []
    for i in range(30):
        d = thirty_ago + timedelta(days=i)
        trend_labels.append(d.strftime('%d %b'))
        trend_illegal.append(illegal_by_day.get(d, 0))
        trend_legal.append(legal_by_day.get(d, 0))

    # Full detection cards — 20 most recent with all related data
    raw_sites = list(
        sites_qs
        .select_related('job', 'job__created_by', 'model_run', 'reviewed_by', 'intersecting_concession')
        .prefetch_related('alerts', 'timelapse_frames')
        .order_by('-detection_date', '-confidence_score')[:20]
    )

    # Build enriched detection data dicts
    detection_data = []
    for site in raw_sites:
        job   = site.job
        alert = site.alerts.first()

        patch_images = None
        if job:
            imgs = {
                'false_color':         django_settings.MEDIA_URL + job.img_false_color         if job.img_false_color else None,
                'prediction_mask':     django_settings.MEDIA_URL + job.img_prediction_mask     if job.img_prediction_mask else None,
                'probability_heatmap': django_settings.MEDIA_URL + job.img_probability_heatmap if job.img_probability_heatmap else None,
                'overlay':             django_settings.MEDIA_URL + job.img_overlay             if job.img_overlay else None,
            }
            if any(imgs.values()):
                patch_images = imgs

        timelapse = list(site.timelapse_frames.order_by('year').values('year', 'thumbnail_url', 'acquisition_period'))

        detection_data.append({
            'site':            site,
            'alert':           alert,
            'patch_images':    patch_images,
            'timelapse':       timelapse,
            'confidence_pct':  round(site.confidence_score * 100, 1),
            'job_source':      job.source if job else '',
            'model_name':      site.model_run.model_name    if site.model_run else '',
            'model_version':   site.model_run.model_version if site.model_run else '',
            'val_precision':   round(site.model_run.val_precision * 100, 1) if site.model_run and site.model_run.val_precision else None,
            'val_iou':         round(site.model_run.val_iou * 100, 1)       if site.model_run and site.model_run.val_iou else None,
        })

    return render(request, 'dashboard/region_detail.html', {
        'region':              region,
        'total_sites':         total_sites,
        'illegal_sites':       illegal_sites,
        'total_area_ha':       total_area_ha,
        'avg_conf':            avg_conf,
        'recurrent':           recurrent,
        'open_alerts':         open_alerts,
        'critical_alerts':     critical_alerts,
        'total_assignments':   total_assignments,
        'confirmed_mining':    confirmed_mining,
        'false_positives':     false_positives,
        'assigned_inspectors': assigned_inspectors,
        'detection_data':      detection_data,
        'trend_labels':        trend_labels,
        'trend_illegal':       trend_illegal,
        'trend_legal':         trend_legal,
    })


# ---------------------------------------------------------------------------
# Email verification helpers & views
# ---------------------------------------------------------------------------

def _send_activation_email(request, user):
    """Send an account activation email to the given user."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    relative_url = reverse('dashboard:activate', kwargs={'uidb64': uid, 'token': token})
    link = request.build_absolute_uri(relative_url)
    subject = "Confirm your GalamseyWatch AI account"
    plain_body = f"Hi {user.username},\n\nClick the link to activate your account:\n{link}\n\nThis link expires in 24 hours."
    html_body = render_to_string('registration/activation_email.html', {'link': link, 'user': user})
    send_mail(
        subject=subject,
        message=plain_body,
        from_email=django_settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=True,
    )


def activation_sent(request):
    return render(request, 'registration/activation_sent.html')


def _send_activation_pin_email(user, pin):
    """Send a 6-digit activation PIN to a newly registered user."""
    subject = "Your GalamseyWatch AI account activation code"
    plain_body = f"Hi {user.username},\n\nYour activation code is: {pin}\n\nThis code expires in 24 hours."
    html_body = render_to_string('registration/activation_pin_email.html', {'pin': pin, 'user': user})
    send_mail(
        subject=subject,
        message=plain_body,
        from_email=django_settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=True,
    )


def activation_pin_entry(request):
    email = (request.GET.get('email') or request.POST.get('email', '')).strip().lower()
    error = None
    if request.method == 'POST':
        pin = request.POST.get('pin', '').strip()
        data = cache.get(f'activation_pin_{email}')
        if data and data['pin'] == pin:
            try:
                user = User.objects.get(pk=data['user_pk'])
                user.is_active = True
                user.save()
                cache.delete(f'activation_pin_{email}')
                messages.success(request, 'Account activated! You can now sign in.')
                return redirect('login')
            except User.DoesNotExist:
                error = 'Something went wrong. Please sign up again.'
        else:
            error = 'That code is incorrect or has expired.'
    return render(request, 'registration/activation_pin.html', {'email': email, 'error': error})


def resend_activation_pin(request):
    """Regenerate a 6-digit activation PIN and re-send it.
    Rate-limited to one resend per 60 seconds per email address."""
    email = request.GET.get('email', '').strip().lower()
    if not email:
        return redirect(reverse('dashboard:activation_pin_entry'))

    # Rate limit: one resend per 60 s
    rl_key = f'resend_pin_rl_{email}'
    if cache.get(rl_key):
        messages.warning(request, 'Please wait a moment before requesting another code.')
        return redirect(f'/dashboard/activation-pin/?email={email}')

    data = cache.get(f'activation_pin_{email}')
    if not data:
        messages.error(request, 'No pending activation found. Please sign up again.')
        return redirect(reverse('dashboard:signup'))

    try:
        user = User.objects.get(pk=data['user_pk'])
    except User.DoesNotExist:
        messages.error(request, 'Account not found. Please sign up again.')
        return redirect(reverse('dashboard:signup'))

    # Generate a fresh PIN, reset the 24-hour TTL, and set rate-limit flag
    pin = str(random.randint(100000, 999999))
    cache.set(f'activation_pin_{email}', {'pin': pin, 'user_pk': str(user.pk)}, timeout=86400)
    cache.set(rl_key, True, timeout=60)

    threading.Thread(
        target=_send_activation_pin_email, args=(user, pin), daemon=True
    ).start()

    messages.success(request, 'A new code has been sent. Check your inbox (and spam folder).')
    return redirect(f'/dashboard/activation-pin/?email={email}')


def activate_account(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        messages.success(request, 'Email confirmed! You can now sign in.')
        return redirect('login')

    return render(request, 'registration/activation_invalid.html')


# ---------------------------------------------------------------------------
# PIN-based password reset
# ---------------------------------------------------------------------------

def _send_pin_email(user, pin):
    """Send a 6-digit PIN to the user for password reset."""
    subject = "Your GalamseyWatch AI password reset code"
    plain_body = f"Hi {user.username},\n\nYour password reset code is: {pin}\n\nThis code expires in 10 minutes.\n\nIf you didn't request this, ignore this email."
    html_body = render_to_string('registration/pin_email.html', {'pin': pin, 'user': user})
    send_mail(
        subject=subject,
        message=plain_body,
        from_email=django_settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=True,
    )


def password_reset_request(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
            pin = str(random.randint(100000, 999999))
            cache.set(f'pwd_reset_{email}', {'pin': pin, 'user_pk': str(user.pk)}, timeout=600)
            threading.Thread(target=_send_pin_email, args=(user, pin), daemon=True).start()
        except User.DoesNotExist:
            pass  # Don't reveal if email exists
        return redirect(f'/accounts/password_reset/pin/?email={email}')
    return render(request, 'registration/password_reset_form.html')


def password_reset_pin_entry(request):
    email = (request.GET.get('email') or request.POST.get('email', '')).lower()
    error = None
    if request.method == 'POST':
        pin = request.POST.get('pin', '').strip()
        data = cache.get(f'pwd_reset_{email}')
        if data and data['pin'] == pin:
            request.session['pwd_reset_user_pk'] = data['user_pk']
            cache.delete(f'pwd_reset_{email}')
            return redirect('/accounts/password_reset/new/')
        else:
            error = 'That code is incorrect or has expired. Please try again.'
    return render(request, 'registration/password_reset_pin.html', {'email': email, 'error': error})


def password_reset_new_password(request):
    user_pk = request.session.get('pwd_reset_user_pk')
    if not user_pk:
        return redirect('/accounts/password_reset/')
    try:
        user = User.objects.get(pk=user_pk)
    except User.DoesNotExist:
        return redirect('/accounts/password_reset/')

    error = None
    if request.method == 'POST':
        p1 = request.POST.get('new_password1', '')
        p2 = request.POST.get('new_password2', '')
        if not p1:
            error = 'Please enter a new password.'
        elif p1 != p2:
            error = 'Passwords do not match.'
        elif len(p1) < 8:
            error = 'Password must be at least 8 characters.'
        else:
            user.set_password(p1)
            user.save()
            del request.session['pwd_reset_user_pk']
            messages.success(request, 'Password changed! You can now sign in.')
            return redirect('login')

    return render(request, 'registration/password_reset_new.html', {'error': error})


# ---------------------------------------------------------------------------
# My Account
# ---------------------------------------------------------------------------

@login_required
def my_account(request):
    """Let any authenticated user update their email, name, organisation,
    and phone number.  Password change is delegated to Django's built-in
    password_change view."""
    from django.core.validators import validate_email as _validate_email
    from django.core.exceptions import ValidationError as _ValidationError

    profile = request.user.profile
    errors = {}

    if request.method == 'POST':
        new_email      = request.POST.get('email', '').strip()
        new_first      = request.POST.get('first_name', '').strip()
        new_last       = request.POST.get('last_name', '').strip()
        new_org        = request.POST.get('organization', '').strip()
        new_phone      = request.POST.get('phone_number', '').strip()

        # Validate email
        if new_email:
            try:
                _validate_email(new_email)
            except _ValidationError:
                errors['email'] = 'Enter a valid email address.'
            else:
                if User.objects.filter(email=new_email).exclude(pk=request.user.pk).exists():
                    errors['email'] = 'That email address is already in use.'

        # Validate organisation
        valid_orgs = [c[0] for c in UserProfile.Organization.choices]
        if new_org and new_org not in valid_orgs:
            errors['organization'] = 'Select a valid organisation.'

        if not errors:
            request.user.email      = new_email
            request.user.first_name = new_first
            request.user.last_name  = new_last
            request.user.save()

            profile.organization  = new_org or profile.organization
            profile.phone_number  = new_phone
            profile.save()

            messages.success(request, 'Your account has been updated.')
            return redirect(reverse('dashboard:my_account'))

    return render(request, 'dashboard/my_account.html', {
        'profile': profile,
        'org_choices': UserProfile.Organization.choices,
        'errors': errors,
    })


# ---------------------------------------------------------------------------
# User Management (admin-only)
# ---------------------------------------------------------------------------

@user_passes_test(is_admin)
def user_management(request):
    """Admin user management — searchable/filterable table of all users."""
    qs = User.objects.select_related('profile').order_by('username')

    search     = request.GET.get('q', '').strip()
    role_filter = request.GET.get('role', '').strip()
    org_filter  = request.GET.get('org', '').strip()

    if search:
        qs = qs.filter(
            Q(username__icontains=search) |
            Q(email__icontains=search) |
            Q(first_name__icontains=search) |
            Q(last_name__icontains=search)
        )
    if role_filter:
        qs = qs.filter(profile__role=role_filter)
    if org_filter:
        qs = qs.filter(profile__organization=org_filter)

    return render(request, 'dashboard/user_management.html', {
        'users':        qs,
        'search':       search,
        'role_filter':  role_filter,
        'org_filter':   org_filter,
        'role_choices': UserProfile.Role.choices,
        'org_choices':  UserProfile.Organization.choices,
        'total_count':  User.objects.count(),
    })


def _user_to_dict(user):
    """Serialise a User + UserProfile into a plain dict for JSON responses."""
    profile = user.profile
    return {
        'id':                    user.id,
        'username':              user.username,
        'email':                 user.email,
        'first_name':            user.first_name,
        'last_name':             user.last_name,
        'full_name':             user.get_full_name(),
        'role':                  profile.role,
        'role_display':          profile.get_role_display(),
        'organization':          profile.organization,
        'organization_display':  profile.get_organization_display() if profile.organization else '',
        'phone_number':          profile.phone_number or '',
        'is_active':             user.is_active,
        'date_joined':           user.date_joined.strftime('%b %d, %Y'),
    }


@user_passes_test(is_admin)
def user_create(request):
    """Create a new user account (admin bypass — no PIN/email verification needed)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.core.validators import validate_email as _validate_email_addr
    from django.core.exceptions import ValidationError as _VE

    username   = request.POST.get('username', '').strip()
    email      = request.POST.get('email', '').strip()
    password   = request.POST.get('password', '').strip()
    role       = request.POST.get('role', '').strip()
    org        = request.POST.get('organization', '').strip()
    phone      = request.POST.get('phone_number', '').strip()
    first_name = request.POST.get('first_name', '').strip()
    last_name  = request.POST.get('last_name', '').strip()

    errors = {}
    if not username:
        errors['username'] = 'Username is required.'
    elif User.objects.filter(username=username).exists():
        errors['username'] = 'Username already taken.'

    if not email:
        errors['email'] = 'Email is required.'
    else:
        try:
            _validate_email_addr(email)
        except _VE:
            errors['email'] = 'Enter a valid email address.'
        else:
            if User.objects.filter(email=email).exists():
                errors['email'] = 'Email already in use.'

    if not password or len(password) < 8:
        errors['password'] = 'Password must be at least 8 characters.'

    valid_roles = [c[0] for c in UserProfile.Role.choices]
    if not role or role not in valid_roles:
        errors['role'] = 'Select a valid role.'

    if errors:
        return JsonResponse({'success': False, 'errors': errors}, status=400)

    user = User.objects.create_user(
        username=username,
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        is_active=True,
    )
    profile = user.profile
    profile.role = role
    if org:
        profile.organization = org
    if phone:
        profile.phone_number = phone
    profile.save()

    try:
        from apps.detections.models import AuditLog
        AuditLog.objects.create(
            user=request.user,
            action='user.created',
            object_id=str(user.pk),
            detail={'username': username, 'role': role, 'created_by': request.user.username},
        )
    except Exception:
        pass

    return JsonResponse({'success': True, 'message': f'User "{username}" created successfully.', 'user': _user_to_dict(user)})


@user_passes_test(is_admin)
def user_edit(request, user_id):
    """Edit an existing user's details."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    from django.core.validators import validate_email as _validate_email_addr
    from django.core.exceptions import ValidationError as _VE

    target_user = get_object_or_404(User, pk=user_id)
    profile     = target_user.profile

    email      = request.POST.get('email', '').strip()
    role       = request.POST.get('role', '').strip()
    org        = request.POST.get('organization', '').strip()
    phone      = request.POST.get('phone_number', '').strip()
    first_name = request.POST.get('first_name', '').strip()
    last_name  = request.POST.get('last_name', '').strip()

    errors = {}
    if email:
        try:
            _validate_email_addr(email)
        except _VE:
            errors['email'] = 'Enter a valid email address.'
        else:
            if User.objects.filter(email=email).exclude(pk=target_user.pk).exists():
                errors['email'] = 'That email is already in use.'

    valid_roles = [c[0] for c in UserProfile.Role.choices]
    if role and role not in valid_roles:
        errors['role'] = 'Select a valid role.'

    valid_orgs = [c[0] for c in UserProfile.Organization.choices]
    if org and org not in valid_orgs:
        errors['organization'] = 'Select a valid organisation.'

    if errors:
        return JsonResponse({'success': False, 'errors': errors}, status=400)

    if email:
        target_user.email = email
    target_user.first_name = first_name
    target_user.last_name  = last_name
    target_user.save()

    if role:
        profile.role = role
    if org is not None:
        profile.organization = org
    profile.phone_number = phone
    profile.save()

    try:
        from apps.detections.models import AuditLog
        AuditLog.objects.create(
            user=request.user,
            action='user.edited',
            object_id=str(target_user.pk),
            detail={'username': target_user.username, 'edited_by': request.user.username},
        )
    except Exception:
        pass

    return JsonResponse({'success': True, 'message': f'User "{target_user.username}" updated.', 'user': _user_to_dict(target_user)})


@user_passes_test(is_admin)
def user_toggle_active(request, user_id):
    """Toggle a user's is_active status."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    target_user = get_object_or_404(User, pk=user_id)

    if target_user == request.user:
        return JsonResponse({'success': False, 'error': 'You cannot deactivate your own account.'}, status=400)

    target_user.is_active = not target_user.is_active
    target_user.save()

    action = 'activated' if target_user.is_active else 'deactivated'

    try:
        from apps.detections.models import AuditLog
        AuditLog.objects.create(
            user=request.user,
            action=f'user.{action}',
            object_id=str(target_user.pk),
            detail={'username': target_user.username, 'by': request.user.username},
        )
    except Exception:
        pass

    return JsonResponse({
        'success':   True,
        'is_active': target_user.is_active,
        'message':   f'User "{target_user.username}" has been {action}.',
    })


@user_passes_test(is_admin)
def user_reset_password(request, user_id):
    """Send a password-reset email to the target user."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    target_user = get_object_or_404(User, pk=user_id)

    if not target_user.email:
        return JsonResponse({'success': False, 'error': 'This user has no email address on file.'}, status=400)

    uid   = urlsafe_base64_encode(force_bytes(target_user.pk))
    token = default_token_generator.make_token(target_user)

    reset_url = request.build_absolute_uri(
        reverse('password_reset_confirm', kwargs={'uidb64': uid, 'token': token})
    )

    subject = f'Password Reset — {django_settings.APP_NAME}'
    body    = (
        f'Hi {target_user.get_full_name() or target_user.username},\n\n'
        f'An administrator has requested a password reset for your account.\n\n'
        f'Click the link below to set a new password (valid for 24 hours):\n{reset_url}\n\n'
        f'If you did not expect this email, you can ignore it.\n\n'
        f'— {django_settings.APP_NAME} Team'
    )

    def _send():
        try:
            send_mail(subject, body, django_settings.DEFAULT_FROM_EMAIL, [target_user.email], fail_silently=True)
        except Exception:
            pass

    threading.Thread(target=_send, daemon=True).start()

    return JsonResponse({'success': True, 'message': f'Password reset email sent to {target_user.email}.'})