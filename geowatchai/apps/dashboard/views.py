from django.shortcuts import render, redirect
from django.contrib.auth import login, authenticate, get_user_model
from django.contrib.auth.views import LoginView, LogoutView
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.tokens import default_token_generator
from django.urls import reverse_lazy, reverse
from django.views.generic import CreateView
from django.utils import timezone
from django.utils.encoding import force_bytes, force_str
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.http import JsonResponse
from django.conf import settings as django_settings
from django.core.mail import send_mail
from django.core.cache import cache
from django.template.loader import render_to_string
from datetime import timedelta, date
from django.contrib import messages
import os
import random
import threading
from .forms import CustomUserCreationForm, RoleBasedLoginForm
from apps.accounts.models import UserProfile

User = get_user_model()


@login_required
def dashboard_router(request):
    """Gatekeeper view that redirects based on user role"""
    try:
        # Check if the user has a profile
        role = request.user.profile.role
        print(f"DEBUG: User {request.user.username} has role: {role}")
    except Exception as e:
        print(f"DEBUG: Profile missing for {request.user.username}: {e}")
        # Create profile if missing
        UserProfile.objects.create(user=request.user, role=UserProfile.Role.ADMIN)
        return redirect('/dashboard/home/')
    
    if role == UserProfile.Role.ADMIN:
        print(f"DEBUG: Redirecting admin {request.user.username} to admin dashboard")
        return redirect('/dashboard/home/')
    elif role == UserProfile.Role.INSPECTOR:
        print(f"DEBUG: Redirecting inspector {request.user.username} to inspector dashboard")
        return redirect('/dashboard/inspector/')
    else:
        print(f"DEBUG: Redirecting non-inspector {request.user.username} to admin dashboard")
        return redirect('/dashboard/home/')


def is_admin(user):
    """Check if user has admin role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.ADMIN


def is_inspector(user):
    """Check if user has inspector role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role == UserProfile.Role.INSPECTOR


def is_inspector_or_admin(user):
    """Check if user has inspector or admin role"""
    return user.is_authenticated and hasattr(user, 'profile') and user.profile.role in (
        UserProfile.Role.ADMIN, UserProfile.Role.INSPECTOR
    )


class CustomLoginView(LoginView):
    form_class = AuthenticationForm
    template_name = 'registration/login.html'
    redirect_authenticated_user = True

    def dispatch(self, request, *args, **kwargs):
        # Strip the next parameter completely to force role-based redirect
        if 'next' in request.GET:
            print(f"DEBUG: Removing next parameter: {request.GET['next']}")
            # Create a mutable copy and remove next
            request.GET = request.GET.copy()
            del request.GET['next']
        return super().dispatch(request, *args, **kwargs)

    def get_redirect_url(self):
        """Override to completely ignore next parameter"""
        return self.get_success_url()

    def get_success_url(self):
        # Completely ignore any 'next' parameter and use role-based redirect
        print(f"DEBUG CustomLoginView: get_success_url called for {self.request.user}")
        if self.request.user.is_authenticated:
            try:
                profile = self.request.user.profile
                print(f"DEBUG CustomLoginView: role={profile.role}")
                if profile.role == UserProfile.Role.INSPECTOR:
                    print(f"DEBUG CustomLoginView: Redirecting to inspector")
                    return '/dashboard/inspector/'
                else:
                    print(f"DEBUG dashboard_home: Role is {profile.role}, showing admin dashboard")
                    return '/dashboard/home/'
            except UserProfile.DoesNotExist:
                # Create profile if missing - default to ADMIN
                print(f"DEBUG CustomLoginView: No profile, creating ADMIN")
                UserProfile.objects.create(user=self.request.user, role=UserProfile.Role.ADMIN)
                return '/dashboard/home/'
        print(f"DEBUG CustomLoginView: User not authenticated, redirecting to home")
        return '/dashboard/home/'

    def form_valid(self, form):
        response = super().form_valid(form)   # calls login(), creates session
        if self.request.POST.get('remember_me'):
            self.request.session.set_expiry(1209600)   # 14 days
        else:
            self.request.session.set_expiry(0)         # ends on browser close
        return response

    def form_invalid(self, form):
        # Give a specific, helpful message when the account is unverified
        username = self.request.POST.get('username', '')
        try:
            u = User.objects.get(username=username)
            if not u.is_active:
                form.add_error(None, 'Please verify your email address before signing in.')
        except User.DoesNotExist:
            pass
        return super().form_invalid(form)


