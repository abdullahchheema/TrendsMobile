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
from decimal import ROUND_HALF_UP,Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q,Sum
from django.utils import timezone
from .models import Branch,Device,Document,Line,OnlineAllocation,OnlineDeliveryRule,OnlineOrder,OnlineOrderAck,OnlineOrderEvent,OnlineOrderLine,OnlinePaymentAccount,OnlineSettings,Party,Product,ShopSettings
from . import pricing,shopdefaults,services as s

ZERO=Decimal('0.00')
METHODS={'Cash on Delivery':'Cash','Bank Transfer':'Bank Transfer','Easypaisa':'Easypaisa','JazzCash':'JazzCash','Card':'Card'}   # order method -> the Online Store's payment method
MAX_LINES=40
MAX_QTY=50
DELIVERY_CODE='ONLINE-DELIVERY'


# ------------------------------------------------------------------ prices and stock
def _after_discount(base,product):
    pct=product.discount_percent or Decimal(0)
    return (base*(Decimal(100)-pct)/Decimal(100)).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP) if pct else base


def device_base(device,product=None):
    """The online price before any percentage off: the phone's own online price, else the product's, else the shop price."""
    product=product or device.product
    return device.online_price if device.online_price is not None else product.online_price if product.online_price is not None else device.asking_price


def device_price(device,product=None):
    """What the website charges for this phone."""
    product=product or device.product
    return _after_discount(device_base(device,product),product)


def product_base(product):
    return product.online_price if product.online_price is not None else product.selling_price


def product_price(product):
    return _after_discount(product_base(product),product)


def compare_price(product,base):
    """The crossed-out price shown next to a sale price (None when nothing is on sale)."""
    if product.compare_price is not None:return product.compare_price
    return base if (product.discount_percent or 0)>0 else None


def reserved_qty(product,branch,exclude_order=None):
    """Accessory units held by orders that are still New in this shop."""
    rows=OnlineAllocation.objects.filter(product=product,branch=branch,device__isnull=True,line__order__status=OnlineOrder.NEW)
    if exclude_order:rows=rows.exclude(line__order=exclude_order)
    return rows.aggregate(q=Sum('quantity'))['q'] or ZERO


def shops():
    """The shops that hold stock (never the Online Store itself)."""
    return list(Branch.objects.filter(enabled=True,is_online=False).order_by('name','code'))


def online_branch():
    """The Online Store's own books: created the first time it is needed. It has its own cash, bank and wallets, customers, sales and
    expenses, and no stock: it sells the shops' stock and owes each shop the cost (inter-shop clearing)."""
    found=Branch.objects.filter(is_online=True).first()
    if found:return found
    from django.contrib.auth import get_user_model
    from . import setup
    owner=get_user_model().objects.filter(is_superuser=True,is_active=True).order_by('pk').first()
    if owner is None:raise ValidationError('The owner account is needed to set up the Online Store books.')
    company=Branch.objects.exclude(company='').exclude(is_online=True).values_list('company',flat=True).first() or 'Business'
    branch=setup.create_shop(dict(code='ONLINE',name='Online Store',company=company,warehouse_name='Online',till_name='Online',payment_methods=list(setup.METHODS)),owner)
    branch.is_online=True;branch.save(update_fields=['is_online'])
    return branch


def phone_pool(product,condition,pta,kind,price,lock=False):
    """{branch: [available devices of this exact variant, oldest first]}."""
    qs=Device.objects.filter(product=product,status='Available',branch__enabled=True,branch__is_online=False,condition=condition,pta_status=pta,device_type=kind,warehouse__isnull=False)
    if lock:qs=qs.select_for_update(of=('self',))
    pool=defaultdict(list)
    for d in qs.select_related('branch','product').order_by('received_on','code'):
        if device_price(d)==price:pool[d.branch_id].append(d)
    return pool


def accessory_free(product_ids):
    """{(product id, branch id): units free to sell} for accessories: stock minus units held by new online orders. Two queries for any number of products."""
    from .models import StockMovement
    shop_ids={b.pk for b in shops()};free={}
    for r in StockMovement.objects.filter(product_id__in=list(product_ids),product__serialized=False,warehouse__branch_id__in=shop_ids).values('product_id','warehouse__branch_id').annotate(q=Sum('quantity')):
        free[(r['product_id'],r['warehouse__branch_id'])]=r['q'] or ZERO
    for r in OnlineAllocation.objects.filter(product_id__in=list(product_ids),device__isnull=True,line__order__status=OnlineOrder.NEW).values('product_id','branch_id').annotate(q=Sum('quantity')):
        key=(r['product_id'],r['branch_id'])
        if key in free:free[key]-=r['q'] or ZERO
    return {k:v for k,v in free.items() if v>0}


def accessory_pool(product,lock=False):
    """{branch id: units free to sell}."""
    return {b:q for (pid,b),q in accessory_free([product.pk]).items()}


def variants(published_only=True):
    """What the website can sell right now: phones grouped by product+condition+PTA+type+price, accessories by product, each with the
    quantity per shop. Used by the Online Store screens and checked on every order. A handful of queries however many products there are."""
    qs=Product.objects.filter(enabled=True)
    if published_only:qs=qs.filter(online_visible=True)
    products={p.pk:p for p in qs.select_related('model')};rows=[]
    groups=defaultdict(lambda:defaultdict(int));base_of={};shop_of={}
    for d in Device.objects.filter(product_id__in=[pk for pk,p in products.items() if p.serialized],status='Available',branch__enabled=True,branch__is_online=False,warehouse__isnull=False):
        p=products[d.product_id];key=(p.pk,d.condition,d.pta_status,d.device_type,device_price(d,p));groups[key][d.branch_id]+=1;base_of[key]=device_base(d,p);shop_of[key]=min(shop_of.get(key,d.asking_price),d.asking_price)
    for (pid,cond,pta,kind,price),per in groups.items():
        p=products[pid];rows.append(dict(product=p,condition=cond,pta_status=pta,device_type=kind,price=price,compare=compare_price(p,base_of[(pid,cond,pta,kind,price)]),shop_price=shop_of[(pid,cond,pta,kind,price)],per_shop=dict(per),quantity=sum(per.values())))
    acc={}
    for (pid,b),q in accessory_free([pk for pk,p in products.items() if not p.serialized and p.stock_item]).items():acc.setdefault(pid,{})[b]=int(q)
    for pid,per in acc.items():
        p=products[pid];rows.append(dict(product=p,condition='',pta_status='',device_type='',price=product_price(p),compare=compare_price(p,product_base(p)),shop_price=p.selling_price,per_shop=per,quantity=sum(per.values())))
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
    allocations=list(OnlineAllocation.objects.select_for_update(of=('self',)).filter(line__order=order).select_related('device'))
    for a in allocations:
        if a.device_id and a.device.status=='Reserved':
            d=Device.objects.select_for_update().get(pk=a.device_id)
            if d.status=='Reserved':d.status='Available';d.save(update_fields=['status','modified'])
    OnlineAllocation.objects.filter(pk__in=[a.pk for a in allocations]).delete()


