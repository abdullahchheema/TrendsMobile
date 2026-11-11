"""Online orders: the engine behind the website's orders.

Life of an order
  New         stock is reserved from whichever shops hold it (phones become 'Reserved', accessories are counted as reserved). Nothing is
              sold yet, so the shops' books are untouched.
  Dispatched  one sales invoice is posted in each shop that supplied stock (channel = Online): stock leaves the shop, cost of goods and
              revenue are posted there, and the money is received (prepaid) or left owing (cash on delivery).
  Delivered   cash on delivery is collected into a shop account.
  Cancelled   a New order is cancelled and its stock is released.
Refunds and exchanges after dispatch use the normal Returns & Exchanges flow on the invoice, so the physical stock comes back to the shop.

Prices always come from the ERP, never from the website. Variants of a phone are: product (model, storage, colour) + condition + PTA status +
type + price; identical phones in several shops are one variant with the total quantity."""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .models import Branch,Device,Document,Line,OnlineAllocation,OnlineOrder,OnlineOrderLine,Party,Product,ShopSettings
from . import pricing,shopdefaults,services as s

ZERO=Decimal('0.00')
METHODS={'Cash on Delivery':'Cash','Bank Transfer':'Bank','Easypaisa':'Easypaisa','JazzCash':'JazzCash','Card':'Card'}   # order method -> shop payment method name
MAX_LINES=40
MAX_QTY=50
DELIVERY_CODE='ONLINE-DELIVERY'


# ------------------------------------------------------------------ prices and stock
def device_price(device,product=None):
    product=product or device.product
    return device.online_price if device.online_price is not None else product.online_price if product.online_price is not None else device.asking_price


def product_price(product):
    return product.online_price if product.online_price is not None else product.selling_price


def reserved_qty(product,branch,exclude_order=None):
    """Accessory units held by orders that are still New in this shop."""
    rows=OnlineAllocation.objects.filter(product=product,branch=branch,device__isnull=True,line__order__status=OnlineOrder.NEW)
    if exclude_order:rows=rows.exclude(line__order=exclude_order)
    return rows.aggregate(q=Sum('quantity'))['q'] or ZERO


def shops():
    return list(Branch.objects.filter(enabled=True).order_by('name','code'))


def phone_pool(product,condition,pta,kind,price,lock=False):
    """{branch: [available devices of this exact variant, oldest first]}."""
    qs=Device.objects.filter(product=product,status='Available',branch__enabled=True,condition=condition,pta_status=pta,device_type=kind,warehouse__isnull=False)
    if lock:qs=qs.select_for_update()
    pool=defaultdict(list)
    for d in qs.select_related('branch','product').order_by('received_on','code'):
        if device_price(d)==price:pool[d.branch_id].append(d)
    return pool


def accessory_pool(product,lock=False):
    """{branch id: units free to sell}."""
    from .models import StockMovement
    out={}
    for b in shops():
        place=shopdefaults.warehouse(b)
        free=s.on_hand(product,place)[0]-reserved_qty(product,b)
        if free>0:out[b.pk]=free
    return out


def variants(published_only=True):
    """What the website can sell right now: phones grouped by product+condition+PTA+type+price, accessories by product, each with the
    quantity per shop. Used by the Online Store screens and checked on every order."""
    rows=[]
    qs=Product.objects.filter(enabled=True)
    if published_only:qs=qs.filter(online_visible=True)
    for p in qs.select_related('model'):
        if p.serialized:
            groups=defaultdict(lambda:defaultdict(int))
            for d in Device.objects.filter(product=p,status='Available',branch__enabled=True,warehouse__isnull=False):groups[(d.condition,d.pta_status,d.device_type,device_price(d,p))][d.branch_id]+=1
            for (cond,pta,kind,price),per in groups.items():rows.append(dict(product=p,condition=cond,pta_status=pta,device_type=kind,price=price,per_shop=dict(per),quantity=sum(per.values())))
        elif p.stock_item:
            per=accessory_pool(p)
            if per:rows.append(dict(product=p,condition='',pta_status='',device_type='',price=product_price(p),per_shop={k:int(v) for k,v in per.items()},quantity=int(sum(per.values()))))
    return rows


