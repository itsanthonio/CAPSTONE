from django.urls import path
from . import views


app_name = 'accounts'

urlpatterns = [
    path('inspectors/', views.inspector_list, name='inspector_list'),
    path('assignments/', views.create_assignment, name='create_assignment'),
    path('assignments/my/', views.inspector_assignments, name='inspector_assignments'),
    path('availability/', views.update_availability, name='update_availability'),
]