# ------------------------------------------------------------------ creating an order
KNOWN_TOP={'external_ref','customer','payment_method','payment_status','payment_reference','delivery_fee','notes','items','expected_total','discount','shipping','shipping_method','client_ip'}
KNOWN_CUSTOMER={'name','phone','email','address','city','alt_phone','phone2','postal_code','zip','province'}
MAX_EXTRA=20000


def _discount(value,subtotal):
    """An order-level discount the website applied (a coupon): a number, or {amount, code}. Never more than the items."""
    if value in (None,'',0,'0'):return ZERO,''
    code=''
    if isinstance(value,dict):code=_text(value.get('code'),'the discount code')[:60];value=value.get('amount')
    amount=_amount(value)
    if amount<0:raise ValidationError('The discount cannot be negative.')
    if amount>subtotal:raise ValidationError('The discount cannot be more than the items total.')
    return amount,code


def _extra(data,cust):
    """Everything else the website sent (billing address, delivery notes, source, …) is kept with the order exactly as received."""
    import json
    extra={k:v for k,v in data.items() if k not in KNOWN_TOP}
    more={k:v for k,v in cust.items() if k not in KNOWN_CUSTOMER}
    if more:extra['customer_details']=more
    if isinstance(data.get('shipping'),dict):
        rest={k:v for k,v in data['shipping'].items() if k!='method'}
        if rest:extra['shipping_details']=rest
    try:text=json.dumps(extra,default=str)
    except (TypeError,ValueError):raise ValidationError('The extra order details could not be read.')
    if len(text)>MAX_EXTRA:raise ValidationError('The extra order details are too large.')
    return json.loads(text)


# ------------------------------------------------------------------ order spam
# The website's own limiter counts in each server's memory, and a serverless site runs many copies of itself, so a spammer is only slowed down.
# These limits count orders in this database, which every copy of the website shares. What spam does here is hold real phones in reserve, so
# the limits are on orders that are still waiting for the confirmation call, and on how fast one phone number or one address can order.
MAX_WAITING_PER_PHONE=3          # orders from one phone number that nobody has called yet
MAX_PER_PHONE_PER_HOUR=6
MAX_PER_ADDRESS_PER_HOUR=12      # orders from one internet address (a shared office or mobile network can place several honest ones)


class TooManyOrders(ValidationError):
    """The same visitor is placing orders faster than a person would. Answered with 429 by the order API."""


def _client_ip(value):
    text=_text(value,'the client address').strip()[:45]
    return text if text and all(ch.isalnum() or ch in '.:-' for ch in text) else ''


def _throttle(phone,ip):
    tail=''.join(ch for ch in phone if ch.isdigit())[-10:]
    mine=OnlineOrder.objects.filter(source='Website',customer_phone__endswith=tail)
    if mine.filter(status=OnlineOrder.NEW,confirmed_at__isnull=True).count()>=MAX_WAITING_PER_PHONE:
        raise TooManyOrders(f'You already have {MAX_WAITING_PER_PHONE} orders waiting for our confirmation call. We will call you shortly; please wait for that call before ordering more.')
    since=timezone.now()-timezone.timedelta(hours=1)
    if mine.filter(created__gte=since).count()>=MAX_PER_PHONE_PER_HOUR:
        raise TooManyOrders('Too many orders from this phone number in the last hour. Please try again later.')
    if ip and OnlineOrder.objects.filter(source='Website',created__gte=since,extra__client_ip=ip).count()>=MAX_PER_ADDRESS_PER_HOUR:
        raise TooManyOrders('Too many orders from this connection in the last hour. Please try again later.')


def _clean_phone(value):
    digits=''.join(ch for ch in _text(value,'the customer phone number') if (ch.isascii() and ch.isdigit()) or ch=='+')
    if len(digits.replace('+',''))<10:raise ValidationError('Enter the customer phone number (at least 10 digits).')
    return digits[:20]


def _int(value,label):
    """A whole number: 2, '2' or 2.0. Never True, 1.5, '1.0', Infinity or a list (int() would quietly accept or crash on those)."""
    if isinstance(value,bool):raise ValidationError(f'{label} must be a whole number.')
    if isinstance(value,int):return value
    if isinstance(value,float) and value==value and abs(value)<1e9 and value==int(value):return int(value)
    if isinstance(value,str) and value.strip().isascii() and value.strip().lstrip('-').isdigit() and len(value.strip())<=9:return int(value.strip())
    raise ValidationError(f'{label} must be a whole number.')


def _amount(value):
    """Money sent by the website. A JSON number that went through binary floating point (269997.30000000005) is the same money as
    269997.30 and is read as that; a real third decimal is still refused, and so is anything that is not a number."""
    if isinstance(value,(bool,list,dict)):raise ValidationError('Enter a valid amount.')
    if isinstance(value,float):
        if value!=value or abs(value)>9999999999999:raise ValidationError('Enter a valid amount.')
        if abs(value-round(value,2))<1e-6:value=format(round(value,2),'.2f')
    return s.payment_money(value)


def _text(value,label,required=False):
    """Text from the website or a form: a string or a plain number, never an object or a list (which would be stored as its Python spelling)."""
    if value is None or value=='':
        if required:raise ValidationError(f'Enter {label}.')
        return ''
    if isinstance(value,bool) or not isinstance(value,(str,int,float)):raise ValidationError(f'{label[0].upper()+label[1:]} must be text.')
    return str(value)