# ------------------------------------------------------------------ allocation
def _need(lines):
    """Per line: ({branch id: free units}, quantity, pool for phones)."""
    out=[]
    for l in lines:
        p=l['product']
        if p.serialized:
            pool=phone_pool(p,l['condition'],l['pta_status'],l['device_type'],l['unit_price'],lock=True);out.append((l,{b:len(v) for b,v in pool.items()},pool))
        else:out.append((l,{b:int(q) for b,q in accessory_pool(p).items()},None))
    return out


def plan(lines,prefer=None):
    """Choose where each line comes from. One shop that can supply the whole order wins (fewest parcels and invoices; the shop with the most
    stock of the first item, or `prefer`). Otherwise each line is filled from the shops with the most stock first.
    Returns [(line, branch id, quantity)] or raises ValidationError naming what is short."""
    needs=_need(lines)
    for l,free,_ in needs:
        if sum(free.values())<l['quantity']:
            raise ValidationError(f'Only {sum(free.values())} of {l["product"].name} are available, not {l["quantity"]}.')
    whole=[b for b in set().union(*[set(f) for _,f,_ in needs]) if all(f.get(b,0)>=l['quantity'] for l,f,_ in needs)]
    if prefer is not None and prefer not in whole:raise ValidationError('That shop does not hold everything in this order.')
    chosen=[]
    if whole:
        pick=prefer if prefer is not None else max(whole,key=lambda b:(sum(f.get(b,0) for _,f,_ in needs),str(b)))
        return [(l,pick,l['quantity']) for l,_,_ in needs]
    for l,free,_ in needs:
        left=l['quantity']
        for b,have in sorted(free.items(),key=lambda kv:(-kv[1],str(kv[0]))):
            if left<=0:break
            take=min(left,have);chosen.append((l,b,take));left-=take
    return chosen


def _reserve(order,planned):
    """Write the plan: reserve the exact phones, record the shops."""
    by_line={str(l['line'].pk):l for l,_,_ in planned}
    cache={}
    for l,branch_id,qty in planned:
        line=l['line'];product=l['product'];branch=Branch.objects.get(pk=branch_id)
        if product.serialized:
            key=str(line.pk)
            pool=cache.get(key) or phone_pool(product,l['condition'],l['pta_status'],l['device_type'],l['unit_price'],lock=True);cache[key]=pool
            take=pool[branch_id][:qty];pool[branch_id]=pool[branch_id][qty:]
            if len(take)<qty:raise ValidationError(f'{product.name} is no longer available in {branch.name}.')
            for d in take:
                d.status='Reserved';d.save(update_fields=['status','modified'])
                OnlineAllocation.objects.create(line=line,product=product,branch=branch,device=d,quantity=1)
        else:
            if accessory_pool(product).get(branch_id,0)<qty:raise ValidationError(f'{product.name} is no longer available in {branch.name}.')
            OnlineAllocation.objects.create(line=line,product=product,branch=branch,device=None,quantity=qty)


def release(order):
    """Give the reserved stock back to the shops."""
    allocations=list(OnlineAllocation.objects.select_for_update().filter(line__order=order).select_related('device'))
    for a in allocations:
        if a.device_id and a.device.status=='Reserved':
            d=Device.objects.select_for_update().get(pk=a.device_id)
            if d.status=='Reserved':d.status='Available';d.save(update_fields=['status','modified'])
    OnlineAllocation.objects.filter(pk__in=[a.pk for a in allocations]).delete()


# ------------------------------------------------------------------ creating an order
def _clean_phone(value):
    digits=''.join(ch for ch in str(value or '') if ch.isdigit() or ch=='+')
    if len(digits.replace('+',''))<10:raise ValidationError('Enter the customer phone number (at least 10 digits).')
    return digits[:20]


def _int(value,label):
    try:n=int(value)
    except (TypeError,ValueError):raise ValidationError(f'{label} must be a whole number.')
    return n


