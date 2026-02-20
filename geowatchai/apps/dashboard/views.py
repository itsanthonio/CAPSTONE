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

        # --- Model performance — use latest ModelRun if available ---
        latest_run = ModelRun.objects.order_by('-created_at').first()
        if latest_run:
            precision = round((latest_run.val_precision or 0) * 100, 1)
            recall    = round((latest_run.val_recall    or 0) * 100, 1)
            iou       = round((latest_run.val_iou       or 0) * 100, 1) if latest_run.val_iou else None
            f1        = round((latest_run.val_f1        or 0) * 100, 1) if latest_run.val_f1 else None
            accuracy  = precision  # proxy — best available single metric
            model_name = f"{latest_run.model_name} {latest_run.model_version}"
        else:
            # Fall back to known best-precision model values
            precision = 72.4
            recall    = 76.7
            iou       = None
            f1        = None
            accuracy  = 72.4
            model_name = "FPN-ResNet50 v1.0"

        # --- Jobs ---
        total_jobs     = Job.objects.count()
        completed_jobs = Job.objects.filter(status='completed').count()
        failed_jobs    = Job.objects.filter(status='failed').count()

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
            'model_accuracy': accuracy,
            'model_name': model_name,
            'precision': precision,
            'recall': recall,
            'iou': iou,
            'f1': f1,
            'total_jobs': total_jobs,
            'completed_jobs': completed_jobs,
            'failed_jobs': failed_jobs,
            'recent_sites': recent_sites,
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
            'model_accuracy': 72.4,
            'model_name': 'FPN-ResNet50 v1.0',
            'precision': 72.4,
            'recall': 76.7,
            'iou': None,
            'f1': None,
            'total_jobs': 0,
            'completed_jobs': 0,
            'failed_jobs': 0,
            'recent_sites': [],
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