"""Resolve typed customer names without losing account identity or retry safety."""
import hashlib
from django.core.exceptions import ValidationError
from django.db import connection,transaction
from .models import Party
from . import access


def clean_name(value):
    if not isinstance(value,str):raise ValidationError('Enter a customer name.')
    name=' '.join(value.split())
    if not name:raise ValidationError('Enter a customer name.')
    if len(name)>180:raise ValidationError('Customer names must be 180 characters or fewer.')
    if any(ord(char)<32 or ord(char)==127 for char in name):raise ValidationError('Enter a valid customer name.')
    return name


def find(name,selected=None,kind="Customer"):
    key=clean_name(name).casefold()
    # Names are shared across shops, just like the existing Customer master.
    # Normalize imported names too, including repeated spaces and Urdu names.
    matches=[party for party in Party.objects.filter(kind=kind).only('id','name','enabled').iterator() if ' '.join(party.name.split()).casefold()==key]
    if selected:
        selected=next((party for party in matches if party.pk==selected.pk),None)
        if selected:matches=[selected]
    if len(matches)>1:raise ValidationError('More than one customer account has this name. Give those accounts distinct names in Customers before continuing.')
    if matches and not matches[0].enabled:raise ValidationError('This customer is inactive. Ask a manager to review the customer account.')
    return matches[0] if matches else None


@transaction.atomic
def resolve(name,user,selected=None,kind="Customer",demo_batch=""):
    access.staff(user)
    name=clean_name(name)
    if kind not in ('Customer','Supplier'):raise ValidationError('Choose a customer or supplier.')
    # Serialize matching name creation across shop counters. The lock lasts for
    # the surrounding sale/draft transaction, so failed sales leave no customer.
    key=int.from_bytes(hashlib.sha256((('covison-customer:' if kind=='Customer' else 'covison-supplier:')+name.casefold()).encode()).digest()[:8],'big',signed=True)
    with connection.cursor() as cursor:cursor.execute('SELECT pg_advisory_xact_lock(%s)',[key])
    existing=find(name,selected,kind)
    if existing:return existing
    from . import services
    customer=Party.objects.create(code=services.code('CUST'),name=name,kind=kind,demo_batch=demo_batch)
    services.audit(user,'Create '+kind.lower(),None,party=str(customer.pk),source='typed name')
    return customer