@transaction.atomic
def create(data,user=None,source='Website',credit=None):
    """Create an order from validated-looking input: {external_ref, customer{name,phone,email,address,city}, payment_method, payment_reference, delivery_fee,
    notes, items:[{product_id, condition, pta_status, device_type, price, quantity}], expected_total}. Returns (order, created)."""
    ref=str(data.get('external_ref') or '').strip()[:80] or None
    if ref:
        existing=OnlineOrder.objects.filter(external_ref=ref).first()
        if existing:return existing,False
    cust=data.get('customer') or {}
    name=' '.join(str(cust.get('name') or '').split())[:180]
    if not name:raise ValidationError('Enter the customer name.')
    phone=_clean_phone(cust.get('phone'))
    method=str(data.get('payment_method') or 'Cash on Delivery')
    if method not in METHODS:raise ValidationError('Payment method must be one of: '+', '.join(METHODS)+'.')
    items=data.get('items')
    if not isinstance(items,list) or not items:raise ValidationError('The order has no items.')
    if len(items)>MAX_LINES:raise ValidationError(f'At most {MAX_LINES} lines per order.')
    try:fee=s.payment_money(data.get('delivery_fee') or 0)
    except ValidationError:raise ValidationError('Delivery fee is not a valid amount.')
    if fee<0 or fee>100000:raise ValidationError('Delivery fee is outside the allowed range.')
    lines=[];seen=set()
    for item in items:
        if not isinstance(item,dict):raise ValidationError('Invalid item.')
        try:product=Product.objects.select_related('model').get(pk=item.get('product_id'),enabled=True)
        except (Product.DoesNotExist,ValueError,ValidationError,TypeError):raise ValidationError('A product in this order does not exist any more.')
        if source=='Website' and not product.online_visible:raise ValidationError(f'{product.name} is not for sale online.')
        qty=_int(item.get('quantity'),'Quantity')
        if not 1<=qty<=MAX_QTY:raise ValidationError(f'Quantity must be between 1 and {MAX_QTY}.')
        cond=str(item.get('condition') or '');pta=str(item.get('pta_status') or '');kind=str(item.get('device_type') or '')
        if product.serialized:
            if not (cond and pta and kind):raise ValidationError(f'Choose the condition, PTA status and type of {product.name}.')
            if item.get('price') in (None,''):raise ValidationError(f'The price of {product.name} is required.')
            price=s.payment_money(item.get('price'))
        else:
            cond=pta=kind='';price=product_price(product)
            if item.get('price') not in (None,'') and s.payment_money(item.get('price'))!=price:raise ValidationError(f'The price of {product.name} changed to PKR {price:,.2f}. Refresh and try again.')
        key=(str(product.pk),cond,pta,kind,price)
        if key in seen:raise ValidationError('The same item appears twice. Combine it into one line with a quantity.')
        seen.add(key)
        if product.serialized:
            pool=phone_pool(product,cond,pta,kind,price)
            if not pool:raise ValidationError(f'{product.name} at PKR {price:,.2f} is no longer available (it may have been sold, or its price changed).')
        if price<=0:raise ValidationError(f'{product.name} has no price.')
        lines.append(dict(product=product,condition=cond,pta_status=pta,device_type=kind,unit_price=price,quantity=qty))
    list(Product.objects.select_for_update().filter(pk__in={l['product'].pk for l in lines}).order_by('pk'))      # one order at a time per product, so the last unit cannot be promised twice
    subtotal=sum((l['unit_price']*l['quantity'] for l in lines),ZERO);total=subtotal+fee
    if data.get('expected_total') not in (None,'') and s.payment_money(data['expected_total'])!=total:
        raise ValidationError(f'The total is now PKR {total:,.2f}, not PKR {s.payment_money(data["expected_total"]):,.2f}. Prices or delivery changed; refresh and try again.')
    order=OnlineOrder.objects.create(code=s.code('OL'),source=source,external_ref=ref,customer_name=name,customer_phone=phone,customer_email=str(cust.get('email') or '')[:180],address=str(cust.get('address') or '')[:500],
        city=str(cust.get('city') or '')[:100],payment_method=method,payment_status='Paid' if (data.get('payment_status')=='Paid' and method!='Cash on Delivery') else 'Unpaid',payment_reference=str(data.get('payment_reference') or '')[:180],
        delivery_fee=fee,subtotal=subtotal,total=total,notes=str(data.get('notes') or '')[:500],created_by=user if getattr(user,'is_authenticated',False) else None,exchange_credit=credit)
    for l in lines:l['line']=OnlineOrderLine.objects.create(order=order,product=l['product'],condition=l['condition'],pta_status=l['pta_status'],device_type=l['device_type'],quantity=l['quantity'],unit_price=l['unit_price'])
    _reserve(order,plan(lines,prefer=credit.branch_id if credit else None))
    if getattr(user,'is_authenticated',False):s.audit(user,'Online order created',None,order=order.code,total=str(total))
    return order,True


