from rest_framework import serializers
from django.contrib.auth.models import User
from .models import UserProfile, InspectorAssignment, Organisation


class OrganisationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Organisation
        fields = ['id', 'name']


class UserProfileSerializer(serializers.ModelSerializer):
    user = serializers.CharField(read_only=True)
    role = serializers.ChoiceField(choices=UserProfile.Role.choices)
    organisation = OrganisationSerializer(read_only=True)
    phone_number = serializers.CharField(max_length=20, allow_blank=True)
    is_available = serializers.BooleanField(read_only=True)

    class Meta:
        model = UserProfile
        fields = ['user', 'role', 'organisation', 'phone_number', 'is_available']


class InspectorSerializer(serializers.ModelSerializer):
    user = serializers.CharField(read_only=True)
    organisation = OrganisationSerializer(read_only=True)

    class Meta:
        model = UserProfile
        fields = ['user', 'role', 'organisation', 'is_available']


class InspectorAssignmentSerializer(serializers.ModelSerializer):
    alert_id = serializers.UUIDField(read_only=True)
    inspector = InspectorSerializer(read_only=True)
    status = serializers.CharField(max_length=20)
    assigned_at = serializers.DateTimeField(read_only=True)
    accepted_at = serializers.DateTimeField(read_only=True)
    completed_at = serializers.DateTimeField(read_only=True)
    notes = serializers.CharField(allow_blank=True)
    
    class Meta:
        model = InspectorAssignment
        fields = ['alert_id', 'inspector', 'status', 'assigned_at', 'accepted_at', 'completed_at', 'notes']
        read_only_fields = ['alert_id', 'assigned_at']
