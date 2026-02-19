from django.shortcuts import render
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.conf import settings

class DataUploadsView(LoginRequiredMixin, TemplateView):
    template_name = 'uploads/data_uploads.html'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context.update({
            'settings': settings,
            'page_title': 'Data Uploads',
        })
        return context
