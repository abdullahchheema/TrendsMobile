"""Consistency check for online orders and stock. Finds anything where the orders and the physical stock disagree, so a problem is seen
the day it happens instead of surfacing as a double sale. Read-only: it never changes data. Run: manage.py online_check"""
from collections import Counter
from django.db.models import Sum
from .models import Device,Document,OnlineAllocation,OnlineOrder,OnlineOrderLine,StockMovement
from . import services as s


def problems():
    found=[]
    allocs=list(OnlineAllocation.objects.select_related('line__order','device','product','branch'))
    invoiced=set(Document.objects.filter(online_order__isnull=False,kind__in=list(s.SELL),is_return=False,status='Submitted').values_list('online_order_id',flat=True))
    def holding(o):return o.status==OnlineOrder.NEW or (o.status==OnlineOrder.DISPATCHED and o.pk not in invoiced)      # stock still on the shop's books, promised to the order
    for a in allocs:
        o=a.line.order
        if o.status==OnlineOrder.CANCELLED:found.append(f'{o.code}: cancelled but still holds stock.')
        elif not holding(o):continue          # delivered / returned / invoiced at dispatch: the allocation stays as the record of which exact phone went out
        elif a.device_id and a.device.status!='Reserved':found.append(f'{o.code}: phone {a.device.code} is {a.device.status}, not Reserved.')
        elif a.device_id and a.device.branch_id!=a.branch_id:found.append(f'{o.code}: phone {a.device.code} is no longer in the shop the order was allocated to.')
    # A Reserved phone is off the shelf and off the website, so something must be holding it: a new online order or a pending transfer.
    from . import transfers
    held={a.device_id for a in allocs if a.device_id and holding(a.line.order)};moving=set()
    for doc in transfers.transfers().filter(status='Submitted',state=transfers.PENDING):moving.update(str(pk) for pk in doc.data.get('devices',[]))
    for d in Device.objects.filter(status='Reserved'):
        if d.pk not in held and str(d.pk) not in moving:found.append(f'Phone {d.code} is Reserved but no new online order or pending transfer holds it: it cannot be sold anywhere.')
    twice=[d for d,n in Counter(a.device_id for a in allocs if a.device_id and holding(a.line.order)).items() if n>1]
    for d in twice:found.append(f'Phone {Device.objects.get(pk=d).code} is reserved by more than one order line.')
    for line in OnlineOrderLine.objects.filter(order__status__in=(OnlineOrder.NEW,OnlineOrder.DISPATCHED)).exclude(order_id__in=invoiced).select_related('order','product'):
        have=sum((a.quantity for a in allocs if a.line_id==line.pk),0)
        if have!=line.quantity:found.append(f'{line.order.code}: {line.product.name} needs {line.quantity} but {have} is reserved.')
    held={}
    for a in allocs:
        if not a.device_id and holding(a.line.order):held[(a.product_id,a.branch_id)]=held.get((a.product_id,a.branch_id),0)+a.quantity
    for (pid,bid),qty in held.items():
        onhand=StockMovement.objects.filter(product_id=pid,warehouse__branch_id=bid).aggregate(q=Sum('quantity'))['q'] or 0
        if qty>onhand:found.append(f'Accessory reservations ({qty}) are more than the stock on hand ({onhand}) for product {pid} in shop {bid}.')
    for o in OnlineOrder.objects.filter(status__in=[OnlineOrder.DISPATCHED,OnlineOrder.DELIVERED]):
        invoices=Document.objects.filter(online_order=o,kind__in=list(s.SELL),is_return=False,status='Submitted')
        if o.status==OnlineOrder.DELIVERED and not invoices.exists():found.append(f'{o.code}: delivered but no sales invoice was posted.')
        if o.status==OnlineOrder.DELIVERED and any(i.outstanding>0 for i in invoices):found.append(f'{o.code}: delivered but money is still owed on its invoice.')
        if o.status==OnlineOrder.DISPATCHED and not (o.courier and o.tracking):found.append(f'{o.code}: dispatched without courier and tracking.')
    for o in OnlineOrder.objects.filter(payment_status='Paid',status__in=(OnlineOrder.NEW,OnlineOrder.DISPATCHED)).exclude(payment_method='Cash on Delivery').exclude(pk__in=invoiced):
        got=Document.objects.filter(online_order=o,kind='Payment Entry',status='Submitted',reference__isnull=True,data__direction='Receive').aggregate(t=Sum('total'))['t'] or 0
        if got and got!=o.total:found.append(f'{o.code}: marked paid but the money booked as an advance is {got}, not {o.total} (booked twice, or for the wrong amount).')      # an order paid before advances were booked (14 Nov 2026) has none, and is received at delivery
    return found