@transaction.atomic
def create(data,user=None,source='Website',credit=None,strict=False):
    """Create an order from validated-looking input: {external_ref, customer{name,phone,email,address,city}, payment_method, payment_reference, delivery_fee,
    notes, items:[{product_id, condition, pta_status, device_type, price, quantity}], expected_total}. Returns (order, created)."""
    ref=_text(data.get('external_ref'),'the order reference').strip()[:80] or None
    if ref:
        existing=OnlineOrder.objects.filter(external_ref=ref).first()
        if existing:return existing,False
    cust=data.get('customer')
    if cust is None:cust={}
    if not isinstance(cust,dict):raise ValidationError('Send the customer as an object with a name and a phone number.')
    name=' '.join(_text(cust.get('name'),'the customer name').split())[:180]
    if not name:raise ValidationError('Enter the customer name.')
    phone=_clean_phone(cust.get('phone'))
    text={k:_text(cust.get(k),'the customer '+k.replace('_',' ')) for k in ('email','address','city','alt_phone','phone2','postal_code','zip','province')}
    notes=_text(data.get('notes'),'the order notes');pay_ref=_text(data.get('payment_reference'),'the payment reference')
    method=data.get('payment_method') or 'Cash on Delivery'
    if not isinstance(method,str):raise ValidationError('Payment method must be one of: '+', '.join(METHODS)+'.')
    if method not in METHODS:raise ValidationError('Payment method must be one of: '+', '.join(METHODS)+'.')
    items=data.get('items')
    if not isinstance(items,list) or not items:raise ValidationError('The order has no items.')
    if len(items)>MAX_LINES:raise ValidationError(f'At most {MAX_LINES} lines per order.')
    try:fee=_amount(data.get('delivery_fee') or 0)
    except ValidationError:raise ValidationError('Delivery fee is not a valid amount.')
    if fee<0 or fee>100000:raise ValidationError('Delivery fee is outside the allowed range.')
    client_ip=_client_ip(data.get('client_ip'))
    if strict:_throttle(phone,client_ip)
    if strict and method not in [o['method'] for o in payment_options()]:raise ValidationError(f'{method} is not available right now. Choose another payment method.')
    lines=[];seen=set()
    for item in items:
        if not isinstance(item,dict):raise ValidationError('Invalid item.')
        try:product=Product.objects.select_related('model').get(pk=item.get('product_id'),enabled=True)
        except (Product.DoesNotExist,ValueError,ValidationError,TypeError):raise ValidationError('A product in this order does not exist any more.')
        if source=='Website' and not product.online_visible:raise ValidationError(f'{product.name} is not for sale online.')
        qty=_int(item.get('quantity'),'Quantity')
        if not 1<=qty<=MAX_QTY:raise ValidationError(f'Quantity must be between 1 and {MAX_QTY}.')
        cond=_text(item.get('condition'),'the condition');pta=_text(item.get('pta_status'),'the PTA status');kind=_text(item.get('device_type'),'the device type')
        if product.serialized:
            if not (cond and pta and kind):raise ValidationError(f'Choose the condition, PTA status and type of {product.name}.')
            if item.get('price') in (None,''):raise ValidationError(f'The price of {product.name} is required.')
            price=_amount(item.get('price'))
        else:
            cond=pta=kind='';price=product_price(product)
            if item.get('price') not in (None,'') and _amount(item.get('price'))!=price:raise ValidationError(f'The price of {product.name} changed to PKR {price:,.2f}. Refresh and try again.')
        key=(str(product.pk),cond,pta,kind,price)
        if key in seen:raise ValidationError('The same item appears twice. Combine it into one line with a quantity.')
        seen.add(key)
        if product.serialized:
            pool=phone_pool(product,cond,pta,kind,price)
            if not pool:raise ValidationError(f'{product.name} at PKR {price:,.2f} is no longer available (it may have been sold, or its price changed).')
        if price<=0:raise ValidationError(f'{product.name} has no price.')
        lines.append(dict(product=product,condition=cond,pta_status=pta,device_type=kind,unit_price=price,quantity=qty))
    list(Product.objects.select_for_update().filter(pk__in={l['product'].pk for l in lines}).order_by('pk'))      # one order at a time per product, so the last unit cannot be promised twice
    if ref:                                                  # a twin request with the same reference may have finished while this one waited for the lock
        existing=OnlineOrder.objects.filter(external_ref=ref).first()
        if existing:return existing,False
    subtotal=sum((l['unit_price']*l['quantity'] for l in lines),ZERO)
    if strict:
        due=delivery_fee_for(subtotal,text['province'])
        if fee!=due:raise ValidationError(f'The delivery charge is now PKR {due:,.2f}, not PKR {fee:,.2f}. Prices or delivery changed; refresh and try again.')
    discount,code=_discount(data.get('discount'),subtotal);total=subtotal-discount+fee
    if data.get('expected_total') not in (None,'') and _amount(data['expected_total'])!=total:
        raise ValidationError(f'The total is now PKR {total:,.2f}, not PKR {_amount(data["expected_total"]):,.2f}. Prices or delivery changed; refresh and try again.')
    order=OnlineOrder.objects.create(code=s.code('OL'),source=source,external_ref=ref,customer_name=name,customer_phone=phone,customer_email=text['email'][:180],address=text['address'][:500],
        city=text['city'][:100],payment_method=method,payment_status='Paid' if (data.get('payment_status')=='Paid' and method!='Cash on Delivery') else 'Unpaid',payment_reference=pay_ref[:180],
        delivery_fee=fee,subtotal=subtotal,total=total,notes=notes[:500],created_by=user if getattr(user,'is_authenticated',False) else None,exchange_credit=credit,
        alt_phone=(text['alt_phone'] or text['phone2'])[:40],postal_code=(text['postal_code'] or text['zip'])[:20],
        shipping_method=_text(data['shipping'].get('method') if isinstance(data.get('shipping'),dict) else data.get('shipping_method'),'the shipping method')[:100],discount_amount=discount,discount_code=code,extra=dict(_extra(data,cust),**({'client_ip':client_ip} if client_ip else {})))
    if getattr(user,'is_authenticated',False):order.seen_at=order.confirmed_at=timezone.now();order.confirmed_by=user;order.save(update_fields=['seen_at','confirmed_at','confirmed_by'])      # an order the team typed in after a call is already confirmed
    for l in lines:l['line']=OnlineOrderLine.objects.create(order=order,product=l['product'],condition=l['condition'],pta_status=l['pta_status'],device_type=l['device_type'],quantity=l['quantity'],unit_price=l['unit_price'])
    _reserve(order,plan(lines))
    event(order,user,'Order placed','','Confirmed' if order.confirmed_at else 'Unconfirmed',f'{source} order · {len(lines)} line(s) · total {total}')
    if getattr(user,'is_authenticated',False):s.audit(user,'Online order created',None,order=order.code,total=str(total))
    return order,True


