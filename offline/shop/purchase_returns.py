"""Return stock to a supplier against a posted supplier bill.

Phones are returned one exact device at a time (at the cost they were bought for); accessories by quantity. Posting:
Dr supplier payable / Cr stock. The bill's outstanding amount falls, and if it was already paid the supplier now owes us
a credit, which is settled with a normal supplier payment (Receive) or applied to the next bill."""
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .models import Branch,Device,Document,Line,StockMovement
from . import access,permissions,services as s

ZERO=Decimal('0.00')
RETURNED='Returned to supplier'


def _allowed(user):return permissions.can(user,'accounting') or access.manager(user)


def returnable(bill):
    """(devices still available from this bill's receipt, accessory lines with the quantity still returnable)."""
    receipt=bill.reference
    if not receipt:return [],[]
    devices=[l.device for l in receipt.lines.select_related('device__product').order_by('pk') if l.device_id and l.device.status=='Available']
    accessories=[]
    for line in receipt.lines.select_related('product').filter(device__isnull=True,product__stock_item=True):
        done=Line.objects.filter(document__reference=bill,document__kind='Purchase Return',document__status='Submitted',product=line.product,device__isnull=True).aggregate(q=Sum('quantity'))['q'] or ZERO
        left=line.quantity-done
        if left>0:accessories.append(dict(line=line,left=left))
    return devices,accessories


@transaction.atomic
def create(bill,user,device_ids,accessory_qty,reason):
    if not _allowed(user):raise PermissionDenied('A manager or accountant must return stock to a supplier.')
    bill=Document.objects.select_for_update().select_related('branch','supplier','reference').get(pk=bill.pk)
    access.require(user,bill.branch)
    if bill.kind!='Purchase Invoice' or bill.status!='Submitted' or bill.is_return or not bill.reference_id or not bill.supplier_id:raise ValidationError('Choose a posted supplier bill that received stock.')
    if not str(reason or '').strip():raise ValidationError('Explain why the stock is being returned.')
    Branch.objects.select_for_update(no_key=True).get(pk=bill.branch_id)
    receipt=bill.reference
    doc=s.new_doc('Purchase Return',bill.branch,supplier=bill.supplier,reference=bill,warehouse=receipt.warehouse,status='Submitted',posted_by=user,notes=str(reason).strip()[:240],data={'reason':str(reason).strip()[:240]})
    s.open_date(doc)
    left_by_line={a['line'].pk:a['left'] for a in returnable(bill)[1]}
    total=ZERO;count=0
    for device_id in device_ids:
        device=Device.objects.select_for_update().get(pk=device_id)
        source=receipt.lines.filter(device=device).first()
        if not source:raise ValidationError('That phone was not received on this bill.')
        if device.status!='Available':raise ValidationError(f'{device.code} is no longer available to return.')
        cost=s.money(source.rate)
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=device.warehouse,quantity=1,rate=cost,cost=cost)
        StockMovement.objects.create(document=doc,posting_date=doc.posting_date,product=device.product,device=device,warehouse=device.warehouse,quantity=-1,value=-cost)
        released=[i.number for i in device.imeis.all()]
        device.details=dict(device.details,returned_to_supplier=doc.code,**({'released_imeis':released} if released else {}))
        device.imeis.all().delete()                                   # frees the IMEI if the phone is re-bought elsewhere
        device.status=RETURNED;device.warehouse=None;device.save()
        total+=cost;count+=1
    for line_id,qty in (accessory_qty or {}).items():
        qty=s.quantity(qty)
        source=receipt.lines.select_related('product').filter(pk=line_id,device__isnull=True).first()
        if not source:raise ValidationError('That item was not received on this bill.')
        left=left_by_line.get(source.pk,ZERO)
        if qty>left:raise ValidationError(f'Only {left:g} of {source.product.name} can still be returned.')
        place=source.warehouse or receipt.warehouse
        available,_value=s.on_hand(source.product,place)
        if qty>available:raise ValidationError(f'Only {available:g} of {source.product.name} is in stock to return.')
        value=s.money(qty*source.rate)
        Line.objects.create(document=doc,product=source.product,warehouse=place,quantity=qty,rate=source.rate,cost=source.rate)
        StockMovement.objects.create(document=doc,posting_date=doc.posting_date,product=source.product,warehouse=place,quantity=-qty,value=-value)
        total+=value;count+=1
    if not count or total<=0:raise ValidationError('Choose at least one phone or item to return.')
    s.entry(doc,'payable',debit=total,party=bill.supplier,reference=bill);s.entry(doc,'inventory',credit=total)
    doc.total=total;doc.save()
    Document.objects.filter(pk=bill.pk).update(outstanding=bill.outstanding-total)
    s.balanced(doc);s.audit(user,'Purchase return',doc,bill=bill.code,amount=str(total),reason=doc.notes)
    return doc


@login_required
def purchase_return(request,pk):
    from .views import document,error_text
    bill=document(request.user,pk)
    if not _allowed(request.user):raise PermissionDenied('A manager or accountant must return stock to a supplier.')
    if bill.kind!='Purchase Invoice':return redirect('detail',pk=bill.pk)
    devices,accessories=returnable(bill)
    if request.method=='POST':
        try:
            qty={k[4:]:v for k,v in request.POST.items() if k.startswith('qty_') and v.strip() and v.strip() not in ('0','0.0')}
            doc=create(bill,request.user,request.POST.getlist('devices'),qty,request.POST.get('reason',''))
            messages.success(request,f'Return {doc.code} posted: PKR {doc.total:,.2f} taken off what you owe {bill.supplier.name}.')
            return redirect('detail',pk=doc.pk)
        except (ValidationError,ValueError,Device.DoesNotExist) as error:messages.error(request,error_text(error) if isinstance(error,ValidationError) else 'Check the selected items.')
    return render(request,'purchase_return.html',dict(title='Return to supplier',bill=bill,devices=devices,accessories=accessories))
