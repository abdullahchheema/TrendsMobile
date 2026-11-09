"""Discount limits and manager overrides.

A role has a discount limit (Role.discount_max_pct). A sale above it, or below a phone's minimum price, needs a manager:
either the manager posts it, or the manager types their staff code and PIN at the counter (a one-off override that is
audited). PINs are stored hashed, never logged, and wrong guesses lock that manager's PIN for a few minutes."""
from decimal import Decimal
from django.contrib.auth.hashers import check_password
from django.core.cache import cache
from django.core.exceptions import PermissionDenied,ValidationError
from . import access,permissions
from .models import Staff

MAX_TRIES=5
LOCK_SECONDS=10*60


def verify(branch,approver_code,pin):
    """Return the Staff member whose code+PIN authorise an override in this shop, or raise ValidationError."""
    code=(approver_code or '').strip()
    if not code or not pin:raise ValidationError('Enter the manager code and PIN to approve this.')
    key='override-fail:'+code.lower()
    if cache.get(key,0)>=MAX_TRIES:raise ValidationError('This manager PIN is locked for a few minutes after repeated wrong attempts.')
    person=Staff.objects.filter(code__iexact=code,branch=branch,enabled=True).select_related('role').first()
    ok=bool(person and person.approval_pin and check_password(str(pin),person.approval_pin) and ('discount_override' in permissions.staff_permissions(person) or person.manager))
    if not ok:
        cache.add(key,0,LOCK_SECONDS)
        try:cache.incr(key)
        except ValueError:cache.set(key,1,LOCK_SECONDS)
        raise ValidationError('Manager code or PIN is not valid for this shop.')
    cache.delete(key)
    return person


def apply(doc,user,approver_code,pin,reason=''):
    """Verify an override and record who approved it on the document (the PIN itself is never stored)."""
    from . import services
    person=verify(doc.branch,approver_code,pin)
    doc.data['override']=dict(approved_by=person.name,code=person.code,reason=(reason or '')[:200])
    doc.save()
    services.audit(user,'Manager override',doc,approved_by=person.code,reason=reason or '')
    return person


def limit_for(user):
    """Discount limit in percent for a user: owners and managers are unlimited; role staff use their role's limit; legacy
    staff without a role keep the previous behaviour (no percentage cap)."""
    person=access.staff(user)
    if person is None or person.manager or not person.role_id:return Decimal(100)
    return Decimal(person.role.discount_max_pct)


def check_sale(doc,totals):
    """Raise PermissionDenied if the invoice discount exceeds the poster's limit and no valid override is attached.
    Returns True when an approved override covers this invoice."""
    approved=bool(doc.data.get('override',{}).get('approved_by'))
    subtotal=Decimal(totals['subtotal']);discount=Decimal(totals['discount'])
    if subtotal<=0 or discount<=0:return approved
    pct=(discount*100/subtotal).quantize(Decimal('0.01'))
    cap=limit_for(doc.posted_by)
    if pct>cap and not approved:
        raise PermissionDenied(f'A discount of {pct}% is above your limit of {cap}%. A manager can approve it: enter their manager code and PIN under Manager override.')
    return approved