# ------------------------------------------------------------------ order actions
def _lock(order):return OnlineOrder.objects.select_for_update().get(pk=order.pk)


# ------------------------------------------------------------------ order status, fulfilment and history
# The order itself is only ever CONFIRMED (stock reserved from the moment it exists) or CANCELLED. Everything after that is fulfilment
# information that never changes the order status: not dispatched -> dispatched -> in transit -> delivered, or delivery failed -> returned to a shop.
SHIPPED_STAGES = ('Dispatched', 'In Transit')
FAILED = 'Delivery Failed'


def is_fulfilled(order):
    """Fulfilled = the payment has been received AND the item has been handed to the courier with a tracking number.
    Cash on delivery therefore stays unfulfilled after dispatch (tracking added, money not yet collected) until the cash is received."""
    return order.status in (OnlineOrder.DISPATCHED, OnlineOrder.DELIVERED) and bool(order.tracking) and order.payment_status == 'Paid' and not order.returned_on


def order_status(order):
    """The order's main status: Unconfirmed (just arrived, the customer has not been called yet), Confirmed, Fulfilled, Cancelled, or Returned
    (a failed delivery that came back)."""
    if order.status == OnlineOrder.CANCELLED:
        return 'Cancelled'
    if order.status == OnlineOrder.RETURNED or order.returned_on:      # a failed delivery that came back, or everything the customer bought sent back
        return 'Returned'
    if order.status == OnlineOrder.NEW and order.confirmed_at is None:
        return 'Unconfirmed'
    return 'Fulfilled' if is_fulfilled(order) else 'Confirmed'


CANCEL_REASONS = (
    'Customer did not answer the call',
    'Customer changed their mind',
    'Wrong or unreachable phone number',
    'Duplicate order',
    'Ordered by mistake',
    'Delivery is not available in that area',
    'Item no longer available (stock problem)',
    'Customer disagreed with the price or total',
    'Suspected fake order or fraud',
    'Payment was not received in time',
    'Other (write the reason)',
)


@transaction.atomic
def confirm(order, user, note=''):
    """The online manager called the customer and the customer confirmed the order. Only then may it be shipped."""
    order = _lock(order)
    if order.status != OnlineOrder.NEW:
        raise ValidationError('Only a new order can be confirmed.')
    if order.confirmed_at is not None:
        return order
    if order.payment_method != 'Cash on Delivery' and order.payment_status != 'Paid':
        raise ValidationError(f'Confirm that the {order.payment_method} payment has arrived first. A prepaid order is confirmed only once its money is received.')
    order.confirmed_at = timezone.now()
    order.confirmed_by = user if getattr(user, 'is_authenticated', False) else None
    order.seen_at = order.seen_at or order.confirmed_at
    order.save(update_fields=['confirmed_at', 'confirmed_by', 'seen_at', 'modified'])
    event(order, user, 'Confirmed by phone', 'Unconfirmed', 'Confirmed', note)
    s.audit(user, 'Online order confirmed', None, order=order.code)
    return order


# ---- delivery pricing and payment accounts (set by the online manager)
def delivery_fee_for(subtotal, province=''):
    """What the website charges for delivery: free from the free-delivery amount, else the province's own fee, else the default fee."""
    cfg = OnlineSettings.get()
    if cfg.free_delivery_from is not None and subtotal >= cfg.free_delivery_from:
        return ZERO
    rule = OnlineDeliveryRule.objects.filter(province__iexact=' '.join(str(province or '').split())).first() if province else None
    return rule.fee if rule else cfg.delivery_fee


def payment_options():
    """[{method, accounts:[{title, bank_name, account_number, iban, instructions}]}] that customers can choose today. Until the manager adds any
    payment account, the four prepaid methods stay available without account details (how it worked before)."""
    cfg = OnlineSettings.get()
    out = [dict(method='Cash on Delivery', accounts=[])] if cfg.cod_enabled else []
    accounts = list(OnlinePaymentAccount.objects.all())
    if not accounts:
        return out + [dict(method=m, accounts=[]) for m in OnlinePaymentAccount.METHODS]
    for method in OnlinePaymentAccount.METHODS:
        mine = [a for a in accounts if a.method == method and a.enabled]
        if mine:
            out.append(dict(method=method, accounts=[dict(title=a.title, bank_name=a.bank_name, account_number=a.account_number, iban=a.iban, instructions=a.instructions) for a in mine]))
    return out


def store_config():
    """Everything the website needs to price delivery and show payment instructions."""
    cfg = OnlineSettings.get()
    money = lambda v: format(Decimal(v), '.2f')
    return dict(delivery=dict(fee=money(cfg.delivery_fee), free_from=money(cfg.free_delivery_from) if cfg.free_delivery_from is not None else None,
                              provinces=[dict(province=r.province, fee=money(r.fee)) for r in OnlineDeliveryRule.objects.all()]),
                payment_methods=payment_options())


def event(order, user, kind, frm='', to='', note=''):
    """Add one step to the order's history. Times only ever move forward, so the history keeps its true order even when two steps happen in the same instant."""
    now = timezone.now()
    last = OnlineOrderEvent.objects.filter(order=order).order_by('-at').values_list('at', flat=True).first()
    if last is not None and now <= last:
        now = last + timezone.timedelta(microseconds=1)
    return OnlineOrderEvent.objects.create(order=order, at=now, user=user if getattr(user, 'is_authenticated', False) else None, kind=kind, from_stage=frm, to_stage=to, note=str(note or '')[:2000])


def display_stage(order):
    """The fulfilment step shown on screens and on the website. A delivered order that was later returned by the customer shows Returned / Refunded."""
    returns = [] if order.status == OnlineOrder.RETURNED else list(Document.objects.filter(online_order=order, is_return=True, kind__in=list(s.SELL), status='Submitted'))
    if returns:
        refunded = any((r.data.get('returns_flow') or {}).get('refunded') for r in returns)
        return 'Refunded' if refunded else 'Returned'
    if order.status == OnlineOrder.NEW:
        return 'Not dispatched'
    if order.status == OnlineOrder.DISPATCHED:
        return order.stage if order.stage in SHIPPED_STAGES + (FAILED,) else 'Dispatched'
    if order.status == OnlineOrder.RETURNED:
        return 'Returned to shop'
    if order.stage == 'Return Requested':
        return 'Return Requested'
    return order.status


