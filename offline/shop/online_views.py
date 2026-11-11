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
    counts={name:OnlineOrder.objects.filter(status=name).count() for name in ('New','Dispatched','Delivered','Cancelled')}
    if status in counts:rows=rows.filter(status=status)
    if q:
        digits=''.join(ch for ch in q if ch.isdigit())
        match=Q(code__icontains=q)|Q(customer_name__icontains=q)|Q(external_ref__icontains=q)|Q(tracking__icontains=q)
        if len(digits)>=4:match|=Q(customer_phone__contains=digits)
        rows=rows.filter(match)
    from .views import paginate
    page,pager=paginate(request,list(rows),40)
    data=[dict(o=o,items=', '.join(f'{l.product.name} × {l.quantity}' for l in o.lines.all())[:90],shops=sorted({a.branch.name for l in o.lines.all() for a in l.allocations.all()})) for o in page.object_list]
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
    _guard(request);o=get_object_or_404(OnlineOrder.objects.select_related('exchange_credit'),pk=pk)
    if request.method=='POST':
        action=request.POST.get('action')
        try:
            if action=='cancel':oo.cancel(o,request.user,request.POST.get('reason',''));messages.success(request,'Order cancelled. The stock is available again.')
            elif action=='paid':oo.mark_paid(o,request.user,request.POST.get('reference',''));messages.success(request,'Payment confirmed.')
            elif action=='reassign':oo.reassign(o,request.user,get_object_or_404(Branch,pk=request.POST.get('shop'),enabled=True));messages.success(request,'The order will be supplied from that shop.')
            elif action=='dispatch':
                made=oo.dispatch(o,request.user,request.POST.get('courier',''),request.POST.get('tracking',''))
                messages.success(request,f'Dispatched. {len(made)} invoice{"s" if len(made)!=1 else ""} posted in the supplying shop{"s" if len(made)!=1 else ""}.')
            elif action=='deliver':oo.deliver(o,request.user,request.POST.get('method','Cash'));messages.success(request,'Marked delivered; any cash on delivery is now recorded as received.')
            else:raise ValidationError('Unknown action.')
        except (ValidationError,IntegrityError,ValueError) as error:messages.error(request,_err(error))
        return redirect('online_order',pk=o.pk)
    o.refresh_from_db()
    lines=oo.order_lines(o);invoices=list(oo.invoices(o))
    returns=list(Document.objects.filter(online_order=o,is_return=True,kind__in=list(s.SELL)).exclude(status='Cancelled').select_related('branch').order_by('created'))
    methods=sorted({m for b in {i.branch for i in invoices} for m in (ShopSettings.objects.filter(branch=b).first().payment_methods if ShopSettings.objects.filter(branch=b).exists() else {})}) or ['Cash']
    return render(request,'online_order.html',dict(title=o.code,o=o,lines=lines,invoices=invoices,returns=returns,methods=methods,
        candidates=list(_candidates(o)) if o.status==OnlineOrder.NEW and not o.exchange_credit_id else [],prepaid=o.payment_method!='Cash on Delivery',
        outstanding=sum((i.outstanding for i in invoices if i.outstanding>0),Decimal(0))))


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
    """Everything the website can sell, one row per variant, with the quantity in each shop."""
    _guard(request)
    shops=list(Branch.objects.filter(enabled=True).order_by('name'));q=request.GET.get('q','').strip().lower()
    rows=[]
    for v in oo.variants(published_only=False):
        p=v['product']
        if q and q not in (p.name+' '+v['condition']+' '+p.colour).lower():continue
        rows.append(dict(v,title=(p.web_title or (f'{p.model.brand} {p.model.name}' if p.model_id else p.name)),live=p.online_visible,cells=[v['per_shop'].get(b.pk,0) for b in shops]))
    rows.sort(key=lambda r:(r['title'],r['product'].storage,r['product'].colour,r['condition']))
    from .views import paginate
    page,pager=paginate(request,rows,60)
    return render(request,'online_stock.html',dict(title='Online Stock',rows=page.object_list,page_obj=page,pager_query=pager,shops=shops,q=q,
        total=sum(r['quantity'] for r in rows)))


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
        with transaction.atomic():order,created=oo.create(data,None,'Website')
    except (ValidationError,IntegrityError,ValueError) as error:
        return JsonResponse({'error':_err(error)},status=409 if 'no longer' in _err(error) or 'total is now' in _err(error) or 'Only ' in _err(error) else 400)
    return JsonResponse(dict(order=order.code,external_ref=order.external_ref,status=order.status,subtotal=str(order.subtotal),delivery_fee=str(order.delivery_fee),total=str(order.total),created=created),status=201 if created else 200)


@csrf_exempt
@require_http_methods(['GET'])
def api_order_status(request,ref):
    if not _authorized(request):return JsonResponse({'error':'Invalid or missing API key.'},status=401)
    o=OnlineOrder.objects.filter(Q(external_ref=ref)|Q(code=ref)).first()
    if not o:return JsonResponse({'error':'Order not found.'},status=404)
    return JsonResponse(dict(order=o.code,external_ref=o.external_ref,status=o.status,payment_status=o.payment_status,courier=o.courier,tracking=o.tracking,total=str(o.total),
                             placed_on=str(o.placed_on),dispatched_on=str(o.dispatched_on) if o.dispatched_on else None,delivered_on=str(o.delivered_on) if o.delivered_on else None))
