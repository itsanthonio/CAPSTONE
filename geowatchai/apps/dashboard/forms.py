from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User
from apps.accounts.models import UserProfile, Organisation


class CustomUserCreationForm(UserCreationForm):
    email = forms.EmailField(required=True, help_text='Required. Enter a valid email address.')
    organisation = forms.ModelChoiceField(
        queryset=Organisation.objects.all(),
        required=False,
        empty_label='Select your organisation',
    )
    phone_number = forms.CharField(max_length=20, required=False)

    class Meta:
        model = User
        fields = ('username', 'email', 'password1', 'password2', 'organisation', 'phone_number')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['username'].widget.attrs.update({'placeholder': 'Choose a username'})
        self.fields['email'].widget.attrs.update({'placeholder': 'Enter your email'})
        self.fields['password1'].widget.attrs.update({'placeholder': 'Create a password'})
        self.fields['password2'].widget.attrs.update({'placeholder': 'Confirm your password'})
        self.fields['phone_number'].widget.attrs.update({'placeholder': 'Enter phone number'})

        # Remove help text for cleaner UI
        for fieldname in ['username', 'password1', 'password2']:
            self.fields[fieldname].help_text = None


class RoleBasedLoginForm(forms.Form):
    username = forms.CharField(max_length=150)
    password = forms.CharField(widget=forms.PasswordInput)
    role = forms.ChoiceField(
        choices=[
            ('agency_admin', 'Agency Administrator'),
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