@transaction.atomic
def set_stage(order, user, stage, note=''):
    """Fulfilment steps while the order is on its way: Dispatched, In Transit (also the way back after a failed delivery that is being retried)."""
    order = _lock(order)
    old = display_stage(order)
    if order.status != OnlineOrder.DISPATCHED:
        raise ValidationError('Fulfilment steps can only be changed while the order is dispatched.')
    if stage not in SHIPPED_STAGES:
        raise ValidationError('A dispatched order can be Dispatched or In Transit.')
    if stage == old:
        return order
    order.stage = stage
    if old == FAILED:
        order.failed_reason = ''
    order.save(update_fields=['stage', 'failed_reason', 'modified'])
    event(order, user, 'Fulfilment', old, stage, note)
    s.audit(user, 'Online order fulfilment', None, order=order.code, stage=stage)
    return order


@transaction.atomic
def update_shipment(order, user, courier, tracking, notes=''):
    """Add or correct the courier, tracking number and dispatch notes of an order that is on its way. A delivered or cancelled order is closed."""
    order = _lock(order)
    if order.status != OnlineOrder.DISPATCHED:
        raise ValidationError('Courier and tracking can only be changed while the order is dispatched.')
    courier = ' '.join(str(courier or '').split())[:100]
    tracking = ' '.join(str(tracking or '').split())[:100]
    if not courier or not tracking:
        raise ValidationError('Enter both the courier company and the tracking number.')
    before = f'{order.courier} / {order.tracking}'
    order.courier = courier
    order.tracking = tracking
    order.dispatch_notes = str(notes or order.dispatch_notes)[:1000]
    order.save(update_fields=['courier', 'tracking', 'dispatch_notes', 'modified'])
    event(order, user, 'Shipment', note=f'{before} -> {courier} / {tracking}' + (f' · {notes}' if notes else ''))
    s.audit(user, 'Online order shipment', None, order=order.code, courier=courier, tracking=tracking)
    return order


@transaction.atomic
def request_return(order, user, note):
    """A customer wants to send an order back. Records the request only: no stock moves until the phone is physically returned and inspected
    (Returns & Exchanges puts it in Holding), and a refund is a separate, money-only step."""
    order = _lock(order)
    if order.status not in (OnlineOrder.DISPATCHED, OnlineOrder.DELIVERED):
        raise ValidationError('Only a dispatched or delivered order can have a return request.')
    if not str(note or '').strip():
        raise ValidationError('Write the reason for the return.')
    old = display_stage(order)
    order.stage = 'Return Requested'
    order.return_note = str(note).strip()[:240]
    order.save(update_fields=['stage', 'return_note', 'modified'])
    event(order, user, 'Return requested', old, 'Return Requested', note)
    s.audit(user, 'Online return requested', None, order=order.code)
    return order


def mark_seen(order, user=None):
    """The online team opened the order: it stops counting as new."""
    if order.seen_at is None:
        OnlineOrder.objects.filter(pk=order.pk, seen_at__isnull=True).update(seen_at=timezone.now())
        order.seen_at = timezone.now()


def unseen_count():
    """Orders waiting for the online manager to call the customer (the badge on Online Orders). It drops when the order is confirmed or cancelled."""
    return OnlineOrder.objects.filter(status=OnlineOrder.NEW, confirmed_at__isnull=True).count()


def shop_orders(branch):
    """Online orders that hold stock in this shop (to pack), newest first. A shop sees only what it must pack: no customer money, no other shop."""
    return OnlineOrder.objects.filter(lines__allocations__branch=branch).filter(Q(status=OnlineOrder.NEW, confirmed_at__isnull=False) | Q(status=OnlineOrder.DISPATCHED)).distinct().order_by('-created')


def shop_unseen_count(branch):
    return shop_orders(branch).filter(status=OnlineOrder.NEW).exclude(acks__branch=branch).count()


def acknowledge(order, branch, user):
    OnlineOrderAck.objects.get_or_create(order=order, branch=branch, defaults=dict(user=user if getattr(user, 'is_authenticated', False) else None))


@transaction.atomic
def delivery_failed(order, user, reason):
    """The courier could not deliver (refused, unreachable, wrong address). The order stays on record exactly as it was, with its tracking; nothing
    is restored to stock until the phone is physically back in a shop and has been checked (receive_returned)."""
    order = _lock(order)
    if order.status != OnlineOrder.DISPATCHED:
        raise ValidationError('Only a dispatched order can have a failed delivery.')
    if not str(reason or '').strip():
        raise ValidationError('Write why the delivery failed (customer refused, not reachable, wrong address…).')
    old = display_stage(order)
    order.stage = FAILED
    order.failed_reason = str(reason).strip()[:240]
    order.save(update_fields=['stage', 'failed_reason', 'modified'])
    event(order, user, 'Delivery failed', old, FAILED, order.failed_reason)
    s.audit(user, 'Online delivery failed', None, order=order.code, reason=order.failed_reason)
    return order


