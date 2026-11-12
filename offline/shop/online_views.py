"""Screens for online orders and online stock (owner and the online-store login), plus the website's order API."""
import hmac
import json
from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.db.models import Q
from django.http import Http404,JsonResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from .models import Account,Branch,Device,Document,OnlineAllocation,OnlineOrder,Product,ShopSettings
from . import online_access,online_accounts,online_orders as oo,services as s


def _guard(request):
    if not online_access.can_use_online(request.user):raise PermissionDenied('Only the owner and the online-store login can use this page.')


def _err(error):
    from .views import error_text
    return error_text(error)


# ------------------------------------------------------------------ orders
@login_required
def orders(request):
    _guard(request)
    q=request.GET.get('q','').strip();status=request.GET.get('status','')
    rows=OnlineOrder.objects.all().prefetch_related('lines__product','lines__allocations__branch')
    NEW,DISP,DELIV=OnlineOrder.NEW,OnlineOrder.DISPATCHED,OnlineOrder.DELIVERED
    fulfilled=Q(status__in=(DISP,DELIV),payment_status='Paid')&~Q(tracking='')
    kinds={'Unconfirmed':Q(status=NEW,confirmed_at__isnull=True),'Confirmed':(Q(status=NEW,confirmed_at__isnull=False)|Q(status__in=(DISP,DELIV)))&~fulfilled,'Fulfilled':fulfilled,
           'Cancelled':Q(status=OnlineOrder.CANCELLED),'Returned':Q(status=OnlineOrder.RETURNED)}
    counts={name:OnlineOrder.objects.filter(cond).count() for name,cond in kinds.items()}
    if status in kinds:rows=rows.filter(kinds[status])
    elif status=='history':rows=rows.filter(kinds['Fulfilled']|kinds['Cancelled']|kinds['Returned'])
    elif status!='all':rows=rows.filter(kinds['Unconfirmed']|kinds['Confirmed']);status=''      # the working list: orders to call, to ship, or shipped and awaiting payment
    if q:
        digits=''.join(ch for ch in q if ch.isdigit())
        match=Q(code__icontains=q)|Q(customer_name__icontains=q)|Q(external_ref__icontains=q)|Q(tracking__icontains=q)
        if len(digits)>=4:match|=Q(customer_phone__contains=digits)
        rows=rows.filter(match)
    from .views import paginate
    page,pager=paginate(request,list(rows),40)
    data=[dict(o=o,primary=oo.order_status(o),new=False,stage=oo.display_stage(o),items=', '.join(f'{l.product.name} × {l.quantity}' for l in o.lines.all())[:90],shops=sorted({a.branch.name for l in o.lines.all() for a in l.allocations.all()})) for o in page.object_list]
    return render(request,'online_orders.html',dict(title='Online Orders',rows=data,page_obj=page,pager_query=pager,q=q,status=status,counts=counts))


def _candidates(order):
    """Shops that could supply the whole of a New order, counting what this order already holds as available."""
    mine={}
    for a in OnlineAllocation.objects.filter(line__order=order):mine[(a.line_id,a.branch_id)]=mine.get((a.line_id,a.branch_id),0)+int(a.quantity)
    result=None
    for l in order.lines.select_related('product'):
        have={}
        if l.product.serialized:
            for d in Device.objects.filter(product=l.product,status='Available',branch__enabled=True,condition=l.condition,pta_status=l.pta_status,device_type=l.device_type,warehouse__isnull=False):
                if oo.device_price(d,l.product)==l.unit_price:have[d.branch_id]=have.get(d.branch_id,0)+1
        else:have={b:int(q) for b,q in oo.accessory_pool(l.product).items()}
        for (line_id,b),n in mine.items():
            if line_id==l.pk:have[b]=have.get(b,0)+n
        ok={b for b,n in have.items() if n>=l.quantity};result=ok if result is None else result&ok
    return Branch.objects.filter(pk__in=result or []).order_by('name')


