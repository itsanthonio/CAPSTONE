from django.urls import path
from . import views

app_name = 'dashboard'

urlpatterns = [
    # Authentication URLs
    path('signup/', views.SignUpView.as_view(), name='signup'),
    
    # Dashboard URLs (to be implemented)
    path('', views.dashboard_home, name='home'),
    path('alerts/', views.dashboard_alerts, name='alerts'),
    path('model-insights/', views.dashboard_model_insights, name='model_insights'),
    path('settings/', views.dashboard_settings, name='settings'),
]
