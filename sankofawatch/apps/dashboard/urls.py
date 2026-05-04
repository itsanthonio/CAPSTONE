from django.urls import path, include
from . import views

app_name = 'dashboard'

urlpatterns = [
    # Authentication URLs
    path('signup/', views.SignUpView.as_view(), name='signup'),
    path('login/', views.CustomLoginView.as_view(), name='login'),
    path('logout/', views.CustomLogoutView.as_view(), name='logout'),
    # Email verification
    path('activation-sent/', views.activation_sent, name='activation_sent'),
    path('activation-pin/', views.activation_pin_entry, name='activation_pin_entry'),
    path('activation-pin/resend/', views.resend_activation_pin, name='resend_activation_pin'),
    path('accounts/activate/<uidb64>/<token>/', views.activate_account, name='activate'),
    
    # Gatekeeper router - handles role-based redirection
    path('', views.dashboard_router, name='dashboard_router'),
    
    # Dashboard URLs
    path('home/', views.dashboard_home, name='home'),
    path('admin/', views.system_admin_dashboard, name='admin_home'),
    path('organisations/', views.organisation_management, name='organisation_management'),
    path('api/chart-data/', views.dashboard_chart_data, name='chart_data'),
    path('api/kpis/', views.dashboard_kpis, name='kpis'),
    path('alerts/', views.dashboard_alerts, name='alerts'),
    path('report/', views.dashboard_report, name='report'),
    path('report/pdf/', views.dashboard_report_pdf, name='report_pdf'),
    path('audit/', views.dashboard_audit, name='audit'),
    path('model-insights/', views.dashboard_model_insights, name='model_insights'),
    path('settings/', views.dashboard_settings, name='settings'),
    path('inspector/', views.inspector_dashboard, name='inspector'),
    path('assignment/<uuid:assignment_id>/report/', views.submit_field_report, name='submit_field_report'),
    path('account/', views.my_account, name='my_account'),
    path('regions/', views.region_list, name='region_list'),
    path('regions/<uuid:region_id>/', views.region_detail, name='region_detail'),

    # User management (admin-only)
    path('users/', views.user_management, name='user_management'),
    path('users/create/', views.user_create, name='user_create'),
    path('users/<int:user_id>/edit/', views.user_edit, name='user_edit'),
    path('users/<int:user_id>/toggle-active/', views.user_toggle_active, name='user_toggle_active'),
    path('users/<int:user_id>/reset-password/', views.user_reset_password, name='user_reset_password'),

    # Include accounts API URLs
    path('api/', include('apps.accounts.urls')),
]