@login_required
def order(request,pk):
    _guard(request);o=get_object_or_404(OnlineOrder.objects.select_related('exchange_credit'),pk=pk);oo.mark_seen(o,request.user)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='confirm':oo.confirm(o,request.user,request.POST.get('note',''));messages.success(request,'Order confirmed. It can now be shipped.')
            elif action=='cancel':
                choice=request.POST.get('reason_choice','').strip();note=request.POST.get('reason_note','').strip()
                if choice not in oo.CANCEL_REASONS:raise ValidationError('Choose why the order is cancelled.')
                if choice.startswith('Other') and not note:raise ValidationError('Write the reason for cancelling.')
                oo.cancel(o,request.user,(choice if not choice.startswith('Other') else '')+(' — ' if note and not choice.startswith('Other') else '')+note);messages.success(request,'Order cancelled. The stock is available again.')
            elif action=='paid':oo.mark_paid(o,request.user,request.POST.get('reference',''));messages.success(request,'Payment confirmed.')
            elif action=='reassign':oo.reassign(o,request.user,get_object_or_404(Branch,pk=request.POST.get('shop'),enabled=True));messages.success(request,'The order will be supplied from that shop.')
            elif action=='dispatch':
                courier=' '.join(request.POST.get('courier','').split());tracking=' '.join(request.POST.get('tracking','').split())
                if not courier or not tracking:raise ValidationError('Enter the courier company and the tracking number (or the rider name) before dispatching.')
                made=oo.dispatch(o,request.user,courier,tracking,request.POST.get('notes',''))
                messages.success(request,f'Dispatched. {len(made)} invoice{"s" if len(made)!=1 else ""} posted in the supplying shop{"s" if len(made)!=1 else ""}.')
            elif action=='stage':oo.set_stage(o,request.user,request.POST.get('stage',''),request.POST.get('note',''));messages.success(request,'Order updated.')
            elif action=='shipment':oo.update_shipment(o,request.user,request.POST.get('courier',''),request.POST.get('tracking',''),request.POST.get('notes',''));messages.success(request,'Courier and tracking saved.')
            elif action=='return_request':oo.request_return(o,request.user,request.POST.get('note',''));messages.success(request,'Return request recorded. Nothing moves in stock until the phone is received and inspected.')
            elif action=='delivery_failed':oo.delivery_failed(o,request.user,request.POST.get('reason',''));messages.success(request,'Failed delivery recorded. Nothing returns to stock until the phone is received back in a shop.')
            elif action=='receive_returned':
                from datetime import date as _date
                from .models import Warehouse
                warehouse=get_object_or_404(Warehouse,pk=request.POST.get('warehouse'),enabled=True)
                charge=request.POST.get('courier_charge','').strip();category=None
                if charge:category=get_object_or_404(Account,pk=request.POST.get('category'))
                oo.receive_returned(o,request.user,warehouse,request.POST.get('condition',''),request.POST.get('resale','hold'),request.POST.get('note',''),charge or None,category,request.POST.get('method'),_date.today())
                messages.success(request,'Returned phone received. The sale is reversed and the phone is back in that shop at its original cost.')
            elif action=='deliver':oo.deliver(o,request.user,request.POST.get('method','Cash'));messages.success(request,'Marked delivered; any cash on delivery is now recorded as received.')
            else:raise ValidationError('Unknown action.')
        except (ValidationError,IntegrityError,ValueError) as error:messages.error(request,_err(error))
        return redirect('online_order',pk=o.pk)
    o.refresh_from_db()
    lines=oo.order_lines(o);invoices=list(oo.invoices(o))
    returns=list(Document.objects.filter(online_order=o,is_return=True,kind__in=list(s.SELL)).exclude(status='Cancelled').select_related('branch').order_by('created'))
    methods=sorted({m for b in {i.branch for i in invoices} for m in (ShopSettings.objects.filter(branch=b).first().payment_methods if ShopSettings.objects.filter(branch=b).exists() else {})}) or ['Cash']
    def flat(v):
        if isinstance(v,dict):return ', '.join(f'{k}: {flat(x)}' for k,x in v.items())
        if isinstance(v,list):return ', '.join(flat(x) for x in v)
        return str(v)
    extra_rows=[(str(k).replace('_',' ').capitalize(),flat(v)) for k,v in (o.extra or {}).items() if v not in ('',None,[],{})]
    return render(request,'online_order.html',dict(title=o.code,o=o,stage=oo.display_stage(o),primary=oo.order_status(o),events=list(o.events.select_related('user')),shipped_stages=oo.SHIPPED_STAGES,failed=oo.FAILED,receiving=_receiving(o),cancel_reasons=oo.CANCEL_REASONS,unconfirmed=o.status==OnlineOrder.NEW and o.confirmed_at is None,extra_rows=extra_rows,lines=lines,invoices=invoices,returns=returns,methods=methods,
        candidates=list(_candidates(o)) if o.status==OnlineOrder.NEW and not o.exchange_credit_id else [],prepaid=o.payment_method!='Cash on Delivery',
        outstanding=sum((i.outstanding for i in invoices if i.outstanding>0),Decimal(0))))


