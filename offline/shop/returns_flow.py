"""Returns & Exchanges: one place to take goods back from a customer, from finding the sale to settling the money.

Every case is one guided screen on top of the existing, audited return and payment services:
  * the customer is owed the value of what comes back, at the price actually paid (discounts included);
  * if the sale is still unpaid, that value first reduces what the customer owes (it can never be refunded as cash while they still owe);
  * what is left is refunded now (from a cash/bank account that really holds it), kept as customer credit, or used for an exchange;
  * phones that come back go to Holding and are not for sale until someone accepts them after checking.
Everything is saved or nothing is (one transaction), and a double click cannot return the same phone twice."""
import uuid
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from django.utils import timezone
from .models import Branch,Device,Document,IMEI,ShopSettings
from . import access,money,returns,services as s

REASONS=('Faulty / not working','Customer changed mind','Wrong item given','Warranty claim','Not as described','Other')
CONDITIONS=('Good, complete with box and accessories','Good, phone only','Damaged or scratched','Faulty (customer reported problem)','Water damage / tampered')
RESOLUTIONS=(('refund','Refund the money now'),('credit','Keep the value as credit on the customer’s account'),('exchange','Exchange: customer takes another item (use this credit on the sale)'))


def _sales_for(user,branch,query):
    sales=Document.objects.filter(branch=branch,kind__in=s.SELL,status='Submitted',is_return=False).select_related('customer')
    query=(query or '').strip()
    if query:
        digits=''.join(ch for ch in query if ch.isdigit())
        match=Q(code__icontains=query)|Q(customer__name__icontains=query)|Q(customer__phone__icontains=query)
        if len(digits)>=6:match|=Q(sold_devices__imeis__number__contains=digits)
        sales=sales.filter(match).distinct()
    return sales.order_by('-posting_date','-created')[:40]


def _branch(request):
    from .views import selected_branch
    return selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))


@login_required
def returns_home(request):
    """Find the sale the customer is bringing something back from."""
    from .views import ask_which_shop
    gate=ask_which_shop(request,'Returns & Exchanges','Choose the shop where the customer bought the item.')
    if gate:return gate
    branch=_branch(request)
    if not branch:return redirect('setup')
    access.require(request.user,branch,'can_sell')
    query=request.GET.get('q','')
    rows=[]
    for sale in _sales_for(request.user,branch,query):
        rows.append(dict(sale=sale,paid=sale.total-sale.outstanding,items=', '.join(sale.lines.values_list('product__name',flat=True)[:3])))
    return render(request,'returns_home.html',dict(title='Returns & Exchanges',branch=branch,rows=rows,q=query))


def _paid_for_line(original,line,qty,prior=Decimal(0)):
    from .pricing import return_parts
    return return_parts(line,prior,qty)['total']