@transaction.atomic
def receive_returned(order, user, warehouse, condition, resale='hold', note='', courier_charge=None, expense_account=None, method=None, when=None):
    """A failed delivery came back to a shop. Posts the sales return (revenue, receivable and cost of goods sold are reversed; the phone goes into the
    chosen shop at its ORIGINAL cost, in Holding), then applies the inspection result:
      approve  -> the phone is available for sale again      hold -> stays in Holding until a manager accepts it
      damaged  -> not for sale (Unavailable); its value stays on the books until the owner writes it off
    The supplier's invoice is never touched. Courier return charges are recorded as a separate online expense."""
    from . import returns
    order = _lock(order)
    if order.status != OnlineOrder.DISPATCHED or order.stage != FAILED:
        raise ValidationError('Record the failed delivery first; only then can the returned phone be received.')
    if resale not in ('approve', 'hold', 'damaged'):
        raise ValidationError('Choose what happens to the phone: approve for resale, keep on hold, or mark damaged.')
    if not str(condition or '').strip():
        raise ValidationError('Write the condition of the returned phone.')
    if warehouse is None or not warehouse.enabled or warehouse.branch.is_online or not warehouse.branch.enabled:
        raise ValidationError('Choose the shop that received the phone.')
    invoice = Document.objects.select_for_update().filter(online_order=order, kind__in=list(s.SELL), is_return=False, status='Submitted').first()
    if invoice is None:
        raise ValidationError('This order has no sales invoice to return.')
    lines = returns.sale_lines(invoice)
    device_ids = [str(l.device_id) for l in lines if l.device_id]
    quantities = {str(l.pk): l.quantity for l in lines if not l.device_id}
    credit = s.submit(returns.create(invoice, user, device_ids, condition, order.failed_reason or 'Delivery failed', quantities or None, warehouse=warehouse), user)
    invoice.refresh_from_db()
    owed = min(invoice.outstanding, -credit.outstanding)
    if owed > 0:
        returns.apply_credit(credit, owed, user)
    cancel_uncollected_charges(Document.objects.select_for_update().get(pk=invoice.pk), order, user)
    for d in Device.objects.select_for_update(of=('self',)).filter(pk__in=device_ids):
        d.details = dict(d.details or {}, inspection_notes=f'Returned delivery {order.code}: {condition}' + (f' · {note}' if note else ''))
        if resale == 'approve':
            d.status = 'Available'
            d.inspection_status = 'Passed'
            d.inspection_date = timezone.localdate()
        elif resale == 'damaged':
            d.status = 'Unavailable'
            d.inspection_status = 'Failed'
            d.inspection_date = timezone.localdate()
        d.save()
        s.audit(user, 'Returned delivery inspected', None, order=order.code, device=d.code, result=resale)
    if courier_charge not in (None, '', 0, '0'):
        from . import money, money_views
        amount = s.payment_money(courier_charge)
        if amount > 0:
            if expense_account is None or not method:
                raise ValidationError('Choose the expense category and the payment method for the courier charge.')
            money_views._ensure_chart(user)
            money.record_voucher(user, online_branch(), 'expense', expense_account, amount, method, when or timezone.localdate(), f'Courier return charges {order.code}')
    old = display_stage(order)
    order.status = OnlineOrder.RETURNED
    order.stage = 'Returned'
    order.returned_on = timezone.localdate()
    order.save(update_fields=['status', 'stage', 'returned_on', 'modified'])
    event(order, user, 'Returned to shop', old, 'Returned to shop', f'{warehouse.branch.name} · {condition} · {resale}' + (f' · {note}' if note else '') + f' · credit {credit.code}')
    return credit


def cancel_uncollected_charges(invoice, order, user):
    """After the whole parcel is back, anything still owed on its invoice is a charge that is not stock (the delivery charge) and that the
    customer will never pay: they refused the parcel. Take it back out of income (and output tax) and off the customer's account, so a
    refused cash-on-delivery order leaves no debt and no revenue. A charge that was already paid (a prepaid order) is not touched."""
    rows = [line.snapshot.get('pricing') or {} for line in invoice.lines.select_related('product') if not line.product.stock_item]
    net = sum((s.money(r.get('net')) for r in rows), ZERO)
    tax = sum((s.money(r.get('tax')) for r in rows), ZERO)
    amount = min(invoice.outstanding, net + tax)
    if amount <= 0:
        return None
    tax_part = s.money(amount * tax / (net + tax)) if tax else ZERO
    doc = s.new_doc('Journal Entry', invoice.branch, customer=invoice.customer, reference=invoice, posted_by=user, channel='Online', online_order=order,
                    notes=f'Delivery charge of online order {order.code} cancelled: the parcel was not delivered', data=dict(charge_cancelled=str(invoice.pk), amount=str(amount)))
    s.open_date(doc)
    s.entry(doc, 'sales', debit=amount - tax_part)
    if tax_part:
        s.entry(doc, pricing.tax_account(invoice), debit=tax_part)
    s.entry(doc, 'receivable', credit=amount, party=invoice.customer, reference=invoice)
    invoice.outstanding -= amount
    invoice.data.setdefault('charges_cancelled', []).append(dict(amount=str(amount), journal=str(doc.pk), date=str(doc.posting_date)))
    invoice.save(update_fields=['outstanding', 'data', 'modified'])
    doc.total = amount
    doc.status = 'Submitted'
    doc.save()
    s.balanced(doc)
    s.audit(user, 'Online delivery charge cancelled', doc, order=order.code, amount=str(amount))
    event(order, user, 'Delivery charge cancelled', note=f'PKR {amount:,.2f} that was never collected')
    return doc


def after_return(doc):
    """A sales return was posted for an online order. When every unit of every invoice of the order has now come back, the order itself becomes
    Returned (it stops counting as Fulfilled), shows up under Returned in the lists and in the customer's tracking, and keeps its history.
    A part return leaves the order as it is. A failed delivery is closed by receive_returned instead."""
    from . import returns
    order = OnlineOrder.objects.select_for_update().get(pk=doc.online_order_id)
    if order.returned_on or order.stage == FAILED or order.status not in (OnlineOrder.DISPATCHED, OnlineOrder.DELIVERED):
        return
    for invoice in Document.objects.filter(online_order=order, kind__in=list(s.SELL), is_return=False, status='Submitted'):
        try:
            lines = returns.sale_lines(invoice)
            used = returns.used_quantities(invoice, lines)
        except ValidationError:
            return
        if any(used[str(line.pk)] < line.quantity for line in lines):
            return
    old = display_stage(order)
    order.returned_on = timezone.localdate()
    order.stage = 'Returned'
    order.save(update_fields=['returned_on', 'stage', 'modified'])
    event(order, doc.posted_by, 'Returned by the customer', old, 'Returned', f'Return {doc.code}')


def returned_devices(order):
    """Phones of this order that came back and are waiting in Holding for someone to check them."""
    ids = Line.objects.filter(document__online_order=order, document__is_return=True, document__status='Submitted', device__isnull=False).values_list('device_id', flat=True)
    return list(Device.objects.filter(pk__in=list(ids), status='Holding').select_related('product', 'branch'))


