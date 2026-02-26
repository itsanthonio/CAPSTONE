import json
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from django.contrib.auth.decorators import login_required


@login_required
def notification_list(request):
    """Return the 20 most recent notifications for the current user + unread count."""
    from apps.notifications.models import NotificationInbox

    notifs = list(
        NotificationInbox.objects
        .filter(user=request.user)
        .values('id', 'kind', 'title', 'body', 'link', 'is_read', 'created_at')[:20]
    )
    unread = NotificationInbox.objects.filter(user=request.user, is_read=False).count()

    return JsonResponse({
        'unread': unread,
        'items': [
            {
                'id':         str(n['id']),
                'kind':       n['kind'],
                'title':      n['title'],
                'body':       n['body'],
                'link':       n['link'],
                'is_read':    n['is_read'],
                'created_at': n['created_at'].strftime('%d %b, %H:%M'),
            }
            for n in notifs
        ],
    })


@login_required
@require_POST
def notification_mark_read(request):
    """Mark notifications as read.  Pass {"ids": [...]} to target specific rows,
    or omit to mark all unread notifications for the current user."""
    from apps.notifications.models import NotificationInbox

    try:
        body = json.loads(request.body or '{}')
    except (json.JSONDecodeError, ValueError):
        body = {}

    ids = body.get('ids')
    qs = NotificationInbox.objects.filter(user=request.user, is_read=False)
    if ids:
        qs = qs.filter(id__in=ids)
    updated = qs.update(is_read=True)
    return JsonResponse({'ok': True, 'updated': updated})