def _receiving(o):
    """What the 'phone came back' form needs: the shops that can receive it, the expense categories and the Online Store's payment methods."""
    if o.status!=OnlineOrder.DISPATCHED or o.stage!=oo.FAILED:return None
    from . import money_views
    from .models import Warehouse
    online=oo.online_branch()
    return dict(warehouses=[(w.pk,w.branch.name) for w in Warehouse.objects.filter(enabled=True,branch__enabled=True,branch__is_online=False).select_related('branch').order_by('branch__name')],
                categories=list(money_views._accounts('Expense',('COA-6950','COA-5200')).values_list('pk','name')) if Account.objects.filter(code='COA-6750').exists() else [],
                methods=list(ShopSettings.objects.get(branch=online).payment_methods))


def _key(v):return f"{v['product'].pk}~{v['condition']}~{v['pta_status']}~{v['device_type']}~{v['price']}"


@login_required
def order_new(request):
    """The online team types an order taken by phone or WhatsApp. Also the way to hand an exchange customer their replacement."""
    _guard(request)
    credit=None
    if request.GET.get('credit') or request.POST.get('credit'):
        credit=get_object_or_404(Document,pk=request.GET.get('credit') or request.POST.get('credit'),is_return=True,kind__in=list(s.SELL),channel='Online',status='Submitted')
    allv=oo.variants(published_only=False)
    if request.method=='POST':
        try:
            wanted=dict(zip(request.POST.getlist('variant'),request.POST.getlist('qty')));by={_key(v):v for v in allv};items=[]
            for key,qty in wanted.items():
                if not str(qty).strip() or str(qty).strip()=='0':continue
                v=by.get(key)
                if not v:raise ValidationError('One of the items is no longer available. Reload and try again.')
                items.append(dict(product_id=str(v['product'].pk),condition=v['condition'],pta_status=v['pta_status'],device_type=v['device_type'],price=str(v['price']),quantity=qty))
            if not items:raise ValidationError('Choose at least one item and quantity.')
            data=dict(customer=dict(name=request.POST.get('name',''),phone=request.POST.get('phone',''),address=request.POST.get('address',''),city=request.POST.get('city','')),payment_method=request.POST.get('payment_method','Cash on Delivery'),
                      payment_status='Paid' if request.POST.get('paid')=='on' else 'Unpaid',payment_reference=request.POST.get('payment_reference',''),delivery_fee=request.POST.get('delivery_fee') or 0,notes=request.POST.get('notes',''),items=items)
            key=request.POST.get('request_key','')
            data['external_ref']='MANUAL-'+key if key else None
            with transaction.atomic():created,_=oo.create(data,request.user,'Manual',credit)
            messages.success(request,f'Order {created.code} created and the stock is reserved.');return redirect('online_order',pk=created.pk)
        except (ValidationError,IntegrityError,ValueError) as error:messages.error(request,_err(error))
    import uuid
    q=request.GET.get('q','').strip().lower()
    rows=[dict(v,key=_key(v),shops=', '.join(f"{Branch.objects.get(pk=b).name} {n}" for b,n in v['per_shop'].items())) for v in allv if not q or q in (v['product'].name+' '+v['condition']).lower()]
    rows.sort(key=lambda r:(r['product'].name,r['condition'],r['price']))
    return render(request,'online_order_new.html',dict(title='New online order',rows=rows,credit=credit,methods=list(oo.METHODS),request_key=uuid.uuid4(),q=q,
        prefill=dict(name=credit.customer.name if credit else '',phone=credit.customer.phone if credit else '')))


# ------------------------------------------------------------------ online stock
@login_required
def stock(request):
    """Everything the website can sell, one line per phone model (never one per variant), with the quantity in each shop. Open a model for its variants."""
    _guard(request)
    from . import online_store
    shops=oo.shops();q=request.GET.get('q','').strip()
    rows=[]
    for fam in online_store.catalog(q):
        per=dict(fam['per_shop'])
        rows.append(dict(fam,cells=[per.get(b.name,0) for b in shops],variant_count=len({(r['product'].pk) for r in fam['rows']})))
    from .views import paginate
    page,pager=paginate(request,rows,60)
    return render(request,'online_stock.html',dict(title='Online Stock',rows=page.object_list,page_obj=page,pager_query=pager,shops=shops,q=q,total=sum(r['stock'] for r in rows)))


