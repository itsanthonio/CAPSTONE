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
    """Get list of available inspectors"""
    try:
        inspectors = UserProfile.objects.filter(
            role=UserProfile.Role.INSPECTOR,
            is_available=True
        ).select_related('user').order_by('user__username')
        
        # Debug: Log the count of inspectors found
        print(f"Found {inspectors.count()} inspectors")
        
        serializer = InspectorSerializer(inspectors, many=True)
        return Response(serializer.data)
    except Exception as e:
        print(f"Error in inspector_list: {e}")
        return Response({
            'error': str(e),
            'inspectors': []
        }, status=500)


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
        
        # Create assignment
        assignment = InspectorAssignment.objects.create(
            alert_id=alert_id,
            inspector=inspector,
            status=InspectorAssignment.Status.PENDING,
            notes=notes
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