class SignUpView(CreateView):
    form_class = CustomUserCreationForm
    template_name = 'registration/signup.html'
    success_url = '/dashboard/home/'

    def form_valid(self, form):
        # Save user but keep inactive until email is confirmed
        user = form.save(commit=False)
        user.is_active = False
        user.save()

        # Create UserProfile with selected role
        role = form.cleaned_data.get('role', UserProfile.Role.ADMIN)
        organization = form.cleaned_data.get('organization', UserProfile.Organization.OTHER)
        phone_number = form.cleaned_data.get('phone_number', '')

        UserProfile.objects.update_or_create(
            user=user,
            defaults={
                'role': role,
                'organization': organization,
                'phone_number': phone_number
            }
        )

        # Generate activation PIN and send in background
        pin = str(random.randint(100000, 999999))
        cache.set(f'activation_pin_{user.email.lower()}', {'pin': pin, 'user_pk': str(user.pk)}, timeout=86400)
        threading.Thread(
            target=_send_activation_pin_email, args=(user, pin), daemon=True
        ).start()
        return redirect(f'/dashboard/activation-pin/?email={user.email}')


class CustomLogoutView(LogoutView):
    next_page = '/accounts/login/'


def _get_dashboard_stats():
    """Query real stats from DetectedSite, Alert, and ModelRun."""
    try:
        from apps.detections.models import DetectedSite, Alert, ModelRun
        from apps.jobs.models import Job
        from django.db.models import Count, Avg, Sum, Q
        from django.db.models.functions import TruncDate

        now = timezone.now()
        thirty_days_ago = now - timedelta(days=30)
        sixty_days_ago  = now - timedelta(days=60)
        seven_days_ago  = now - timedelta(days=7)
        fourteen_days_ago = now - timedelta(days=14)

        # --- Detected sites ---
        total_sites    = DetectedSite.objects.count()
        illegal_sites  = DetectedSite.objects.filter(legal_status='illegal').count()
        sites_this_week = DetectedSite.objects.filter(
            detection_date__gte=seven_days_ago.date()
        ).count()
        sites_last_week = DetectedSite.objects.filter(
            detection_date__gte=fourteen_days_ago.date(),
            detection_date__lt=seven_days_ago.date()
        ).count()

        # --- Alerts ---
        open_alerts    = Alert.objects.filter(status='open').count()
        critical_alerts = Alert.objects.filter(status='open', severity='critical').count()
        high_alerts    = Alert.objects.filter(status='open', severity='high').count()
        alerts_this_month = Alert.objects.filter(created_at__gte=thirty_days_ago).count()
        alerts_last_month = Alert.objects.filter(
            created_at__gte=sixty_days_ago,
            created_at__lt=thirty_days_ago
        ).count()

        alerts_change_pct = 0
        if alerts_last_month > 0:
            alerts_change_pct = round(
                ((alerts_this_month - alerts_last_month) / alerts_last_month) * 100
            )

        # --- High-risk zones: sites with recurrence > 1 or critical alerts ---
        high_risk = DetectedSite.objects.filter(
            Q(recurrence_count__gt=1) | Q(alerts__severity='critical')
        ).distinct().count()

        # --- Total area ---
        area_result = DetectedSite.objects.filter(
            legal_status='illegal'
        ).aggregate(total=Sum('area_hectares'))
        total_area_ha = round(area_result['total'] or 0, 1)

        # --- Jobs ---
        total_jobs     = Job.objects.count()
        completed_jobs = Job.objects.filter(status='completed').count()
        failed_jobs    = Job.objects.filter(status='failed').count()

        # --- Detection trend: daily counts for last 30 days (Python-level grouping) ---
        from collections import defaultdict
        illegal_by_day = defaultdict(int)
        legal_by_day   = defaultdict(int)
        recent_all = DetectedSite.objects.filter(
            created_at__gte=thirty_days_ago
        ).values('legal_status', 'created_at')
        for site in recent_all:
            day = site['created_at'].date()
            if site['legal_status'] == 'illegal':
                illegal_by_day[day] += 1
            else:
                legal_by_day[day] += 1
        trend_labels, trend_illegal, trend_legal = [], [], []
        for i in range(29, -1, -1):
            d = (now - timedelta(days=i)).date()
            trend_labels.append(d.strftime('%d %b'))
            trend_illegal.append(illegal_by_day.get(d, 0))
            trend_legal.append(legal_by_day.get(d, 0))

        # --- Top regions by detection count (all-time) ---
        top_regions = list(
            DetectedSite.objects
            .filter(region__isnull=False)
            .values('region__name')
            .annotate(
                total=Count('id'),
                illegal=Count('id', filter=Q(legal_status='illegal')),
            )
            .order_by('-total')[:6]
        )
        if top_regions:
            max_total = top_regions[0]['total']
            for r in top_regions:
                r['illegal_pct'] = round((r['illegal'] / r['total']) * 100) if r['total'] > 0 else 0
                r['bar_pct'] = round((r['total'] / max_total) * 100) if max_total > 0 else 0

        # --- Recent sites for activity feed (last 5 by scan time) ---
        recent_sites = list(
            DetectedSite.objects.select_related('region')
            .order_by('-created_at')[:5]
            .values(
                'id', 'detection_date', 'created_at', 'legal_status',
                'confidence_score', 'area_hectares',
                'region__name', 'recurrence_count'
            )
        )
        for s in recent_sites:
            s['confidence_pct'] = round(s['confidence_score'] * 100, 1)
            s['id'] = str(s['id'])

        return {
            'total_detected_sites': total_sites,
            'illegal_sites': illegal_sites,
            'open_alerts': open_alerts,
            'critical_alerts': critical_alerts,
            'high_alerts': high_alerts,
            'high_risk_zones': high_risk,
            'alerts_this_month': alerts_this_month,
            'total_area_ha': total_area_ha,
            'total_jobs': total_jobs,
            'completed_jobs': completed_jobs,
            'failed_jobs': failed_jobs,
            'recent_sites': recent_sites,
            'top_regions': top_regions,
            'trend_labels': trend_labels,
            'trend_illegal': trend_illegal,
            'trend_legal': trend_legal,
            'trends': {
                'sites_change': sites_this_week,
                'alerts_change': alerts_change_pct,
            },
            'has_data': total_sites > 0,
        }
    except Exception:
        # Models not yet migrated or DB empty — return safe defaults
        return {
            'total_detected_sites': 0,
            'illegal_sites': 0,
            'open_alerts': 0,
            'critical_alerts': 0,
            'high_alerts': 0,
            'high_risk_zones': 0,
            'alerts_this_month': 0,
            'total_area_ha': 0,
            'total_jobs': 0,
            'completed_jobs': 0,
            'failed_jobs': 0,
            'recent_sites': [],
            'top_regions': [],
            'trend_labels': [],
            'trend_illegal': [],
            'trend_legal': [],
            'trends': {'sites_change': 0, 'alerts_change': 0},
            'has_data': False,
        }