@login_required
def accounts(request):
    """The Online Store's books: sales, money, what it owes the shops, and the forms to record online expenses, owner money and payments to shops."""
    _guard(request)
    from datetime import date
    from . import money,money_views
    online=oo.online_branch();methods=list(ShopSettings.objects.get(branch=online).payment_methods)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            when=date.fromisoformat(request.POST.get('date') or str(timezone.localdate()))
            money_views._ensure_chart(request.user)
            with transaction.atomic():
                if action=='expense':
                    category=get_object_or_404(money_views._accounts('Expense',('COA-6950','COA-5200')),pk=request.POST.get('category'))
                    money.record_voucher(request.user,online,'expense',category,request.POST.get('amount'),request.POST.get('method'),when,request.POST.get('description') or category.name)
                    messages.success(request,'Online expense recorded.')
                elif action=='capital':
                    money.record_voucher(request.user,online,'capital',money_views._by_code('COA-3100'),request.POST.get('amount'),request.POST.get('method'),when,request.POST.get('description') or 'Money put into the Online Store')
                    messages.success(request,'Money put into the Online Store recorded.')
                elif action=='pay_shop':
                    online_accounts.pay_shop(request.user,get_object_or_404(Branch,pk=request.POST.get('shop'),is_online=False),request.POST.get('amount'),request.POST.get('method'),request.POST.get('shop_method'),when,request.POST.get('description',''))
                    messages.success(request,'Payment to the shop recorded in both sets of books.')
                else:raise ValidationError('Unknown action.')
        except (ValidationError,ValueError,IntegrityError) as error:messages.error(request,_err(error))
        return redirect('online_accounts')
    g=request.GET
    try:start=date.fromisoformat(g['from']) if g.get('from') else None;end=date.fromisoformat(g['to']) if g.get('to') else None
    except ValueError:start=end=None
    shop_methods={b.pk:list(ShopSettings.objects.filter(branch=b).values_list('payment_methods',flat=True).first() or []) for b in Branch.objects.filter(enabled=True,is_online=False)}
    owner=request.user.is_superuser
    return render(request,'online_accounts.html',dict(title='Online Accounts',data=online_accounts.summary(start,end),start=g.get('from',''),end=g.get('to',''),profit=owner,owner=owner,methods=methods,
        categories=list(money_views._accounts('Expense',('COA-6950','COA-5200')).values_list('pk','name')) if Account.objects.filter(code='COA-6750').exists() else [],
        shops=list(Branch.objects.filter(enabled=True,is_online=False).order_by('name')),all_shop_methods=sorted({m for ms in shop_methods.values() for m in ms}),today=timezone.localdate()))


# ------------------------------------------------------------------ the website's API
def _authorized(request):
    key=getattr(settings,'ONLINE_API_KEY','') or ''
    sent=request.headers.get('X-API-Key','')
    return bool(key) and hmac.compare_digest(key.encode(),sent.encode())


@csrf_exempt
@require_http_methods(['POST'])
def api_order(request):
    """POST /api/online/orders/  (header X-API-Key). Creates the order and reserves the stock; sending the same external_ref again returns the same order."""
    if not _authorized(request):return JsonResponse({'error':'Invalid or missing API key.'},status=401)
    try:data=json.loads(request.body or b'{}')
    except ValueError:return JsonResponse({'error':'Send JSON.'},status=400)
    if not isinstance(data,dict):return JsonResponse({'error':'Send a JSON object.'},status=400)
    try:
        with transaction.atomic():order,created=oo.create(data,None,'Website',strict=True)
    except (ValidationError,IntegrityError,ValueError) as error:
        text=_err(error);conflict=any(w in text for w in ('no longer','total is now','Only ','does not exist any more','not for sale online','changed to PKR','delivery charge is now','is not available right now'))
        return JsonResponse({'error':text},status=409 if conflict else 400)
    return JsonResponse(dict(order=order.code,external_ref=order.external_ref,status=order.status,subtotal=str(order.subtotal),delivery_fee=str(order.delivery_fee),total=str(order.total),created=created),status=201 if created else 200)


