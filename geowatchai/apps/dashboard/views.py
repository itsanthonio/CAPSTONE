from django.shortcuts import render, redirect
from django.contrib.auth import login
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import reverse_lazy
from django.views.generic import CreateView
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
        # Log the user in after successful registration
        login(self.request, self.object)
        return response


class CustomLogoutView(LogoutView):
    next_page = reverse_lazy('dashboard:home')


# Dashboard Views
def dashboard_home(request):
    """Main dashboard view"""
    return render(request, 'dashboard/dashboard.html')


def dashboard_alerts(request):
    """Alerts management view"""
    return render(request, 'dashboard/alerts.html')


def dashboard_model_insights(request):
    """Model insights and analytics view"""
    return render(request, 'dashboard/model_insights.html')


def dashboard_settings(request):
    """Settings view"""
    return render(request, 'dashboard/settings.html')