@login_required
def dashboard_home(request):
    """Admin-only dashboard home with automatic redirect for inspectors"""
    print(f"DEBUG dashboard_home: user={request.user}, authenticated={request.user.is_authenticated}")
    
    if request.user.is_authenticated:
        try:
            profile = request.user.profile
            print(f"DEBUG dashboard_home: profile.role={profile.role}")
            if profile.role == UserProfile.Role.INSPECTOR:
                print(f"DEBUG dashboard_home: Inspector detected, redirecting to inspector dashboard")
                return redirect('/dashboard/inspector/')
        except UserProfile.DoesNotExist:
            print(f"DEBUG dashboard_home: No profile, creating ADMIN profile")
            UserProfile.objects.create(user=request.user, role=UserProfile.Role.ADMIN)
    
    # Any non-inspector gets admin dashboard
    try:
        if request.user.profile.role == UserProfile.Role.INSPECTOR:
            print(f"DEBUG dashboard_home: Inspector redirecting to inspector dashboard")
            return redirect('/dashboard/inspector/')
    except UserProfile.DoesNotExist:
        pass
    
    print(f"DEBUG dashboard_home: Rendering admin dashboard")
    try:
        stats = _get_dashboard_stats()
    except Exception as e:
        # Fallback stats if there's an error
        stats = {
            'total_sites': 0,
            'illegal_sites': 0,
            'sites_this_week': 0,
            'total_alerts': 0,
            'alerts_this_week': 0,
            'pending_assignments': 0,
            'completed_assignments': 0,
            'avg_processing_time': 0,
            'top_regions': [],
            'trend_labels': [],
            'trend_illegal': [],
            'trend_legal': [],
            'trends': {'sites_change': 0, 'alerts_change': 0},
            'has_data': False,
        }
    
    return render(request, 'dashboard/dashboard.html', {'stats': stats})