@csrf_exempt
@require_http_methods(['GET'])
def api_config(request):
    """GET /api/online/config/  (header X-API-Key): delivery pricing and the payment methods and accounts customers may use."""
    if not _authorized(request):return JsonResponse({'error':'Invalid or missing API key.'},status=401)
    return JsonResponse(oo.store_config())


@csrf_exempt
@require_http_methods(['GET'])
def api_order_status(request,ref):
    if not _authorized(request):return JsonResponse({'error':'Invalid or missing API key.'},status=401)
    o=OnlineOrder.objects.filter(Q(external_ref=ref)|Q(code=ref)).first()
    if not o:return JsonResponse({'error':'Order not found.'},status=404)
    return JsonResponse(dict(order=o.code,external_ref=o.external_ref,status=o.status,order_status=oo.order_status(o),fulfilment=oo.display_stage(o),stage=oo.display_stage(o),failed_reason=o.failed_reason,payment_status=o.payment_status,courier=o.courier,tracking=o.tracking,total=str(o.total),
                             placed_on=str(o.placed_on),dispatched_on=str(o.dispatched_on) if o.dispatched_on else None,delivered_on=str(o.delivered_on) if o.delivered_on else None))


# ------------------------------------------------------------------ the Online Store's finance screens (the same ones a shop has, on the Online Store's books)
def _finance(view):
    from functools import wraps
    @wraps(view)
    def inner(request,*args,**kwargs):
        _guard(request);request.online_finance=True
        return view(request,*args,**kwargs)
    return login_required(inner)


def finance_views():
    from . import money_views as mv
    return dict(money_in=_finance(mv.money_in),money_out=_finance(mv.money_out),expenses=_finance(mv.expenses),customer_dues=_finance(mv.customer_dues),cash_and_bank=_finance(mv.cash_and_bank),history=_finance(mv.history_view))


# ------------------------------------------------------------------ shops: what to pack (read only) and the live state every screen polls
@login_required
def shop_orders(request):
    """A shop sees only the online orders that hold its own stock: what to pack, the phone codes and IMEIs, and the courier once dispatched. No customer, no money."""
    from . import access
    person=access.staff(request.user)
    if person is None:return redirect('online_orders') if online_access.can_use_online(request.user) else redirect('dashboard')
    branch=person.branch
    if request.method=='POST':
        order=get_object_or_404(oo.shop_orders(branch),pk=request.POST.get('order'));oo.acknowledge(order,branch,request.user);return redirect('shop_online_orders')
    rows=[]
    for o in oo.shop_orders(branch)[:100]:
        mine=[a for l in o.lines.all() for a in l.allocations.all() if a.branch_id==branch.pk]
        rows.append(dict(o=o,status=oo.order_status(o),fulfilment=oo.display_stage(o),acked=o.acks.filter(branch=branch).exists(),
            items=[dict(name=a.product.name,detail=(a.device.code+''.join(' · IMEI '+i.number for i in a.device.imeis.all())) if a.device_id else f'× {a.quantity:.0f}') for a in mine]))
    return render(request,'shop_online_orders.html',dict(title='Online orders to pack',rows=rows,branch=branch))


@login_required
def live_state(request):
    """A cheap fingerprint of the data plus the badge counts. Screens poll it and reload themselves when it changes: nobody has to press refresh."""
    import hashlib
    from django.db.models import Count,Max
    from . import access
    from .models import OnlineOrderEvent,Product,StockMovement
    parts=[]
    for model in (Device,Document,OnlineOrder,Product):
        r=model.objects.aggregate(n=Count('pk'),m=Max('modified'));parts.append(f'{model.__name__}:{r["n"]}:{r["m"]}')
    parts.append('events:%s'%OnlineOrderEvent.objects.aggregate(n=Count('pk'),m=Max('at')))
    from django.db.models import Sum
    parts.append('stock:%s'%StockMovement.objects.aggregate(n=Count('pk'),q=Sum('quantity')))
    parts.append('devices:%s'%sorted(Device.objects.values_list('status').annotate(n=Count('pk'))))      # queryset .update() calls do not touch 'modified', so count by status too
    parts.append('live:%s'%Product.objects.filter(online_visible=True).count())
    parts.append('orders:%s'%sorted(OnlineOrder.objects.values_list('status','stage').annotate(n=Count('pk'))))
    out=dict(token=hashlib.sha1('|'.join(parts).encode()).hexdigest()[:16])
    if online_access.can_use_online(request.user):out['online']=oo.unseen_count()
    else:
        person=access.staff(request.user)
        if person is not None:out['shop']=oo.shop_unseen_count(person.branch)
    resp=JsonResponse(out);resp['Cache-Control']='no-store';return resp