# ------------------------------------------------------------------ order actions
def _lock(order):return OnlineOrder.objects.select_for_update().get(pk=order.pk)


def order_lines(order):
    rows=[]
    for l in order.lines.select_related('product').prefetch_related('allocations__branch','allocations__device'):
        rows.append(dict(line=l,shops=sorted({a.branch.name for a in l.allocations.all()}),allocations=list(l.allocations.all())))
    return rows


@transaction.atomic
def reassign(order,user,branch):
    """Supply a New order from one chosen shop instead (when that shop holds everything)."""
    order=_lock(order)
    if order.status!=OnlineOrder.NEW:raise ValidationError('Only a new order can be moved to another shop.')
    lines=[dict(line=l,product=l.product,condition=l.condition,pta_status=l.pta_status,device_type=l.device_type,unit_price=l.unit_price,quantity=l.quantity) for l in order.lines.select_related('product')]
    release(order);_reserve(order,plan(lines,prefer=branch.pk))
    s.audit(user,'Online order reassigned',None,order=order.code,shop=branch.name);return order


@transaction.atomic
def cancel(order,user,reason):
    order=_lock(order)
    if order.status!=OnlineOrder.NEW:raise ValidationError('Only a new order can be cancelled. For a dispatched order use Return / exchange.')
    if not str(reason or '').strip():raise ValidationError('Give a reason for cancelling.')
    release(order);order.status=OnlineOrder.CANCELLED;order.cancel_reason=str(reason).strip()[:240];order.save(update_fields=['status','cancel_reason','modified'])
    s.audit(user,'Online order cancelled',None,order=order.code,reason=order.cancel_reason);return order


@transaction.atomic
def mark_paid(order,user,reference=''):
    order=_lock(order)
    if order.status in (OnlineOrder.CANCELLED,):raise ValidationError('This order is cancelled.')
    if order.payment_method=='Cash on Delivery':raise ValidationError('Cash on delivery is collected when the order is delivered.')
    if order.status!=OnlineOrder.NEW:raise ValidationError('Payment can only be confirmed before dispatch.')
    order.payment_status='Paid';order.payment_reference=str(reference or order.payment_reference)[:180];order.save(update_fields=['payment_status','payment_reference','modified'])
    s.audit(user,'Online payment confirmed',None,order=order.code,method=order.payment_method);return order


def party_for(branch,order):
    """The customer in this shop's own list: found by phone, else created. Shops never share customers."""
    if order.exchange_credit_id and order.exchange_credit.branch_id==branch.pk and order.exchange_credit.customer_id:return order.exchange_credit.customer
    found=Party.objects.filter(branch=branch,kind='Customer',phone=order.customer_phone).first()
    if found:return found
    name=order.customer_name
    if Party.objects.filter(branch=branch,kind='Customer',name=name).exists():name=f'{name} ({order.customer_phone})'[:180]
    return Party.objects.create(code=s.code('CUST'),name=name,kind='Customer',phone=order.customer_phone,city=order.city,address=order.address,email=order.customer_email,branch=branch)


def delivery_product():
    p=Product.objects.filter(code=DELIVERY_CODE).first()
    return p or Product.objects.create(code=DELIVERY_CODE,name='Delivery charge',group='Online',serialized=False,stock_item=False,selling_price=ZERO,enabled=False)


def shop_method(branch,order):
    name=METHODS[order.payment_method];conf=ShopSettings.objects.filter(branch=branch).first()
    if not conf or name not in conf.payment_methods:raise ValidationError(f'{branch.name} has no “{name}” payment method set up, so it cannot receive this order’s money. Add it in Shop Settings first.')
    return name


