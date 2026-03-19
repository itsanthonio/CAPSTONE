from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.contrib.auth.decorators import login_required
from .models import UserProfile, InspectorAssignment
from .serializers import UserProfileSerializer, InspectorSerializer, InspectorAssignmentSerializer
from apps.detections.models import Alert


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inspector_list(request):
    """Get list of available inspectors, scoped to the requester's org for agency admins."""
    try:
        qs = UserProfile.objects.filter(
            role=UserProfile.Role.INSPECTOR,
            is_available=True,
        ).select_related('user', 'organisation').order_by('user__username')

        # Agency admins only see inspectors from their own org
        profile = request.user.profile
        if profile.role == UserProfile.Role.AGENCY_ADMIN:
            qs = qs.filter(organisation=profile.organisation)

        serializer = InspectorSerializer(qs, many=True)
        return Response(serializer.data)
    except Exception as e:
        return Response({'error': str(e), 'inspectors': []}, status=500)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_assignment(request):
    """Create a new inspector assignment"""
    alert_id = request.data.get('alert_id')
    inspector_username = request.data.get('inspector')  # Changed from inspector_id to inspector
    notes = request.data.get('notes', '')
    
    if not alert_id or not inspector_username:
        return Response({
            'success': False,
            'error': 'alert_id and inspector are required'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        # Get inspector by username instead of ID
        inspector = UserProfile.objects.get(
            user__username=inspector_username,
            role=UserProfile.Role.INSPECTOR
        )
        
        # Check if inspector is available
        if not inspector.is_available:
            return Response({
                'success': False,
                'error': 'Inspector is not available for assignment'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Check max pending assignments
        from django.conf import settings as _django_settings
        max_pending = getattr(_django_settings, 'INSPECTOR_MAX_PENDING_ASSIGNMENTS', 3)
        current_pending = InspectorAssignment.objects.filter(
            inspector=inspector,
            status=InspectorAssignment.Status.PENDING,
        ).count()
        if current_pending >= max_pending:
            return Response({
                'success': False,
                'error': f'Inspector already has {current_pending} pending assignment(s) (maximum {max_pending}).'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Compute SLA due date
        from datetime import timedelta
        from django.utils import timezone as _tz
        sla_days = getattr(_django_settings, 'INSPECTOR_SLA_DAYS', 5)
        due_date = (_tz.now() + timedelta(days=sla_days)).date()

        # Create assignment
        assignment = InspectorAssignment.objects.create(
            alert_id=alert_id,
            inspector=inspector,
            status=InspectorAssignment.Status.PENDING,
            notes=notes,
            due_date=due_date,
        )
        
        # Update the alert status to dispatched
        alert = None
        try:
            alert = Alert.objects.get(id=alert_id)
            alert.status = 'dispatched'
            alert.assigned_to = inspector.user
            alert.save()
        except Alert.DoesNotExist:
            pass  # Alert might not exist, but assignment is still created

        # Audit log
        try:
            from apps.detections.models import AuditLog
            AuditLog.objects.create(
                user=request.user if request.user.is_authenticated else None,
                action='alert.assigned',
                object_id=str(alert_id),
                detail={'inspector': inspector_username, 'assignment_id': str(assignment.id)},
            )
        except Exception:
            pass

        try:
            import threading
            from apps.notifications.services import send_new_assignment
            threading.Thread(
                target=send_new_assignment, args=(assignment, alert), daemon=True
            ).start()
        except Exception:
            pass

        serializer = InspectorAssignmentSerializer(assignment)
        return Response({
            'success': True,
            'assignment': serializer.data
        }, status=status.HTTP_201_CREATED)
    
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def inspector_assignments(request):
    """Get assignments for current inspector"""
    try:
        profile = request.user.profile
        if profile.role != UserProfile.Role.INSPECTOR:
            return Response({
                'error': 'Only inspectors can view their assignments'
            }, status=status.HTTP_403_FORBIDDEN)
        
        assignments = InspectorAssignment.objects.filter(
            inspector=profile
        ).select_related('alert', 'inspector__user').order_by('-created_at')
        
        serializer = InspectorAssignmentSerializer(assignments, many=True)
        return Response(serializer.data)
    
    except UserProfile.DoesNotExist:
        return Response({
            'error': 'Inspector profile not found'
        }, status=status.HTTP_404_NOT_FOUND)


@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def delete_assignment(request, assignment_id):
    """Delete an assignment (inspector only - can only delete their own assignments)"""
    try:
        profile = request.user.profile
        if profile.role != UserProfile.Role.INSPECTOR:
            return Response({
                'error': 'Only inspectors can delete assignments'
            }, status=status.HTTP_403_FORBIDDEN)
        
        # Try to convert assignment_id to UUID, if it fails treat as string
        try:
            from django.core.exceptions import ValidationError
            from django.core.validators import validate_uuid
            validate_uuid(assignment_id)
            # If it's a valid UUID, use it directly
            assignment = InspectorAssignment.objects.get(
                id=assignment_id,
                inspector=profile  # Ensure inspector can only delete their own assignments
            )
        except (ValidationError, InspectorAssignment.DoesNotExist):
            # If UUID validation fails or assignment not found, try as integer
            try:
                assignment = InspectorAssignment.objects.get(
                    id=int(assignment_id),
                    inspector=profile
                )
            except (ValueError, InspectorAssignment.DoesNotExist):
                return Response({
                    'error': 'Assignment not found or you do not have permission to delete it'
                }, status=status.HTTP_404_NOT_FOUND)
        
        # Get the alert to unassign the inspector
        alert = assignment.alert
        alert.assigned_to = None
        alert.save()
        
        # Delete the assignment
        assignment.delete()
        
        return Response({
            'success': True,
            'message': 'Assignment deleted successfully'
        })
    
    except InspectorAssignment.DoesNotExist:
        return Response({
            'error': 'Assignment not found or you do not have permission to delete it'
        }, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_user_preferences(request):
    """Update user preferences"""
    try:
        from apps.accounts.models import UserPreferences
        
        preferences, created = UserPreferences.objects.get_or_create(user=request.user)
        
        # Update theme and display settings
        if 'theme' in request.data:
            preferences.theme = request.data['theme']
        if 'layout' in request.data:
            preferences.layout = request.data['layout']
        if 'font_size' in request.data:
            preferences.font_size = request.data['font_size']
        if 'high_contrast' in request.data:
            preferences.high_contrast = request.data['high_contrast']
        
        # Update notification settings
        if 'email_notifications' in request.data:
            preferences.email_notifications = request.data['email_notifications']
        if 'quiet_hours_enabled' in request.data:
            preferences.quiet_hours_enabled = request.data['quiet_hours_enabled']
        if 'quiet_hours_start' in request.data:
            preferences.quiet_hours_start = request.data['quiet_hours_start']
        if 'quiet_hours_end' in request.data:
            preferences.quiet_hours_end = request.data['quiet_hours_end']
        if 'timezone' in request.data:
            preferences.timezone = request.data['timezone']
        if 'language' in request.data:
            preferences.language = request.data['language']
        if 'critical_alerts_override' in request.data:
            preferences.critical_alerts_override = request.data['critical_alerts_override']
        
        # Update dashboard settings
        if 'show_alerts_widget' in request.data:
            preferences.show_alerts_widget = request.data['show_alerts_widget']
        if 'show_assignments_widget' in request.data:
            preferences.show_assignments_widget = request.data['show_assignments_widget']
        if 'show_reports_widget' in request.data:
            preferences.show_reports_widget = request.data['show_reports_widget']
        if 'show_audit_widget' in request.data:
            preferences.show_audit_widget = request.data['show_audit_widget']
        if 'default_page' in request.data:
            preferences.default_page = request.data['default_page']
        
        # Update privacy settings
        if 'activity_visibility' in request.data:
            preferences.activity_visibility = request.data['activity_visibility']
        if 'location_sharing' in request.data:
            preferences.location_sharing = request.data['location_sharing']
        
        # Update mobile settings
        if 'mobile_push_notifications' in request.data:
            preferences.mobile_push_notifications = request.data['mobile_push_notifications']
        if 'mobile_offline_sync' in request.data:
            preferences.mobile_offline_sync = request.data['mobile_offline_sync']
        if 'mobile_theme' in request.data:
            preferences.mobile_theme = request.data['mobile_theme']
        
        preferences.save()
        
        return Response({
            'success': True,
            'message': 'Preferences updated successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def update_availability(request):
    """Update inspector availability status"""
    try:
        profile = request.user.profile
        if profile.role != UserProfile.Role.INSPECTOR:
            return Response({
                'error': 'Only inspectors can update their availability'
            }, status=status.HTTP_403_FORBIDDEN)
        
        is_available = request.data.get('is_available', True)
        profile.is_available = is_available
        profile.save()
        
        return Response({
            'success': True,
            'is_available': is_available
        })
    
    except UserProfile.DoesNotExist:
        return Response({
            'error': 'Inspector profile not found'
        }, status=status.HTTP_404_NOT_FOUND)