# ------------------------------------------------------------------ delivery pricing and payment accounts
@login_required
def settings_page(request):
    """The online manager's settings: what delivery costs (default, free-from amount, per province) and the accounts customers pay into."""
    _guard(request)
    from .models import OnlineDeliveryRule,OnlinePaymentAccount,OnlineSettings
    cfg=OnlineSettings.get()
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            with transaction.atomic():
                if action=='delivery':
                    fee=s.payment_money(request.POST.get('delivery_fee') or 0)
                    free=request.POST.get('free_from','').strip().replace(',','')
                    if fee<0 or fee>100000:raise ValidationError('The delivery fee must be between 0 and 100,000.')
                    cfg.delivery_fee=fee;cfg.free_delivery_from=s.payment_money(free) if free else None;cfg.cod_enabled=request.POST.get('cod')=='on'
                    if cfg.free_delivery_from is not None and cfg.free_delivery_from<0:raise ValidationError('The free-delivery amount cannot be negative.')
                    cfg.save();messages.success(request,'Delivery settings saved.')
                elif action=='rule_add':
                    province=' '.join(request.POST.get('province','').split())
                    if not province:raise ValidationError('Choose the province.')
                    fee=s.payment_money(request.POST.get('rule_fee') or 0)
                    if fee<0 or fee>100000:raise ValidationError('The delivery fee must be between 0 and 100,000.')
                    found=OnlineDeliveryRule.objects.filter(province__iexact=province).first()
                    if found:found.fee=fee;found.save(update_fields=['fee'])
                    else:OnlineDeliveryRule.objects.create(province=province,fee=fee)
                    messages.success(request,f'Delivery to {province} saved.')
                elif action=='rule_delete':
                    get_object_or_404(OnlineDeliveryRule,pk=request.POST.get('rule')).delete();messages.success(request,'Removed.')
                elif action=='account_add':
                    method=request.POST.get('method','')
                    if method not in OnlinePaymentAccount.METHODS:raise ValidationError('Choose a payment method.')
                    title=' '.join(request.POST.get('title','').split())[:120]
                    if not title:raise ValidationError('Enter the account title (the name on the account).')
                    number=' '.join(request.POST.get('account_number','').split())[:60]
                    if method!='Card' and not number:raise ValidationError('Enter the account number or mobile wallet number.')
                    OnlinePaymentAccount.objects.create(method=method,title=title,bank_name=request.POST.get('bank_name','').strip()[:120],account_number=number,iban=request.POST.get('iban','').replace(' ','')[:40],instructions=request.POST.get('instructions','').strip()[:300])
                    messages.success(request,'Payment account added.')
                elif action in ('account_toggle','account_delete'):
                    account=get_object_or_404(OnlinePaymentAccount,pk=request.POST.get('account'))
                    if action=='account_delete':account.delete();messages.success(request,'Account removed.')
                    else:account.enabled=not account.enabled;account.save(update_fields=['enabled']);messages.success(request,'Account '+('shown' if account.enabled else 'hidden')+' on the website.')
                else:raise ValidationError('Unknown action.')
                s.audit(request.user,'Online settings changed',None,change=action)
        except (ValidationError,ValueError,IntegrityError) as error:messages.error(request,_err(error))
        return redirect('online_settings')
    return render(request,'online_settings.html',dict(title='Delivery & Payment Settings',cfg=cfg,rules=list(OnlineDeliveryRule.objects.all()),accounts=list(OnlinePaymentAccount.objects.all()),
        provinces=['Punjab','Sindh','Khyber Pakhtunkhwa','Balochistan','Islamabad Capital Territory','Gilgit-Baltistan','Azad Kashmir'],methods=OnlinePaymentAccount.METHODS,
        preview=[dict(label=lab,amount=amt,fee=oo.delivery_fee_for(amt)) for lab,amt in (('A small order',Decimal('5000')),('An order of 20,000',Decimal('20000')),('A large order',Decimal('60000')))]))
