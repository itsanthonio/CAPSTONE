from django.shortcuts import render
from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

class AlertsView(LoginRequiredMixin, TemplateView):
    template_name = 'alerts/alerts.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'alerts': [
                {
                    'id': 'ALT-001',
                    'timestamp': '2024-12-10 14:23',
                    'location': 'Ashanti Region, Obuasi',
                    'confidence': 97.3,
                    'status': 'new',
                    'type': 'Mining Expansion',
                    'risk_level': 'high'
                },
                {
                    'id': 'ALT-002',
                    'timestamp': '2024-12-10 13:45',
                    'location': 'Western Region, Tarkwa',
                    'confidence': 89.1,
                    'status': 'investigating',
                    'type': 'Vegetation Clearance',
                    'risk_level': 'moderate'
                },
                {
                    'id': 'ALT-003',
                    'timestamp': '2024-12-10 12:30',
                    'location': 'Eastern Region, Kibi',
                    'confidence': 76.4,
                    'status': 'investigating',
                    'type': 'Water Contamination',
                    'risk_level': 'high'
                },
                {
                    'id': 'ALT-004',
                    'timestamp': '2024-12-10 11:15',
                    'location': 'Central Region, Cape Coast',
                    'confidence': 92.8,
                    'status': 'resolved',
                    'type': 'Equipment Detection',
                    'risk_level': 'moderate'
                },
                {
                    'id': 'ALT-005',
                    'timestamp': '2024-12-10 10:00',
                    'location': 'Greater Accra, Tema',
                    'confidence': 84.2,
                    'status': 'new',
                    'type': 'Road Construction',
                    'risk_level': 'low'
                }
            ],
            'filters': {
                'date_range': {
                    'start': '2024-12-01',
                    'end': '2024-12-10'
                },
                'alert_types': [
                    {'value': 'mining', 'label': 'Mining Activity', 'count': 45},
                    {'value': 'vegetation', 'label': 'Vegetation Clearance', 'count': 23},
                    {'value': 'water', 'label': 'Water Contamination', 'count': 18},
                    {'value': 'equipment', 'label': 'Equipment Detection', 'count': 12},
                    {'value': 'road', 'label': 'Road Construction', 'count': 8}
                ],
                'statuses': [
                    {'value': 'new', 'label': 'New', 'count': 23},
                    {'value': 'investigating', 'label': 'Investigating', 'count': 18},
                    {'value': 'resolved', 'label': 'Resolved', 'count': 45}
                ],
                'min_confidence': 70
            }
        })
        return context

def alerts_view(request):
    """Alerts and notifications management view"""
    
    view = AlertsView()
    return view.get(request)