@user_passes_test(is_admin)
def dashboard_alerts(request):
    """Admin-only alerts view"""
    return render(request, 'dashboard/alerts.html')


@user_passes_test(is_admin)
def dashboard_model_insights(request):
    """Admin-only model insights view — live metrics driven by inspector field reports."""
    import json
    import calendar
    from collections import defaultdict
    from apps.accounts.models import InspectorAssignment
    from apps.detections.models import DetectedSite

    # ── Confidence distribution (all detections) ─────────────────────────────
    scores = list(
        DetectedSite.objects.filter(confidence_score__isnull=False)
        .values_list('confidence_score', flat=True)
    )
    conf_total = len(scores)
    conf_bins = [0, 0, 0, 0, 0]   # 50-60, 60-70, 70-80, 80-90, 90-100
    for s in scores:
        if s >= 0.5:
            idx = min(int((s - 0.5) / 0.1), 4)
            conf_bins[idx] += 1

    # ── All field-verified outcomes, oldest first ─────────────────────────────
    verified = list(
        InspectorAssignment.objects.filter(
            outcome__in=['mining_confirmed', 'false_positive'],
            completed_at__isnull=False,
        ).order_by('completed_at')
    )

    total_verified = len(verified)
    total_tp = sum(1 for a in verified if a.outcome == 'mining_confirmed')
    total_fp = sum(1 for a in verified if a.outcome == 'false_positive')
    has_field_data = total_verified > 0

    # ── Live Precision — rolling last 50 verified ─────────────────────────────
    PRECISION_WINDOW = 50
    recent = verified[-PRECISION_WINDOW:]
    if recent:
        w_tp = sum(1 for a in recent if a.outcome == 'mining_confirmed')
        w_fp = sum(1 for a in recent if a.outcome == 'false_positive')
        live_precision = round(w_tp / (w_tp + w_fp) * 100, 1) if (w_tp + w_fp) else 73.1
    else:
        live_precision = 73.1   # test-set fallback

    # ── Live Accuracy — EMA starting from test-set baseline ──────────────────
    ALPHA = 0.05            # each report carries ~5% weight
    BASE_ACCURACY = 0.979   # FPN-ResNet50 test-set pixel accuracy
    ema = BASE_ACCURACY
    for a in verified:
        ema = ALPHA * (1.0 if a.outcome == 'mining_confirmed' else 0.0) + (1 - ALPHA) * ema
    live_accuracy = round(ema * 100, 1)

    # ── Monthly chart data — last 12 months ──────────────────────────────────
    now = timezone.now()
    months = []
    for i in range(11, -1, -1):
        # Step back month by month
        m = now.month - i
        y = now.year
        while m <= 0:
            m += 12
            y -= 1
        months.append((y, m))

    # Bucket verified assignments into months
    monthly_buckets = defaultdict(lambda: {'tp': 0, 'fp': 0})
    for a in verified:
        key = (a.completed_at.year, a.completed_at.month)
        if a.outcome == 'mining_confirmed':
            monthly_buckets[key]['tp'] += 1
        else:
            monthly_buckets[key]['fp'] += 1

    monthly_labels     = []
    monthly_precision  = []
    monthly_accuracy   = []
    running_ema        = BASE_ACCURACY

    for y, m in months:
        monthly_labels.append(f"{calendar.month_abbr[m]} '{str(y)[2:]}")
        bucket = monthly_buckets.get((y, m), {'tp': 0, 'fp': 0})
        m_tp, m_fp = bucket['tp'], bucket['fp']

        # Apply EMA for every outcome in this month (chronological nudges)
        for _ in range(m_tp):
            running_ema = ALPHA * 1.0 + (1 - ALPHA) * running_ema
        for _ in range(m_fp):
            running_ema = ALPHA * 0.0 + (1 - ALPHA) * running_ema

        monthly_accuracy.append(round(running_ema * 100, 1))
        monthly_precision.append(
            round(m_tp / (m_tp + m_fp) * 100, 1) if (m_tp + m_fp) else None
        )

    return render(request, 'dashboard/model_insights.html', {
        # Live metrics
        'live_precision':   live_precision,
        'live_accuracy':    live_accuracy,
        'has_field_data':   has_field_data,
        'total_verified':   total_verified,
        'total_tp':         total_tp,
        'total_fp':         total_fp,
        # Confidence distribution
        'conf_bins':        conf_bins,
        'conf_total':       conf_total,
        # Chart series (JSON strings)
        'monthly_labels':    json.dumps(monthly_labels),
        'monthly_precision': json.dumps(monthly_precision),
        'monthly_accuracy':  json.dumps(monthly_accuracy),
    })