@transaction.atomic
def dispatch(order,user,courier='',tracking=''):
    """Post the sale: one invoice per supplying shop. Prepaid orders are received at once; cash on delivery stays owing until delivery."""
    order=_lock(order)
    if order.status!=OnlineOrder.NEW:raise ValidationError('Only a new order can be dispatched.')
    if order.payment_method!='Cash on Delivery' and order.payment_status!='Paid':raise ValidationError('Confirm that the payment arrived before dispatching.')
    allocations=list(OnlineAllocation.objects.select_for_update().filter(line__order=order).select_related('line','product','device','branch'))
    if not allocations:raise ValidationError('This order has no reserved stock.')
    per_branch=defaultdict(list)
    for a in allocations:per_branch[a.branch_id].append(a)
    release_devices=[a.device for a in allocations if a.device_id]
    for d in Device.objects.select_for_update().filter(pk__in=[x.pk for x in release_devices]):
        if d.status!='Reserved':raise ValidationError(f'{d.code} is no longer reserved for this order. Cancel the order and create it again.')
    for a in allocations:                     # the reservation ends here: the invoice sells the exact phones
        if a.device_id:Device.objects.filter(pk=a.device_id).update(status='Available')
    OnlineAllocation.objects.filter(pk__in=[a.pk for a in allocations if not a.device_id]).delete()   # accessories: counted again by the invoice itself
    first=True;made=[]
    for branch_id,group in per_branch.items():
        branch=Branch.objects.get(pk=branch_id);person=shopdefaults.shop_person(branch);warehouse=shopdefaults.warehouse(branch)
        method=shop_method(branch,order) if order.payment_method!='Cash on Delivery' else None
        doc=s.new_doc('Sales Invoice',branch,customer=party_for(branch,order),seller=person,channel='Online',online_order=order,notes=f'Online order {order.code}'+(f' · {order.notes}' if order.notes else ''),
                      data=dict(channel='Online',online_order=order.code))
        pricing.inputs(doc,'amount','0','','');doc.save()
        for a in group:
            if a.device_id:Line.objects.create(document=doc,product=a.product,device=a.device,warehouse=a.device.warehouse,quantity=1,rate=a.line.unit_price)
            else:Line.objects.create(document=doc,product=a.product,warehouse=warehouse,quantity=a.quantity,rate=a.line.unit_price)
        if first and order.delivery_fee>0:Line.objects.create(document=doc,product=delivery_product(),warehouse=warehouse,quantity=1,rate=order.delivery_fee)
        first=False;doc.save()
        sale=s.checkout(doc,user,[],person)
        if order.exchange_credit_id and order.exchange_credit.branch_id==branch.pk:
            from . import returns
            returns.apply_to_sale(Document.objects.get(pk=order.exchange_credit_id),sale,user);sale.refresh_from_db()
        if method and sale.outstanding>0:_receive(sale,order,method,user,order.payment_reference)
        sale.refresh_from_db();made.append(sale)
    order.status=OnlineOrder.DISPATCHED;order.dispatched_on=timezone.localdate();order.courier=str(courier or '')[:100];order.tracking=str(tracking or '')[:100]
    order.save(update_fields=['status','dispatched_on','courier','tracking','modified'])
    s.audit(user,'Online order dispatched',None,order=order.code,invoices=[d.code for d in made]);return made


def _receive(inv,order,method,user,reference=''):
    pe=s.new_doc('Payment Entry',inv.branch,customer=inv.customer,reference=inv,handler=shopdefaults.shop_person(inv.branch),channel='Online',online_order=order,posting_date=timezone.localdate(),
                 data=dict(amount=str(inv.outstanding),direction='Receive',mode=method,reference=reference))
    return s.submit(pe,user)


@transaction.atomic
def deliver(order,user,method='Cash'):
    """Delivered. Cash on delivery is collected now into the chosen shop account, against each shop's invoice."""
    order=_lock(order)
    if order.status!=OnlineOrder.DISPATCHED:raise ValidationError('Only a dispatched order can be marked delivered.')
    for inv in Document.objects.select_for_update().filter(online_order=order,kind__in=list(s.SELL),is_return=False,status='Submitted'):
        if inv.outstanding>0:
            conf=ShopSettings.objects.filter(branch=inv.branch).first()
            if not conf or method not in conf.payment_methods:raise ValidationError(f'{inv.branch.name} has no “{method}” payment method set up.')
            _receive(inv,order,method,user,f'Delivery of {order.code}')
    order.status=OnlineOrder.DELIVERED;order.payment_status='Paid';order.delivered_on=timezone.localdate()
    order.save(update_fields=['status','payment_status','delivered_on','modified']);s.audit(user,'Online order delivered',None,order=order.code);return order


def invoices(order):
    return Document.objects.filter(online_order=order,kind__in=list(s.SELL),is_return=False).select_related('branch','customer').order_by('created')
