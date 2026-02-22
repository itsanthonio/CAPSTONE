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
        from django.db.models import Count, Sum, Q
        from collections import defaultdict

        now = timezone.now()
        seven_days_ago    = now - timedelta(days=7)
        fourteen_days_ago = now - timedelta(days=14)
        thirty_days_ago   = now - timedelta(days=30)
        sixty_days_ago    = now - timedelta(days=60)

        # --- Detected sites ---
        total_sites   = DetectedSite.objects.count()
        illegal_sites = DetectedSite.objects.filter(legal_status='illegal').count()
        sites_this_week = DetectedSite.objects.filter(created_at__gte=seven_days_ago).count()

        # --- Alerts ---
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

        # --- High-risk zones ---
        high_risk = DetectedSite.objects.filter(
            Q(recurrence_count__gt=1) | Q(alerts__severity='critical')
        ).distinct().count()

        # --- Total illegal area ---
        area_result  = DetectedSite.objects.filter(legal_status='illegal').aggregate(total=Sum('area_hectares'))
        total_area_ha = round(area_result['total'] or 0, 1)

        # --- Jobs ---
        total_jobs     = Job.objects.count()
        completed_jobs = Job.objects.filter(status='completed').count()
        failed_jobs    = Job.objects.filter(status='failed').count()

        # --- Detection trend: daily counts for last 30 days ---
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

        # --- Top regions ---
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

        # --- Recent sites for activity feed ---
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
        return {
            'total_detected_sites': 0, 'illegal_sites': 0,
            'open_alerts': 0, 'critical_alerts': 0, 'high_alerts': 0,
            'high_risk_zones': 0, 'alerts_this_month': 0, 'total_area_ha': 0,
            'total_jobs': 0, 'completed_jobs': 0, 'failed_jobs': 0,
            'recent_sites': [], 'top_regions': [],
            'trend_labels': [], 'trend_illegal': [], 'trend_legal': [],
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
            'accuracy': 97.9,
            'precision': 73.1,
            'recall': 77.1,
            'f1_score': 75.0,
            'iou': 60.0,
            'dice': 75.0,
            'loss': 0.1246,
        }
        # Confidence distribution from real predictions
        try:
            from apps.detections.models import DetectedSite
            scores = list(DetectedSite.objects.values_list('confidence_score', flat=True))
            bins = [0, 0, 0, 0, 0]  # 50-60, 60-70, 70-80, 80-90, 90-100
            for s in scores:
                pct = s * 100
                if pct < 60:   bins[0] += 1
                elif pct < 70: bins[1] += 1
                elif pct < 80: bins[2] += 1
                elif pct < 90: bins[3] += 1
                else:          bins[4] += 1
            context['conf_bins'] = bins
            context['conf_total'] = len(scores)
        except Exception:
            context['conf_bins'] = [0, 0, 0, 0, 0]
            context['conf_total'] = 0
        return context


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = 'dashboard/settings.html'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['settings'] = settings
        return context