@transaction.atomic
def accept_returned(order, user, note=''):
    """The returned phone was checked: it goes back on sale in the shop that holds it (and on the website). The same inspection step as accepting
    any returned phone into stock, done from the order."""
    order = _lock(order)
    wanted = [d.pk for d in returned_devices(order)]
    devices = list(Device.objects.select_for_update(of=('self',)).filter(pk__in=wanted, status='Holding'))
    if not devices:
        raise ValidationError('No returned phone of this order is waiting to go back on sale.')
    for d in devices:
        d.status = 'Available'
        d.inspection_status = 'Passed'
        d.inspection_date = timezone.localdate()
        d.save()
        s.audit(user, 'Returned phone accepted into stock', None, order=order.code, device=d.code, shop=d.branch.name)
    event(order, user, 'Returned phone back on sale', note=', '.join(f'{d.code} in {d.branch.name}' for d in devices) + (f' · {note}' if note else ''))
    return devices


def delivery_charge_of(invoice):
    """(net, tax) of the delivery charge on an online invoice: the lines that are not stock."""
    rows = [line.snapshot.get('pricing') or {} for line in invoice.lines.select_related('product') if not line.product.stock_item]
    return sum((s.money(r.get('net')) for r in rows), ZERO), sum((s.money(r.get('tax')) for r in rows), ZERO)


def delivery_refundable(order):
    """The delivery charge the customer paid on an order that came back whole after delivery, if it has not been given back yet. None otherwise."""
    if not order.returned_on or order.status == OnlineOrder.RETURNED:
        return None
    invoice = Document.objects.filter(online_order=order, kind__in=list(s.SELL), is_return=False, status='Submitted').first()
    if invoice is None or invoice.data.get('charges_cancelled') or invoice.data.get('charges_refunded'):
        return None
    net, tax = delivery_charge_of(invoice)
    return net + tax if net + tax > 0 else None


@transaction.atomic
def refund_delivery_charge(order, user, note=''):
    """Give the delivery charge back after the whole order was returned (the customer returned a faulty phone, or you decided to). The charge comes out of the
    Online Store's income (and output tax) and becomes more credit owed to the customer on the return, to be refunded with the rest."""
    order = _lock(order)
    amount = delivery_refundable(order)
    if amount is None:
        raise ValidationError('There is no delivery charge to refund on this order (it is not fully returned, or it was already refunded).')
    invoice = Document.objects.select_for_update().filter(online_order=order, kind__in=list(s.SELL), is_return=False, status='Submitted').first()
    credit = Document.objects.select_for_update().filter(online_order=order, kind__in=list(s.SELL), is_return=True, status='Submitted').order_by('-created').first()
    if credit is None:
        raise ValidationError('This order has no return to attach the refund to.')
    net, tax = delivery_charge_of(invoice)
    doc = s.new_doc('Journal Entry', invoice.branch, customer=invoice.customer, reference=credit, posted_by=user, channel='Online', online_order=order,
                    notes=f'Delivery charge of online order {order.code} refunded after the order was returned' + (f' · {note}' if note else ''),
                    data=dict(charge_cancelled=str(invoice.pk), amount=str(amount), refunded_after_return=True))
    s.open_date(doc)
    s.entry(doc, 'sales', debit=net)
    if tax:
        s.entry(doc, pricing.tax_account(invoice), debit=tax)
    s.entry(doc, 'receivable', credit=amount, party=invoice.customer, reference=credit)
    credit.outstanding -= amount
    credit.save(update_fields=['outstanding', 'modified'])
    invoice.data['charges_refunded'] = dict(amount=str(amount), journal=str(doc.pk), date=str(doc.posting_date))
    invoice.save(update_fields=['data', 'modified'])
    doc.total = amount
    doc.status = 'Submitted'
    doc.save()
    s.balanced(doc)
    s.audit(user, 'Online delivery charge refunded', doc, order=order.code, amount=str(amount))
    event(order, user, 'Delivery charge refunded', note=f'PKR {amount:,.2f} added to what is owed back to the customer')
    return doc


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
    release(order);_reserve(order,plan(lines,prefer=branch.pk));event(order,user,'Moved to another shop','','',branch.name)
    s.audit(user,'Online order reassigned',None,order=order.code,shop=branch.name);return order


@transaction.atomic
def cancel(order,user,reason):
    order=_lock(order)
    if order.status!=OnlineOrder.NEW:raise ValidationError('Only a new order can be cancelled. For a dispatched order use Return / exchange.')
    if not str(reason or '').strip():raise ValidationError('Give a reason for cancelling.')
    old=display_stage(order);release(order);order.status=OnlineOrder.CANCELLED;order.stage='Cancelled';order.cancel_reason=str(reason).strip()[:240];order.save(update_fields=['status','stage','cancel_reason','modified']);event(order,user,'Cancelled',old,'Cancelled',order.cancel_reason)
    s.audit(user,'Online order cancelled',None,order=order.code,reason=order.cancel_reason);return order


@transaction.atomic
def mark_paid(order,user,reference=''):
    order=_lock(order)
    if order.status in (OnlineOrder.CANCELLED,):raise ValidationError('This order is cancelled.')
    if order.payment_method=='Cash on Delivery':raise ValidationError('Cash on delivery is collected when the order is delivered.')
    if order.status!=OnlineOrder.NEW:raise ValidationError('Payment can only be confirmed before dispatch.')
    order.payment_status='Paid';order.payment_reference=str(reference or order.payment_reference)[:180]
    order.save(update_fields=['payment_status','payment_reference','modified']);event(order,user,'Payment confirmed','','',order.payment_reference)
    s.audit(user,'Online payment confirmed',None,order=order.code,method=order.payment_method);return order


def party_for(branch,order):
    """The online customer, in the Online Store's own customer list (found by phone, else created). Shops never see them."""
    if order.exchange_credit_id and order.exchange_credit.branch_id==branch.pk and order.exchange_credit.customer_id:return order.exchange_credit.customer
    mine=Party.objects.filter(branch=branch,kind='Customer')
    found=mine.filter(phone=order.customer_phone).first()
    if not found:                                             # 0300 1234567 and +92 300 1234567 are one person: compare the last ten digits
        tail=''.join(ch for ch in order.customer_phone if ch.isdigit())[-10:]
        if len(tail)==10:found=mine.filter(phone__endswith=tail).order_by('created').first()
    if found:return found
    name=order.customer_name
    if Party.objects.filter(branch=branch,kind='Customer',name=name).exists():name=f'{name} ({order.customer_phone})'[:180]
    return Party.objects.create(code=s.code('CUST'),name=name,kind='Customer',phone=order.customer_phone,city=order.city,address=order.address,email=order.customer_email,branch=branch)


