from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy
from django.views.generic import CreateView
from django.utils import timezone
from datetime import timedelta
from .forms import CustomUserCreationForm


class CustomLoginView(LoginView):
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def get_success_url(self):
        return reverse_lazy('dashboard:home')


class SignUpView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'registration/signup.html'
    success_url = reverse_lazy('dashboard:home')

    def form_valid(self, form):
        response = super().form_valid(form)
        login(self.request, self.object)
        return response


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('dashboard:home')


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
    stats = _get_dashboard_stats()
    return render(request, 'dashboard/dashboard.html', {'stats': stats})


def dashboard_alerts(request):
    return render(request, 'dashboard/alerts.html')


def dashboard_model_insights(request):
    return render(request, 'dashboard/model_insights.html')


def dashboard_settings(request):
    return render(request, 'dashboard/settings.html')