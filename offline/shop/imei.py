"""IMEI lookup and the full life-cycle history of one physical phone."""
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from .models import Device,Document,Line,StockMovement
from . import access


def history(device,show_costs,places=None):
    """Chronological events for one device, built from the documents that touched it."""
    events=[]
    lines=Line.objects.filter(device=device)
    if places is not None:lines=lines.filter(document__branch__in=places)       # a shop never sees another shop's purchases, sales or parties
    for line in lines.select_related('document__customer','document__supplier','document__branch','warehouse'):
        d=line.document
        if d.kind=='Purchase Invoice':continue            # the supplier bill repeats the receipt; show the receipt only
        if d.status=='Cancelled':label=f'{d.kind} (cancelled)'
        elif d.is_return:label='Sales return'
        else:label={'Purchase Receipt':'Received into stock','Sales Invoice':'Sold','POS Invoice':'Sold','Purchase Return':'Returned to supplier','Stock Adjustment':'Stock adjustment'}.get(d.kind,d.kind)
        party=(d.customer or d.supplier)
        events.append(dict(when=d.posting_date,order=d.created,label=label,document=d,party=party.name if party else '',shop=d.branch.name,amount=line.rate if (show_costs or d.kind in ('Sales Invoice','POS Invoice')) else None))
    for m in StockMovement.objects.filter(device=device,document__kind='Stock Entry').select_related('document__branch','warehouse'):
        events.append(dict(when=m.posting_date,order=m.document.created,label='Stock transfer '+('out' if m.quantity<0 else 'in'),document=m.document,party='',shop=m.warehouse.name if m.warehouse_id else m.document.branch.name,amount=None))
    for claim in Document.objects.filter(kind='Warranty Claim',branch=device.branch):
        if str(claim.data.get('device'))==str(device.pk):events.append(dict(when=claim.posting_date,order=claim.created,label='Warranty claim ('+claim.state+')',document=claim,party=claim.customer.name if claim.customer else '',shop=claim.branch.name,amount=None))
    events.sort(key=lambda e:(e['when'],e['order']))
    return events


@login_required
def device_history(request,pk):
    from .views import scoped_devices
    device=get_object_or_404(scoped_devices(request.user).select_related('product__model','branch','warehouse','customer','sale').prefetch_related('imeis'),pk=pk)
    show_costs=access.costs(request.user)
    events=history(device,show_costs,None if request.user.is_superuser else access.branches(request.user))
    purchase=next((e for e in events if e['label']=='Received into stock'),None)
    from .parties import warranty
    return render(request,'device_history.html',dict(title=device.code,device=device,events=events,purchase=purchase,show_costs=show_costs,warranty=warranty(device),imeis=list(device.imeis.all()),
        can_edit=access.costs(request.user) or access.manager(request.user)))


@login_required
def imei_lookup(request):
    """Scan or type an IMEI, device reference or model. One match opens the phone; several show a list."""
    from .views import scoped_devices
    q=(request.GET.get('q') or '').strip()[:40]
    matches=[]
    if q:
        base=scoped_devices(request.user).select_related('product','branch','warehouse').prefetch_related('imeis')
        exact=base.filter(Q(imeis__number=q)|Q(code__iexact=q)).distinct()
        if exact.count()==1:return redirect('device_history',pk=exact.first().pk)
        matches=list(base.filter(Q(imeis__number__icontains=q)|Q(code__icontains=q)|Q(product__name__icontains=q)).distinct().order_by('-created')[:30]) if len(q)>=3 else []
    return render(request,'imei_lookup.html',dict(title='Find a phone by IMEI',q=q,matches=matches))


@login_required
def accept_holding(request,pk):
    """Inspection gate: a returned or traded-in phone stays in Holding (owned, valued, not for sale) until a manager accepts it."""
    from django.contrib import messages
    from django.core.exceptions import PermissionDenied
    from django.db import transaction
    from django.utils import timezone
    from . import services
    from .views import scoped_devices
    if request.method!='POST':return redirect('device_history',pk=pk)
    if not (access.manager(request.user) or access.costs(request.user)):raise PermissionDenied('A manager must accept phones into stock.')
    with transaction.atomic():
        device=get_object_or_404(scoped_devices(request.user).select_for_update(of=('self',)),pk=pk)
        if device.status!='Holding':messages.error(request,'This phone is not waiting for inspection.');return redirect('device_history',pk=pk)
        if not device.warehouse_id:messages.error(request,'Give the phone a stock location first.');return redirect('device',pk=pk)
        device.status='Available';device.inspection_status='Passed';device.inspection_date=timezone.localdate();device.save()
        services.audit(request.user,'Accept phone into stock',None,branch=device.branch,device=device.code)
    messages.success(request,f'{device.code} is now available for sale.')
    return redirect('device_history',pk=pk)
