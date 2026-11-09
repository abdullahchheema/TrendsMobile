"""Customer and supplier profile pages: balances, history, devices, payments, exchanges and credit."""
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.shortcuts import get_object_or_404,render
from django.utils import timezone
from .models import Device,Document,Line,Party
from . import access,credit,permissions,services as s

ZERO=Decimal('0.00')


def warranty(device,today=None):
    today=today or timezone.localdate()
    if device.status!='Sold' or not device.sold_on or not device.warranty_days:return dict(label='No warranty recorded',valid=None,until=None)
    until=device.sold_on.fromordinal(device.sold_on.toordinal()+device.warranty_days)
    return dict(label=f'Warranty until {until:%d %b %Y}',valid=until>=today,until=until)


@login_required
def profile(request,pk):
    party=get_object_or_404(Party,pk=pk)
    if not (access.manager(request.user) or access.costs(request.user) or permissions.can(request.user,'sell') or permissions.can(request.user,'payments') or access.statements(request.user)):raise PermissionDenied('You do not have access to profiles.')
    places=access.branches(request.user);show_costs=access.costs(request.user)
    docs=Document.objects.filter(branch__in=places,status='Submitted')
    if party.kind=='Customer':
        invoices=docs.filter(customer=party,kind__in=list(s.SELL)).select_related('branch').order_by('-posting_date','-created')
        payments=docs.filter(customer=party,kind='Payment Entry').order_by('-posting_date','-created')
        exchanges=Document.objects.filter(branch__in=places,customer=party,kind='Mobile Exchange').order_by('-posting_date')
        claims=Document.objects.filter(branch__in=places,customer=party,kind='Warranty Claim').order_by('-posting_date')
        devices=[dict(device=d,warranty=warranty(d)) for d in Device.objects.filter(customer=party,branch__in=places).select_related('product').prefetch_related('imeis').order_by('-sold_on')]
        sales=invoices.filter(is_return=False);returns=invoices.filter(is_return=True)
        total_sales=(sales.aggregate(t=Sum('total'))['t'] or ZERO)+(returns.aggregate(t=Sum('total'))['t'] or ZERO)       # returns carry negative totals
        balance=credit.customer_balance(party)
        overdue=sum((credit.overdue_amount(d) for d in sales.filter(outstanding__gt=0).prefetch_related('installments')),ZERO)
        ctx=dict(invoices=invoices[:50],payments=payments[:50],exchanges=exchanges[:20],claims=claims[:20],devices=devices[:50],total=total_sales,balance=balance,overdue=overdue,paid=total_sales-balance,
                 last=sales.first(),limit=party.credit_limit,whatsapp=credit.whatsapp_number(party.phone))
    else:
        invoices=docs.filter(supplier=party,kind='Purchase Invoice').select_related('branch').order_by('-posting_date','-created')
        payments=docs.filter(supplier=party,kind='Payment Entry').order_by('-posting_date','-created')
        supplied=Line.objects.filter(document__supplier=party,document__kind='Purchase Receipt',document__status='Submitted',document__branch__in=places,device__isnull=False).select_related('device__product','document').order_by('-document__posting_date')
        total=invoices.filter(is_return=False).aggregate(t=Sum('outstanding'))['t'] or ZERO
        ctx=dict(invoices=invoices[:50],payments=payments[:50],supplied=supplied[:60],total=invoices.aggregate(t=Sum('total'))['t'] or ZERO,balance=total,whatsapp=credit.whatsapp_number(party.phone))
    ctx.update(title=party.name,party=party,show_costs=show_costs,today=timezone.localdate())
    return render(request,'party.html',ctx)
