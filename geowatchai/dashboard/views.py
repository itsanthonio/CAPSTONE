from django.shortcuts import render
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings
from django.utils import timezone
from datetime import timedelta
from apps.results.models import Result
from apps.jobs.models import Job
import json


def _get_stats():
    try:
        from apps.detections.models import DetectedSite, Alert
        from django.db.models import Sum, Q

        now = timezone.now()
        seven_days_ago    = now - timedelta(days=7)
        fourteen_days_ago = now - timedelta(days=14)
        thirty_days_ago   = now - timedelta(days=30)
        sixty_days_ago    = now - timedelta(days=60)

        total_sites   = DetectedSite.objects.count()
        illegal_sites = DetectedSite.objects.filter(legal_status='illegal').count()

        sites_this_week = DetectedSite.objects.filter(detection_date__gte=seven_days_ago.date()).count()

        open_alerts     = Alert.objects.filter(status='open').count()
        critical_alerts = Alert.objects.filter(status='open', severity='critical').count()
        high_alerts     = Alert.objects.filter(status='open', severity='high').count()
        alerts_this_month = Alert.objects.filter(created_at__gte=thirty_days_ago).count()
        alerts_last_month = Alert.objects.filter(
            created_at__gte=sixty_days_ago, created_at__lt=thirty_days_ago
        ).count()
        alerts_change_pct = 0
        if alerts_last_month > 0:
            alerts_change_pct = round(((alerts_this_month - alerts_last_month) / alerts_last_month) * 100)

        high_risk = DetectedSite.objects.filter(
            Q(recurrence_count__gt=1) | Q(alerts__severity='critical')
        ).distinct().count()

        area_result  = DetectedSite.objects.filter(legal_status='illegal').aggregate(total=Sum('area_hectares'))
        total_area_ha = round(area_result['total'] or 0, 1)

        recent_sites = list(
            DetectedSite.objects.select_related('region')
            .order_by('-detection_date')[:5]
            .values('id', 'detection_date', 'legal_status', 'confidence_score', 'area_hectares', 'region__name')
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
            'precision': 72.4,
            'recall': 76.7,
            'model_name': 'FPN-ResNet50 v1.0',
            'recent_sites': recent_sites,
            'trends': {
                'sites_change': sites_this_week,
                'alerts_change': alerts_change_pct,
            },
            'has_data': total_sites > 0,
        }
    except Exception:
        return {
            'total_detected_sites': 0, 'illegal_sites': 0,
            'open_alerts': 0, 'critical_alerts': 0, 'high_alerts': 0,
            'high_risk_zones': 0, 'alerts_this_month': 0, 'total_area_ha': 0,
            'precision': 72.4, 'recall': 76.7, 'model_name': 'FPN-ResNet50 v1.0',
            'recent_sites': [],
            'trends': {'sites_change': 0, 'alerts_change': 0},
            'has_data': False,
        }


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/dashboard.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['stats'] = _get_stats()
        context['settings'] = settings
        return context


class AlertsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/alerts.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['settings'] = settings
        return context


class ModelInsightsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/model_insights.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['settings'] = settings
        context['model_metrics'] = {
            'accuracy': 72.4, 'precision': 72.4, 'recall': 76.7, 'f1_score': 74.5
        }
        return context


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['settings'] = settings
        return context