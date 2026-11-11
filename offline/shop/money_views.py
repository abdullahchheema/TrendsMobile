"""Accounts & Finance screens: Money In, Money Out, Expenses, Customer Dues, Supplier Dues, Cash and Bank, Transaction History."""
import uuid
from datetime import date
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from .models import Account,Party,ShopSettings
from . import access,coa,money,services as s


def _guard(request):
    if not access.manager(request.user):raise PermissionDenied('You do not have access to this page.')


def _scope(request):
    """(shops to read, shop to post in). A shop login is always its own shop; the owner may pick one or see all."""
    from .views import selected_branch
    value=request.POST.get('branch') or request.GET.get('branch')
    places=access.branches(request.user)
    if request.user.is_superuser and not value and request.method=='GET':return list(places),selected_branch(request.user,None),None
    branch=selected_branch(request.user,value);return [branch] if branch else [],branch,branch


def _methods(branch):
    config=ShopSettings.objects.filter(branch=branch).first();return [(m,m) for m in (config.payment_methods if config else [])]


def _ensure_chart(user):
    if not Account.objects.filter(code='COA-6750').exists():coa.install_standard(user)


def _accounts(root,exclude=()):
    return Account.objects.filter(code__startswith='COA-',root_type=root,enabled=True,is_group=False).exclude(code__in=exclude).exclude(account_type__in=['Payable','Receivable','Stock','Cash','Bank','Cost of Goods Sold','Depreciation','Accumulated Depreciation']).order_by('code')


def _by_code(code):
    try:return Account.objects.get(code=code)
    except Account.DoesNotExist:raise ValidationError('Install the standard chart of accounts first (Settings).')


