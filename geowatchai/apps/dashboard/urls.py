from django.urls import path, include
from . import views

app_name = 'dashboard'

urlpatterns = [
    # Authentication URLs
    path('signup/', views.SignUpView.as_view(), name='signup'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    
    # Gatekeeper router - handles role-based redirection
    path('', views.dashboard_router, name='dashboard_router'),
    
    # Dashboard URLs
    path('home/', views.dashboard_home, name='home'),
    path('alerts/', views.dashboard_alerts, name='alerts'),
    path('model-insights/', views.dashboard_model_insights, name='model_insights'),
    path('settings/', views.dashboard_settings, name='settings'),
    path('inspector/', views.inspector_dashboard, name='inspector'),
    path('assignment/<uuid:assignment_id>/status/', views.update_assignment_status, name='update_assignment_status'),
    
    # Include accounts API URLs
    path('api/', include('apps.accounts.urls')),
]