@user_passes_test(is_inspector_or_admin)
def dashboard_settings(request):
    """Settings view for all authenticated users"""
    return render(request, 'dashboard/settings.html')


@user_passes_test(is_inspector_or_admin)
def inspector_dashboard(request):
    """Inspector-specific dashboard"""
    try:
        from apps.accounts.models import InspectorAssignment
        from apps.detections.models import Alert

        assignments = InspectorAssignment.objects.filter(
            inspector=request.user.profile
        ).order_by('-created_at')

        assignment_data = []
        for assignment in assignments:
            try:
                alert = Alert.objects.select_related('detected_site', 'detected_site__region').get(
                    id=assignment.alert_id
                )
                site = alert.detected_site
                centroid_lng = site.centroid.x if site and site.centroid else None
                centroid_lat = site.centroid.y if site and site.centroid else None
                timelapse = list(
                    site.timelapse_frames.order_by('year').values(
                        'year', 'thumbnail_url', 'acquisition_period'
                    )
                ) if site else []
                assignment_data.append({
                    'assignment': assignment,
                    'alert': alert,
                    'site': site,
                    'centroid_lng': centroid_lng,
                    'centroid_lat': centroid_lat,
                    'timelapse_frames': timelapse,
                    'photo_urls': [
                        django_settings.MEDIA_URL + p for p in (assignment.evidence_photos or [])
                    ],
                })
            except Alert.DoesNotExist:
                assignment_data.append({
                    'assignment': assignment,
                    'alert': None,
                    'site': None,
                    'centroid_lng': None,
                    'centroid_lat': None,
                    'timelapse_frames': [],
                    'photo_urls': [],
                })

        pending_count = sum(1 for d in assignment_data if d['assignment'].status == 'pending')
        verified_count = sum(
            1 for d in assignment_data
            if d['assignment'].status == 'resolved'
            and d['assignment'].outcome in ('mining_confirmed', 'false_positive')
        )
        inconclusive_count = sum(
            1 for d in assignment_data
            if d['assignment'].status == 'resolved'
            and d['assignment'].outcome == 'inconclusive'
        )

        return render(request, 'dashboard/inspector.html', {
            'assignments': assignment_data,
            'pending_count': pending_count,
            'verified_count': verified_count,
            'inconclusive_count': inconclusive_count,
        })
    except Exception as e:
        return render(request, 'dashboard/inspector.html', {
            'assignments': [],
            'pending_count': 0,
            'verified_count': 0,
            'inconclusive_count': 0,
            'error': str(e)
        })


