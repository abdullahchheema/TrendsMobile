"""Resolve typed customer names without losing account identity or retry safety."""
import hashlib
from django.core.exceptions import ValidationError
from django.db import connection,transaction
from django.db.models import Q
from .models import Party
from . import access


def clean_name(value):
    if not isinstance(value,str):raise ValidationError('Enter a customer name.')
    name=' '.join(value.split())
    if not name:raise ValidationError('Enter a customer name.')
    if len(name)>180:raise ValidationError('Customer names must be 180 characters or fewer.')
    if any(ord(char)<32 or ord(char)==127 for char in name):raise ValidationError('Enter a valid customer name.')
    return name


def find(name,selected=None,kind="Customer",branch=None):
    """The shop's own record with this name. A legacy record that belongs to no shop yet is also found (and adopted on first use)."""
    key=clean_name(name).casefold()
    pool=Party.objects.filter(kind=kind)
    pool=pool.filter(Q(branch=branch)|Q(branch__isnull=True)) if branch is not None else pool.filter(branch__isnull=True)
    matches=[party for party in pool.only('id','name','enabled','branch').iterator() if ' '.join(party.name.split()).casefold()==key]
    own=[party for party in matches if branch is not None and party.branch_id==getattr(branch,'pk',branch)]
    if own:matches=own        # the shop's own record always wins over an unassigned one with the same name
    if selected:
        selected=next((party for party in matches if party.pk==selected.pk),None)
        if selected:matches=[selected]
    if len(matches)>1:raise ValidationError('More than one customer account has this name. Give those accounts distinct names in Customers before continuing.')
    if matches and not matches[0].enabled:raise ValidationError('This customer is inactive. Ask the owner to review the customer account.')
    return matches[0] if matches else None


@transaction.atomic
def resolve(name,user,selected=None,kind="Customer",demo_batch="",branch=None):
    person=access.staff(user)
    if branch is None and person is not None:branch=person.branch
    name=clean_name(name)
    if kind not in ('Customer','Supplier'):raise ValidationError('Choose a customer or supplier.')
    # Serialize matching name creation across shop counters. The lock lasts for
    # the surrounding sale/draft transaction, so failed sales leave no customer.
    key=int.from_bytes(hashlib.sha256((('covison-customer:' if kind=='Customer' else 'covison-supplier:')+name.casefold()+':'+str(getattr(branch,'pk','')) ).encode()).digest()[:8],'big',signed=True)
    with connection.cursor() as cursor:cursor.execute('SELECT pg_advisory_xact_lock(%s)',[key])
    existing=find(name,selected,kind,branch)
    if existing:
        if existing.branch_id is None and branch is not None:       # first use of an old unassigned record: it now belongs to this shop
            existing.branch=branch;existing.save(update_fields=['branch','modified'])
        return existing
    from . import services
    customer=Party.objects.create(code=services.code('CUST'),name=name,kind=kind,demo_batch=demo_batch,branch=branch)
    services.audit(user,'Create '+kind.lower(),None,party=str(customer.pk),source='typed name')
    return customer
