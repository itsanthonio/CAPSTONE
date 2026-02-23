from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from apps.accounts.models import UserProfile


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, help_text='Required. Enter a valid email address.')
    role = forms.ChoiceField(
        choices=UserProfile.Role.choices,
        required=True,
        widget=forms.RadioSelect,
        initial=UserProfile.Role.ADMIN
    )
    organization = forms.ChoiceField(
        choices=UserProfile.Organization.choices,
        required=True,
        initial=UserProfile.Organization.OTHER
    )
    phone_number = forms.CharField(max_length=20, required=False)
    
    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2', 'role', 'organization', 'phone_number')
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'placeholder': 'Choose a username'})
        self.fields['email'].widget.attrs.update({'placeholder': 'Enter your email'})
        self.fields['password1'].widget.attrs.update({'placeholder': 'Create a password'})
        self.fields['password2'].widget.attrs.update({'placeholder': 'Confirm your password'})
        self.fields['role'].widget.attrs.update({'class': 'space-y-2'})
        self.fields['organization'].widget.attrs.update({'placeholder': 'Select your organization'})
        self.fields['phone_number'].widget.attrs.update({'placeholder': 'Enter phone number'})
        
        # Remove help text for cleaner UI
        for fieldname in ['username', 'password1', 'password2']:
            self.fields[fieldname].help_text = None


class RoleBasedLoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(
        choices=[
            ('admin', 'Admin'),
            ('inspector', 'Inspector'),
        ],
        required=False,
        widget=forms.RadioSelect,
        help_text="Select your role for this session"
    )
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'placeholder': 'Enter your username'})
        self.fields['password'].widget.attrs.update({'placeholder': 'Enter your password'})
        self.fields['role'].widget.attrs.update({'class': 'space-y-2'})
