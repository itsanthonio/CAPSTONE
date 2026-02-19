from django.urls import path
from .views import DashboardView, AlertsView, ModelInsightsView, SettingsView

app_name = 'dashboard'

urlpatterns = [
    path('', DashboardView.as_view(), name='home'),
    path('alerts/', AlertsView.as_view(), name='alerts'),
    path('model-insights/', ModelInsightsView.as_view(), name='model_insights'),
    path('settings/', SettingsView.as_view(), name='settings'),
]