def delivery_product():
    p=Product.objects.filter(code=DELIVERY_CODE).first()
    return p or Product.objects.create(code=DELIVERY_CODE,name='Delivery charge',group='Online',serialized=False,stock_item=False,selling_price=ZERO,enabled=False)


def shop_method(branch,order):
    name=METHODS[order.payment_method];conf=ShopSettings.objects.filter(branch=branch).first()
    if not conf or name not in conf.payment_methods:raise ValidationError(f'The Online Store has no “{name}” payment method set up, so it cannot receive this order’s money.')
    return name


@transaction.atomic
def dispatch(order,user,courier='',tracking='',notes=''):
    """Post the sale. One invoice in the Online Store's books holds the whole order: the customer, the revenue, the money. The stock leaves the
    shops that held it, and each shop is owed its cost by the Online Store (see services.post_supply). Prepaid orders are received at once;
    cash on delivery stays owing until delivery."""
    order=_lock(order)
    if order.status!=OnlineOrder.NEW:raise ValidationError('Only a new order can be dispatched.')
    if order.confirmed_at is None:raise ValidationError('Call the customer and confirm the order before shipping it.')
    if order.payment_method!='Cash on Delivery' and order.payment_status!='Paid':raise ValidationError('Confirm that the payment arrived before dispatching.')
    list(Product.objects.select_for_update().filter(pk__in=OnlineOrderLine.objects.filter(order=order).values('product_id')).order_by('pk'))      # an arriving order waits here, so it never counts this order's accessories as both reserved and still on the shelf
    allocations=list(OnlineAllocation.objects.select_for_update(of=('self',)).filter(line__order=order).select_related('line','product','device','branch'))
    if not allocations:raise ValidationError('This order has no reserved stock.')
    for d in Device.objects.select_for_update().filter(pk__in=[a.device_id for a in allocations if a.device_id]):
        if d.status!='Reserved':raise ValidationError(f'{d.code} is no longer reserved for this order. Cancel the order and create it again.')
    for a in allocations:                     # the reservation ends here: the invoice sells the exact phones
        if a.device_id:Device.objects.filter(pk=a.device_id).update(status='Available')
    OnlineAllocation.objects.filter(pk__in=[a.pk for a in allocations if not a.device_id]).delete()   # accessories: counted again by the invoice itself
    branch=online_branch();person=shopdefaults.shop_person(branch)
    method=shop_method(branch,order) if order.payment_method!='Cash on Delivery' else None
    doc=s.new_doc('Sales Invoice',branch,customer=party_for(branch,order),seller=person,channel='Online',online_order=order,notes=f'Online order {order.code}'+(f' · {order.notes}' if order.notes else ''),
                  data=dict(channel='Online',online_order=order.code))
    pricing.inputs(doc,'amount',str(order.discount_amount or 0),'','');doc.save()
    for a in allocations:
        if a.device_id:Line.objects.create(document=doc,product=a.product,device=a.device,warehouse=a.device.warehouse,quantity=1,rate=a.line.unit_price)
        else:Line.objects.create(document=doc,product=a.product,warehouse=shopdefaults.warehouse(a.branch),quantity=a.quantity,rate=a.line.unit_price)
    if order.delivery_fee>0:Line.objects.create(document=doc,product=delivery_product(),warehouse=shopdefaults.warehouse(branch),quantity=1,rate=order.delivery_fee)
    doc.save()
    sale=s.checkout(doc,user,[],person)
    if order.exchange_credit_id:
        from . import returns
        returns.apply_to_sale(Document.objects.get(pk=order.exchange_credit_id),sale,user);sale.refresh_from_db()
    if method and sale.outstanding>0:_receive(sale,order,method,user,order.payment_reference)
    sale.refresh_from_db()
    old=display_stage(order)
    order.status=OnlineOrder.DISPATCHED;order.stage='Dispatched';order.dispatched_on=timezone.localdate();order.courier=str(courier or '')[:100];order.tracking=str(tracking or '')[:100];order.dispatch_notes=str(notes or '')[:1000]
    order.save(update_fields=['status','stage','dispatched_on','courier','tracking','dispatch_notes','modified']);event(order,user,'Dispatched',old,'Dispatched',f'{order.courier} {order.tracking}'.strip()+(f' · {notes}' if notes else ''))
    s.audit(user,'Online order dispatched',None,order=order.code,invoices=[sale.code]);return [sale]


def _receive(inv,order,method,user,reference=''):
    pe=s.new_doc('Payment Entry',inv.branch,customer=inv.customer,reference=inv,handler=shopdefaults.shop_person(inv.branch),channel='Online',online_order=order,posting_date=timezone.localdate(),
                 data=dict(amount=str(inv.outstanding),direction='Receive',mode=method,reference=reference))
    return s.submit(pe,user)


@transaction.atomic
def deliver(order,user,method='Cash'):
    """Delivered. Cash on delivery is collected now into the chosen Online Store account."""
    order=_lock(order)
    if order.status!=OnlineOrder.DISPATCHED:raise ValidationError('Only a dispatched order can be marked delivered.')
    if order.stage==FAILED:raise ValidationError('This delivery failed. Mark it In Transit again if the courier retries, or receive the returned phone.')
    for inv in Document.objects.select_for_update().filter(online_order=order,kind__in=list(s.SELL),is_return=False,status='Submitted'):
        if inv.outstanding>0:
            conf=ShopSettings.objects.filter(branch=inv.branch).first()
            if not conf or method not in conf.payment_methods:raise ValidationError(f'The Online Store has no “{method}” payment method set up.')
            _receive(inv,order,method,user,f'Delivery of {order.code}')
    old=display_stage(order)
    order.status=OnlineOrder.DELIVERED;order.stage='Delivered';order.payment_status='Paid';order.delivered_on=timezone.localdate()
    order.save(update_fields=['status','stage','payment_status','delivered_on','modified']);event(order,user,'Delivered',old,'Delivered');s.audit(user,'Online order delivered',None,order=order.code);return order


def invoices(order):
    return Document.objects.filter(online_order=order,kind__in=list(s.SELL),is_return=False).select_related('branch','customer').order_by('created')
