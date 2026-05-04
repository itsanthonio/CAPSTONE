import logging

from django.shortcuts import render
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.auth.decorators import login_required, user_passes_test
from django.utils.decorators import method_decorator
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
from django.conf import settings
from apps.accounts.models import UserProfile

logger = logging.getLogger(__name__)


def is_admin(user):
    """Check if user has any admin role (system or agency)."""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role in (
        UserProfile.Role.SYSTEM_ADMIN, UserProfile.Role.AGENCY_ADMIN
    )

@method_decorator(user_passes_test(is_admin), name='dispatch')
class AnalysisView(LoginRequiredMixin, TemplateView):
    template_name = 'analysis/analysis.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'analysis_tools': [
                {
                    'name': 'Change Detection',
                    'description': 'Compare satellite imagery over time to detect changes',
                    'icon': 'compare',
                    'enabled': True
                },
                {
                    'name': 'Risk Assessment',
                    'description': 'Evaluate environmental risk levels for detected areas',
                    'icon': 'warning',
                    'enabled': True
                },
                {
                    'name': 'Conflict Analysis',
                    'description': 'Identify overlaps between legal concessions and illegal activities',
                    'icon': 'conflict',
                    'enabled': True
                },
                {
                    'name': 'Export Reports',
                    'description': 'Generate detailed reports for regulatory action',
                    'icon': 'document',
                    'enabled': True
                }
            ],
            'analysis_results': {
                'total_area_analyzed': '45,230 km²',
                'detections_found': 247,
                'high_risk_areas': 43,
                'legal_conflicts': 28,
                'analysis_date': '2024-12-10'
            }
        })
        return context

def analysis_view(request):
    """Geospatial analysis and tools view — delegates through dispatch so all
    decorators (login_required, user_passes_test) are correctly enforced."""
    return AnalysisView.as_view()(request)

class LiveMapView(LoginRequiredMixin, TemplateView):
    template_name = 'analysis/live_map.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'settings': settings,
            'page_title': 'Live Map',
        })
        return context

@login_required
def run_hls_inference(request):
    """
    Placeholder function for HLS Optical Model inference.
    This will eventually process satellite imagery and return detection results.
    """
    if request.method == 'POST':
        try:
            # Placeholder data - in real implementation, this would:
            # 1. Receive satellite imagery data
            # 2. Process HLS bands through the ML model
            # 3. Return GeoJSON with detected illegal mining sites
            
            response_data = {
                'status': 'success',
                'message': 'HLS inference completed successfully',
                'detections': {
                    'type': 'FeatureCollection',
                    'features': [
                        {
                            'type': 'Feature',
                            'geometry': {
                                'type': 'Polygon',
                                'coordinates': [[[-1.2345, 6.7890], [-1.2340, 6.7890], [-1.2340, 6.7895], [-1.2345, 6.7895], [-1.2345, 6.7890]]]
                            },
                            'properties': {
                                'detection_type': 'illegal_mining',
                                'confidence': 0.94,
                                'area_hectares': 2.5,
                                'detection_date': '2024-01-15T10:30:00Z',
                                'risk_level': 'high'
                            }
                        }
                    ]
                },
                'model_info': {
                    'model_version': '2.0.0',
                    'processing_time': 2.3,
                    'satellite_source': 'Landsat-8',
                    'bands_used': ['B2', 'B3', 'B4', 'B5', 'B6', 'B7']
                }
            }
            
            return JsonResponse(response_data)
            
        except Exception as e:
            logger.error(f'HLS inference failed: {e}')
            return JsonResponse({
                'status': 'error',
                'message': 'HLS inference failed. Check server logs for details.'
            }, status=500)
    
    return JsonResponse({
        'status': 'error',
        'message': 'Only POST method is supported'
    }, status=405)