class Quick(forms.Form):
    """One small form. Fields are chosen by name so every panel stays short."""
    def __init__(self,*a,branch,fields,party_kind=None,**k):
        super().__init__(*a,**k)
        pool={}
        if 'party' in fields:
            qs=Party.objects.filter(branch=branch,kind=party_kind,enabled=True).order_by('name')
            pool['party']=forms.ModelChoiceField(label='Customer' if party_kind=='Customer' else 'Supplier',queryset=qs,empty_label='Choose…')
        if 'category' in fields:pool['category']=forms.ModelChoiceField(label='Category',queryset=_accounts('Expense',('COA-6950','COA-5200')),empty_label='Choose a category…')
        if 'income_account' in fields:pool['income_account']=forms.ModelChoiceField(label='Income type',queryset=_accounts('Income',('COA-4100','COA-4200')),empty_label='Choose…')
        pool['amount']=forms.DecimalField(label='Amount (PKR)',min_value=0,max_digits=15,decimal_places=2)
        pool['method']=forms.ChoiceField(label='Paid by / received in',choices=_methods(branch),initial='Cash')
        pool['date']=forms.DateField(label='Date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        pool['reference']=forms.CharField(label='Bank / wallet reference',required=False,max_length=180)
        pool['description']=forms.CharField(label='Description',required=False,max_length=240)
        self.fields={name:pool[name] for name in fields}


def _post(request,branch,panels):
    """Run the submitted panel once (idempotent through its request key). Returns True when something was posted."""
    from .views import error_text
    action=request.POST.get('action');panel=next((p for p in panels if p['action']==action),None)
    if not panel:return False
    form=panel['form_class'](request.POST,branch=branch,fields=panel['fields'],party_kind=panel.get('party_kind'))
    panel['form']=form
    try:
        if not form.is_valid():return False
        key=uuid.UUID(request.POST.get('request_key',''))
        payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
        with transaction.atomic():doc=s.once(key,request.user,{'money':payload},lambda:panel['run'](form.cleaned_data))
        messages.success(request,f'{panel["done"]} Reference {doc.code}.');return True
    except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    return False


def _panels(request,branch,which):
    user=request.user;ctor=Quick
    def voucher(kind,account_of,desc_default):
        def run(d):
            account=account_of(d);return money.record_voucher(user,branch,kind,account,d['amount'],d['method'],d['date'],d.get('description') or desc_default,d.get('reference',''))
        return run
    panels={
     'customer_payment':dict(title='Customer payment received',fields=['party','amount','method','date','description'],party_kind='Customer',done='Customer payment recorded.',
        run=lambda d:money.record_party_payment(user,d['party'],d['amount'],d['method'],d['date'],d.get('reference',''),d.get('description',''))),
     'supplier_refund':dict(title='Refund received from a supplier',fields=['party','amount','method','date','description'],party_kind='Supplier',done='Supplier refund recorded.',
        run=lambda d:money.record_party_cash_back(user,d['party'],d['amount'],d['method'],d['date'],d.get('reference',''),d.get('description',''))),
     'other_income':dict(title='Other income',fields=['income_account','amount','method','date','description'],done='Income recorded.',run=lambda d:money.record_voucher(user,branch,'income',d['income_account'],d['amount'],d['method'],d['date'],d.get('description') or d['income_account'].name,d.get('reference',''))),
     'owner_funds':dict(title='Money put in by the owner',fields=['amount','method','date','description'],done='Owner funds recorded.',run=voucher('capital',lambda d:_by_code('COA-3100'),'Owner funds')),
     'supplier_payment':dict(title='Pay a supplier',fields=['party','amount','method','date','description'],party_kind='Supplier',done='Supplier payment recorded.',
        run=lambda d:money.record_party_payment(user,d['party'],d['amount'],d['method'],d['date'],d.get('reference',''),d.get('description',''))),
     'customer_refund':dict(title='Refund a customer',fields=['party','amount','method','date','description'],party_kind='Customer',done='Customer refund recorded.',
        run=lambda d:money.record_party_cash_back(user,d['party'],d['amount'],d['method'],d['date'],d.get('reference',''),d.get('description',''))),
     'expense':dict(title='Record an expense',fields=['category','amount','method','date','description'],done='Expense recorded.',run=lambda d:money.record_voucher(user,branch,'expense',d['category'],d['amount'],d['method'],d['date'],d.get('description') or d['category'].name,d.get('reference',''))),
     'owner_drawings':dict(title='Money taken by the owner',fields=['amount','method','date','description'],done='Owner drawings recorded.',run=voucher('drawings',lambda d:_by_code('COA-3300'),'Owner drawings')),
    }
    out=[]
    for name in which:
        p=dict(panels[name]);p.update(action=name,form_class=ctor,form=None);out.append(p)
    return out


def _render_panels(request,branch,panels):
    for p in panels:
        if p['form'] is None:p['form']=Quick(branch=branch,fields=p['fields'],party_kind=p.get('party_kind'))
    return panels


def _history_context(request,shops,forced=None):
    g=request.GET
    try:start=date.fromisoformat(g['from_date']) if g.get('from_date') else None;end=date.fromisoformat(g['to_date']) if g.get('to_date') else None
    except ValueError:start=end=None
    kind=forced or g.get('type','');rows,totals=money.history(shops,start,end,kind,g.get('method',''),g.get('q',''))
    from .views import paginate
    page,pager=paginate(request,rows,60)
    return dict(rows=page.object_list,page_obj=page,pager_query=pager,totals=totals,types=money.TYPES,filters=dict(from_date=g.get('from_date',''),to_date=g.get('to_date',''),type=kind,method=g.get('method',''),q=g.get('q','')),
                methods=sorted({m for _,(b,m) in money.method_map(shops).items()}))


def _page(request,title,which,mode='all',intro=''):
    _guard(request);_ensure_chart(request.user)
    shops,branch,posting=_scope(request)
    if not branch:return redirect('setup')
    panels=_panels(request,branch,which)
    if request.method=='POST':
        if _post(request,branch,panels):return redirect(request.get_full_path())
    ctx=_history_context(request,shops if not posting else [branch],forced=None)
    if mode in ('in','out'):
        ctx['rows']=[r for r in ctx['rows'] if (r['received']>0 if mode=='in' else r['paid']>0)]
    return render(request,'money_page.html',dict(title=title,intro=intro,branch=branch,scope_shops=shops,panels=_render_panels(request,branch,panels),request_key=uuid.uuid4(),mode=mode,**ctx))


@login_required
def money_in(request):return _page(request,'Money In',['customer_payment','supplier_refund','other_income','owner_funds'],'in','All money received by the shop. Customer payments reduce what customers owe; they are not new sales.')


@login_required
def money_out(request):return _page(request,'Money Out',['supplier_payment','expense','customer_refund','owner_drawings'],'out','All money paid by the shop. Supplier payments reduce what you owe; stock purchases are not expenses.')


@login_required
def history_view(request):
    _guard(request);shops,branch,posting=_scope(request)
    if not branch:return redirect('setup')
    return render(request,'money_page.html',dict(title='Transaction History',intro='Every movement of cash, bank, Easypaisa and JazzCash. Moves between your own accounts are shown once and are not income or expense.',branch=branch,scope_shops=shops,panels=[],request_key=uuid.uuid4(),mode='all',**_history_context(request,shops)))


@login_required
def expenses(request):
    _guard(request);_ensure_chart(request.user)
    shops,branch,posting=_scope(request)
    if not branch:return redirect('setup')
    panels=_panels(request,branch,['expense'])
    if request.method=='POST' and _post(request,branch,panels):return redirect(request.get_full_path())
    ctx=_history_context(request,shops,forced='Expense')
    rows=ctx['rows']
    category=request.GET.get('category','')
    if category:ctx['rows']=[r for r in rows if category.casefold() in r['description'].casefold()]
    ctx['categories']=list(_accounts('Expense',('COA-6950','COA-5200')).values_list('name',flat=True));ctx['category']=category
    ctx['total']=sum((r['paid'] for r in ctx['rows']),money.ZERO)
    return render(request,'money_expenses.html',dict(title='Expenses',branch=branch,scope_shops=shops,panels=_render_panels(request,branch,panels),request_key=uuid.uuid4(),**ctx))


def _dues(request,kind):
    _guard(request);shops,branch,posting=_scope(request)
    if not branch:return redirect('setup')
    rows=money.customer_balances(shops) if kind=='Customer' else money.supplier_balances(shops)
    owes_you=sum((r['owes_you'] for r in rows),money.ZERO);you_owe=sum((r['you_owe'] for r in rows),money.ZERO)
    title='Customer Dues' if kind=='Customer' else 'Supplier Dues'
    intro='What customers owe the shop, and what the shop owes customers (advance payments and returned goods).' if kind=='Customer' else 'What the shop owes its suppliers, and what suppliers owe the shop (overpayments and returned stock).'
    return render(request,'money_dues.html',dict(title=title,intro=intro,kind=kind,rows=rows,owes_you=owes_you,you_owe=you_owe,branch=branch,scope_shops=shops,owner=request.user.is_superuser))


def _payments(request,kind):
    _guard(request);shops,branch,posting=_scope(request)
    if not branch:return redirect('setup')
    q=request.GET.get('q','');rows=money.payment_history(shops,kind,q)
    from .views import paginate
    page,pager=paginate(request,rows,60)
    title=('Customer' if kind=='Customer' else 'Supplier')+' Payment History'
    return render(request,'money_payments.html',dict(title=title,kind=kind,rows=page.object_list,page_obj=page,pager_query=pager,q=q,branch=branch,scope_shops=shops,
        intro='Every payment, newest first: paid with the sale, clearing an older due, paid in advance, and refunds.' if kind=='Customer' else 'Every payment, newest first: paid against a bill, paid in advance, and refunds from suppliers.'))


@login_required
def customer_payments(request):return _payments(request,'Customer')


@login_required
def supplier_payments(request):return _payments(request,'Supplier')


@login_required
def customer_dues(request):return _dues(request,'Customer')


@login_required
def supplier_dues(request):return _dues(request,'Supplier')


@login_required
def party_account(request,pk):
    from .views import error_text
    _guard(request);party=get_object_or_404(access.scoped_parties(request.user),pk=pk)
    if not party.branch_id:raise PermissionDenied('This record belongs to no shop yet.')
    branch=party.branch;customer=party.kind=='Customer'
    form=Quick(request.POST or None,branch=branch,fields=['amount','method','date','description'])
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            if form.is_valid():
                d=form.cleaned_data
                with transaction.atomic():doc=s.once(key,request.user,{'party':request.POST.dict()},lambda:money.record_party_payment(request.user,party,d['amount'],d['method'],d['date'],d.get('reference',''),d.get('description','')))
                messages.success(request,('Payment received' if customer else 'Payment made')+f'. Reference {doc.code}.');return redirect('party_account',pk=party.pk)
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    detail=money.party_detail(party)
    return render(request,'money_party.html',dict(title=party.name,party=party,customer=customer,form=form,request_key=uuid.uuid4(),**detail))


@login_required
def cash_and_bank(request):
    from .views import error_text
    _guard(request);shops,branch,posting=_scope(request)
    if not branch:return redirect('setup')
    class Move(forms.Form):
        def __init__(self,*a,**k):
            super().__init__(*a,**k);choices=_methods(branch)
            self.fields['source']=forms.ChoiceField(label='Move from',choices=choices);self.fields['target']=forms.ChoiceField(label='Move to',choices=choices,initial=choices[1][0] if len(choices)>1 else None)
            self.fields['amount']=forms.DecimalField(label='Amount (PKR)',min_value=0,max_digits=15,decimal_places=2)
            self.fields['date']=forms.DateField(label='Date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
            self.fields['notes']=forms.CharField(label='Reason',required=False,max_length=240)
    form=Move(request.POST or None)
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            if form.is_valid():
                d=form.cleaned_data
                with transaction.atomic():doc=s.once(key,request.user,{'move':request.POST.dict()},lambda:money.move_money(request.user,branch,d['source'],d['target'],d['amount'],d['date'],d.get('reference',''),d.get('notes','')))
                messages.success(request,f'Money moved. Reference {doc.code}.');return redirect(request.get_full_path())
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    accounts=money.balances(shops)
    rows,_=money.history(shops)
    negative=[a for a in accounts if a['amount']<0]
    return render(request,'money_cash.html',dict(title='Cash and Bank',branch=branch,scope_shops=shops,accounts=accounts,negative=negative,total=sum((a['amount'] for a in accounts),money.ZERO),form=form,request_key=uuid.uuid4(),recent=rows[:25]))