@login_required
def submit_field_report(request, assignment_id):
    """Inspector submits their field verification report (outcome, visit date, notes, photos)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    try:
        from apps.accounts.models import InspectorAssignment
        from apps.detections.models import Alert, DetectedSite

        assignment = InspectorAssignment.objects.get(id=assignment_id, inspector=request.user.profile)

        outcome = request.POST.get('outcome', '').strip()
        visit_date_str = request.POST.get('visit_date', '').strip()
        notes = request.POST.get('notes', '').strip()

        valid_outcomes = [o[0] for o in InspectorAssignment.Outcome.choices]
        if outcome not in valid_outcomes:
            return JsonResponse({'error': 'Invalid outcome'}, status=400)

        # Parse visit date
        visit_date = None
        if visit_date_str:
            try:
                from datetime import date as date_cls
                visit_date = date_cls.fromisoformat(visit_date_str)
            except ValueError:
                return JsonResponse({'error': 'Invalid visit date format'}, status=400)

        # Save uploaded photos
        photos = request.FILES.getlist('evidence_photos')
        photo_paths = list(assignment.evidence_photos or [])
        if photos:
            upload_dir = os.path.join(django_settings.MEDIA_ROOT, 'inspections', str(assignment_id))
            os.makedirs(upload_dir, exist_ok=True)
            for photo in photos:
                safe_name = photo.name.replace(' ', '_')
                dest = os.path.join(upload_dir, safe_name)
                with open(dest, 'wb') as f:
                    for chunk in photo.chunks():
                        f.write(chunk)
                photo_paths.append(f'inspections/{assignment_id}/{safe_name}')

        # Update assignment
        assignment.outcome = outcome
        assignment.visit_date = visit_date
        assignment.notes = notes
        assignment.evidence_photos = photo_paths
        assignment.status = InspectorAssignment.Status.RESOLVED
        if not assignment.completed_at:
            assignment.completed_at = timezone.now()
        assignment.save()

        # Update Alert
        try:
            alert = Alert.objects.get(id=assignment.alert_id)
            alert.status = Alert.AlertStatus.RESOLVED
            alert.resolved_at = timezone.now()
            outcome_label = dict(InspectorAssignment.Outcome.choices).get(outcome, outcome)
            alert.resolution_notes = (
                f"Field report by {request.user.username} on "
                f"{visit_date or 'unknown date'}: {outcome_label}. {notes}"
            )
            alert.save()

            # Update DetectedSite status to match outcome
            site = alert.detected_site
            if outcome == 'mining_confirmed':
                site.status = DetectedSite.Status.CONFIRMED_ILLEGAL
            elif outcome == 'false_positive':
                site.status = DetectedSite.Status.FALSE_POSITIVE
            # inconclusive: leave site status unchanged
            if outcome != 'inconclusive':
                site.reviewed_by = request.user
                site.reviewed_at = timezone.now()
                site.review_notes = notes
                site.save()
        except Alert.DoesNotExist:
            pass

        try:
            import threading
            from apps.notifications.services import send_field_report_received
            threading.Thread(
                target=send_field_report_received,
                args=(assignment, assignment.alert),
                daemon=True
            ).start()
        except Exception:
            pass

        return JsonResponse({
            'success': True,
            'outcome': outcome,
            'message': f'Field report submitted: {dict(InspectorAssignment.Outcome.choices).get(outcome)}'
        })

    except InspectorAssignment.DoesNotExist:
        return JsonResponse({'error': 'Assignment not found'}, status=404)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


# ---------------------------------------------------------------------------
# Email verification helpers & views
# ---------------------------------------------------------------------------

def _send_activation_email(request, user):
    """Send an account activation email to the given user."""
    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    relative_url = reverse('dashboard:activate', kwargs={'uidb64': uid, 'token': token})
    link = request.build_absolute_uri(relative_url)
    subject = "Confirm your GalamseyWatch AI account"
    plain_body = f"Hi {user.username},\n\nClick the link to activate your account:\n{link}\n\nThis link expires in 24 hours."
    html_body = render_to_string('registration/activation_email.html', {'link': link, 'user': user})
    send_mail(
        subject=subject,
        message=plain_body,
        from_email=django_settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=True,
    )


def activation_sent(request):
    return render(request, 'registration/activation_sent.html')


def _send_activation_pin_email(user, pin):
    """Send a 6-digit activation PIN to a newly registered user."""
    subject = "Your GalamseyWatch AI account activation code"
    plain_body = f"Hi {user.username},\n\nYour activation code is: {pin}\n\nThis code expires in 24 hours."
    html_body = render_to_string('registration/activation_pin_email.html', {'pin': pin, 'user': user})
    send_mail(
        subject=subject,
        message=plain_body,
        from_email=django_settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=True,
    )


def activation_pin_entry(request):
    email = (request.GET.get('email') or request.POST.get('email', '')).strip().lower()
    error = None
    if request.method == 'POST':
        pin = request.POST.get('pin', '').strip()
        data = cache.get(f'activation_pin_{email}')
        if data and data['pin'] == pin:
            try:
                user = User.objects.get(pk=data['user_pk'])
                user.is_active = True
                user.save()
                cache.delete(f'activation_pin_{email}')
                messages.success(request, 'Account activated! You can now sign in.')
                return redirect('login')
            except User.DoesNotExist:
                error = 'Something went wrong. Please sign up again.'
        else:
            error = 'That code is incorrect or has expired.'
    return render(request, 'registration/activation_pin.html', {'email': email, 'error': error})


def activate_account(request, uidb64, token):
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    if user is not None and default_token_generator.check_token(user, token):
        user.is_active = True
        user.save()
        messages.success(request, 'Email confirmed! You can now sign in.')
        return redirect('login')

    return render(request, 'registration/activation_invalid.html')


# ---------------------------------------------------------------------------
# PIN-based password reset
# ---------------------------------------------------------------------------

def _send_pin_email(user, pin):
    """Send a 6-digit PIN to the user for password reset."""
    subject = "Your GalamseyWatch AI password reset code"
    plain_body = f"Hi {user.username},\n\nYour password reset code is: {pin}\n\nThis code expires in 10 minutes.\n\nIf you didn't request this, ignore this email."
    html_body = render_to_string('registration/pin_email.html', {'pin': pin, 'user': user})
    send_mail(
        subject=subject,
        message=plain_body,
        from_email=django_settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        html_message=html_body,
        fail_silently=True,
    )


def password_reset_request(request):
    if request.method == 'POST':
        email = request.POST.get('email', '').strip().lower()
        try:
            user = User.objects.get(email__iexact=email, is_active=True)
            pin = str(random.randint(100000, 999999))
            cache.set(f'pwd_reset_{email}', {'pin': pin, 'user_pk': str(user.pk)}, timeout=600)
            threading.Thread(target=_send_pin_email, args=(user, pin), daemon=True).start()
        except User.DoesNotExist:
            pass  # Don't reveal if email exists
        return redirect(f'/accounts/password_reset/pin/?email={email}')
    return render(request, 'registration/password_reset_form.html')


def password_reset_pin_entry(request):
    email = (request.GET.get('email') or request.POST.get('email', '')).lower()
    error = None
    if request.method == 'POST':
        pin = request.POST.get('pin', '').strip()
        data = cache.get(f'pwd_reset_{email}')
        if data and data['pin'] == pin:
            request.session['pwd_reset_user_pk'] = data['user_pk']
            cache.delete(f'pwd_reset_{email}')
            return redirect('/accounts/password_reset/new/')
        else:
            error = 'That code is incorrect or has expired. Please try again.'
    return render(request, 'registration/password_reset_pin.html', {'email': email, 'error': error})


def password_reset_new_password(request):
    user_pk = request.session.get('pwd_reset_user_pk')
    if not user_pk:
        return redirect('/accounts/password_reset/')
    try:
        user = User.objects.get(pk=user_pk)
    except User.DoesNotExist:
        return redirect('/accounts/password_reset/')

    error = None
    if request.method == 'POST':
        p1 = request.POST.get('new_password1', '')
        p2 = request.POST.get('new_password2', '')
        if not p1:
            error = 'Please enter a new password.'
        elif p1 != p2:
            error = 'Passwords do not match.'
        elif len(p1) < 8:
            error = 'Password must be at least 8 characters.'
        else:
            user.set_password(p1)
            user.save()
            del request.session['pwd_reset_user_pk']
            messages.success(request, 'Password changed! You can now sign in.')
            return redirect('login')

    return render(request, 'registration/password_reset_new.html', {'error': error})