@login_required
def return_sale(request,pk):
    original=get_object_or_404(Document.objects.select_related('customer','branch'),pk=pk,kind__in=s.SELL,is_return=False,status='Submitted')
    s.authorize(request.user,original);access.require(request.user,original.branch,'can_sell')
    branch=original.branch
    warning='';options=[]
    try:options=returns.options(original)
    except ValidationError as error:warning='; '.join(error.messages)
    for line in options:
        if line.returnable:line.unit_value=_paid_for_line(original,line,Decimal(1),line.returned_quantity) if line.quantity>=1 else Decimal(0)
    methods=list((ShopSettings.objects.filter(branch=branch).first().payment_methods if ShopSettings.objects.filter(branch=branch).exists() else {}).keys())
    previous=list(Document.objects.filter(reference=original,is_return=True,kind__in=s.SELL).exclude(status='Cancelled').order_by('-created'))
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():return process(request,original)
            done=s.once(key,request.user,{'return_sale':{k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')},'sale':str(original.pk)},perform)
            return redirect('return_done',pk=done.pk)
        except (ValidationError,IntegrityError,ValueError) as error:
            from .views import error_text
            messages.error(request,error_text(error))
    return render(request,'return_sale.html',dict(title='Return or exchange',doc=original,branch=branch,options=options,warning=warning,reasons=REASONS,conditions=CONDITIONS,resolutions=RESOLUTIONS,
        methods=methods,receive_shops=list(Branch.objects.filter(enabled=True,is_online=False).order_by('name')) if original.branch.is_online else [],previous=previous,paid=original.total-original.outstanding,request_key=uuid.uuid4(),posted=request.POST))


def process(request,original):
    """Take the goods back and settle the money, all in one transaction."""
    user=request.user;post=request.POST
    device_ids=post.getlist('devices')
    quantities={key[len('return_qty_'):]:value for key,value in post.items() if key.startswith('return_qty_') and value.strip() and value.strip() not in ('0','0.0','0.00','0.000')}
    reason=post.get('reason','').strip();note=post.get('note','').strip();condition=post.get('condition','').strip()
    if reason not in REASONS:raise ValidationError('Choose why the item is being returned.')
    if condition not in CONDITIONS:raise ValidationError('Choose the condition the item came back in.')
    resolution=post.get('resolution','')
    if resolution not in [r for r,_ in RESOLUTIONS]:raise ValidationError('Choose what happens to the money: refund, credit or exchange.')
    if not device_ids and not quantities:raise ValidationError('Tick at least one item that is being returned.')
    full_reason=reason+(' — '+note if note else '')
    warehouse=None
    if original.branch.is_online:        # an online return can go back into any shop
        from . import shopdefaults
        shop=Branch.objects.filter(pk=post.get('receive_shop') or None,enabled=True,is_online=False).first() if post.get('receive_shop') else None
        if post.get('receive_shop') and not shop:raise ValidationError('Choose a valid shop to take the returned stock.')
        warehouse=shopdefaults.warehouse(shop) if shop else None
    return settle(user,original,device_ids,quantities,condition,full_reason,resolution,post.get('refund_method',''),post.get('refund_amount',''),warehouse)


@transaction.atomic
def settle(user,original,device_ids,quantities,condition,reason,resolution,method='',refund_amount='',warehouse=None):
    original=Document.objects.get(pk=original.pk)
    draft=s.return_sale(original,user,device_ids,condition,reason,quantities,warehouse)
    credit=s.submit(draft,user)
    value=-credit.outstanding;applied=Decimal(0)
    original.refresh_from_db()
    if original.outstanding>0:                     # a customer who still owes is credited against that debt first
        applied=min(value,original.outstanding)
        s.apply_return_credit(credit,applied,user)
        credit.refresh_from_db()
    left=-credit.outstanding
    refunded=Decimal(0)
    if resolution=='refund' and left>0:
        s.require(user,credit.branch,'can_handle_payments')
        amount=s.payment_money(refund_amount) if str(refund_amount).strip()!='' else left
        if amount<=0:raise ValidationError('Enter the amount to refund (more than zero).')
        if amount>left:raise ValidationError(f'You can refund at most PKR {left:,.2f} for this return. The rest of the sale was not paid yet.')
        if method not in ShopSettings.objects.get(branch=credit.branch).payment_methods:raise ValidationError('Choose how the refund is paid (cash, bank or wallet).')
        if not credit.customer.branch_id:credit.customer.branch=credit.branch;credit.customer.save(update_fields=['branch','modified'])      # an older customer record belongs to the shop that made this sale
        money.record_party_cash_back(user,credit.customer,amount,method,timezone.localdate(),reference='Return '+credit.code,notes='Refund for return '+credit.code)
        refunded=amount
    credit.refresh_from_db()
    credit.data['returns_flow']=dict(resolution=resolution,reason=reason,condition=condition,value=str(value),applied_to_balance=str(applied),refunded=str(refunded),credit_left=str(-credit.outstanding))
    credit.save(update_fields=['data','modified'])
    s.audit(user,'Return processed',credit,resolution=resolution,value=str(value),refunded=str(refunded),applied=str(applied))
    return credit


@login_required
def return_done(request,pk):
    credit=get_object_or_404(Document.objects.select_related('customer','reference','branch'),pk=pk,is_return=True,kind__in=s.SELL)
    s.authorize(request.user,credit)
    flow=credit.data.get('returns_flow',{})
    lines=credit.lines.select_related('product','device')
    left=max(-credit.outstanding,Decimal(0))
    return render(request,'return_done.html',dict(title='Return saved',doc=credit,flow=flow,lines=lines,left=left,value=-credit.total,
        exchange=flow.get('resolution')=='exchange' and left>0))


@login_required
def return_history(request):
    from .views import ask_which_shop,paginate
    gate=ask_which_shop(request,'Return history','Choose the shop whose returns you want to see.')
    if gate:return gate
    branch=_branch(request)
    if not branch:return redirect('setup')
    access.require(request.user,branch,'can_sell')
    docs=Document.objects.filter(branch=branch,is_return=True,kind__in=s.SELL).exclude(status='Cancelled').select_related('customer','reference').order_by('-posting_date','-created')
    page,pager_query=paginate(request,list(docs))
    return render(request,'return_history.html',dict(title='Return history',branch=branch,rows=page.object_list,page_obj=page,pager_query=pager_query))
