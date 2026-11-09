"""Control screens: the audit log and accounting periods (owner only)."""
from decimal import Decimal,InvalidOperation
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect,render
from django.utils.text import slugify
from .models import Audit,Staff
from . import access,permissions,services


def _require(user,code):
    if not permissions.can(user,code):raise PermissionDenied('You do not have access to this page.')


@login_required
def audit_log(request):
    _require(request.user,'view_audit')
    from .views import paginate
    rows=Audit.objects.select_related('user','document','branch').order_by('-created')
    if not request.user.is_superuser:
        places=access.branches(request.user)
        rows=rows.filter(Q(branch__in=places)|Q(document__branch__in=places))
    visible_actions=list(rows.order_by('action').values_list('action',flat=True).distinct()[:60])
    action=request.GET.get('action','').strip();who=request.GET.get('user','').strip();start=request.GET.get('from','').strip();end=request.GET.get('to','').strip()
    if action:rows=rows.filter(action__icontains=action)
    if who:rows=rows.filter(user__username__icontains=who)
    try:
        if start:rows=rows.filter(created__date__gte=start)
        if end:rows=rows.filter(created__date__lte=end)
    except (ValidationError,ValueError):messages.error(request,'Enter valid dates.')
    page,pager_query=paginate(request,rows,50)
    return render(request,'audit_log.html',dict(title='Audit log',rows=page.object_list,page_obj=page,pager_query=pager_query,
        actions=visible_actions))


@login_required
def periods(request):
    """Lock an accounting period: nothing can be posted, edited or cancelled on or before the lock date."""
    _require(request.user,'accounting')
    from datetime import date
    from django.utils import timezone
    from .models import Branch
    shops=access.branches(request.user)
    rows=list(shops.order_by('name'))
    if request.method=='POST':
        branch=get_object_or_404(shops,pk=request.POST.get('branch'))
        reason=(request.POST.get('reason') or '').strip()
        try:
            raw=request.POST.get('lock_through','').strip()
            new=date.fromisoformat(raw) if raw else None
            if new and new>timezone.localdate():raise ValidationError('A period can only be locked up to today.')
            old=branch.lock_through
            moving_back=bool(old and (new is None or new<old))
            if moving_back and not request.user.is_superuser:raise PermissionDenied('Only the owner can reopen a locked period.')
            if moving_back and len(reason)<5:raise ValidationError('Give a reason (at least 5 characters) for reopening a locked period.')
            if new==old:raise ValidationError('That is already the lock date.')
            with transaction.atomic():
                Branch.objects.filter(pk=branch.pk).update(lock_through=new)
                services.audit(request.user,'Reopen period' if moving_back else 'Lock period',None,branch=branch,before=str(old) if old else None,after=str(new) if new else None,reason=reason)
            messages.success(request,f'{branch.name}: '+(f'locked through {new:%d %b %Y}.' if new else 'period lock removed.'))
        except ValueError:messages.error(request,'Enter a valid date.')
        except ValidationError as error:messages.error(request,'; '.join(error.messages))
        return redirect('periods')
    return render(request,'periods.html',dict(title='Accounting periods',rows=rows,today=timezone.localdate(),is_owner=request.user.is_superuser))
