"""Advanced accounting: fixed assets with depreciation, prepaid expenses and accruals, and business loans.

Money that really moves (buying an asset, paying a prepaid bill, receiving or repaying a loan) goes through the existing
voucher posting, so cash and bank balances, the till and the period lock all keep working. Everything that is only an
accounting adjustment (depreciation, releasing a prepayment, an accrual and its reversal, removing a sold asset) is a
balanced Journal Entry posted for the exact month it belongs to."""
import calendar
import uuid
from datetime import date,timedelta
from decimal import Decimal,ROUND_HALF_UP
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.shortcuts import redirect,render
from django.utils import timezone
from .models import Account,Branch,Deferral,FixedAsset,Loan,ShopSettings
from . import access,permissions,vouchers,services as s

ZERO=Decimal('0.00')
cents=lambda v:Decimal(v).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)


def _guard(user,branch):
    if not permissions.can(user,'accounting'):raise PermissionDenied('You do not have permission for advanced accounting.')
    access.require(user,branch)


def month_end(d):return d.replace(day=calendar.monthrange(d.year,d.month)[1])


def last_full_month_end(through):
    """The latest month end that is on or before `through`."""
    return through if through==month_end(through) else through.replace(day=1)-timedelta(days=1)


def months_inclusive(start,end):return (end.year-start.year)*12+end.month-start.month+1


def _journal(user,branch,on,notes,lines,**data):
    """Post a balanced Journal Entry directly (same pattern as the year-end close)."""
    doc=s.new_doc('Journal Entry',branch,posting_date=on,status='Submitted',posted_by=user,notes=notes,data=dict(data))
    s.open_date(doc)
    for account,debit,credit in lines:s.entry(doc,account,debit=debit,credit=credit)
    doc.total=sum((d for _,d,_ in lines),ZERO);doc.save(update_fields=['total'])
    s.balanced(doc)
    return doc


def _account(pk,roots,label):
    try:account=Account.objects.get(pk=pk)
    except (Account.DoesNotExist,ValueError,ValidationError):raise ValidationError(f'Choose the {label}.')
    if not account.enabled or account.is_group or account.root_type not in roots:raise ValidationError(f'Choose an active {" or ".join(r.lower() for r in roots)} account for the {label}.')
    return account


# ---------------------------------------------------------------- fixed assets
@transaction.atomic
def add_asset(user,branch,d):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=branch.pk);_guard(user,branch)
    cost=cents(d['cost']);salvage=cents(d.get('salvage') or 0);life=int(d['life_months'])
    if cost<=0:raise ValidationError('Enter what the asset cost.')
    if salvage<0 or salvage>cost:raise ValidationError('The scrap value must be between zero and the cost.')
    if life<1:raise ValidationError('Enter how many months the asset will be used.')
    if d['purchase_date']>timezone.localdate():raise ValidationError('The purchase date cannot be in the future.')
    method=d.get('method') or 'Straight line'
    if method=='Reducing balance' and not (0<Decimal(d.get('rate') or 0)<=100):raise ValidationError('Enter the yearly reducing-balance rate (1 to 100 percent).')
    asset_account=_account(d['asset_account'],('Asset',),'asset account');accumulated=_account(d['accumulated_account'],('Asset',),'accumulated depreciation account')
    expense=_account(d['expense_account'],('Expense',),'depreciation expense account')
    funding=d.get('funding') or ''
    if funding=='existing':
        equity=_account(d.get('equity_account'),('Equity',),'owner equity account')
        acquisition=_journal(user,branch,d['purchase_date'],f'Opening balance of asset {d["name"]}',[(asset_account,cost,ZERO),(equity,ZERO,cost)],asset=d['name'])
    else:
        acquisition=vouchers.post(dict(branch=branch,type='asset',posting_date=d['purchase_date'],amount=cost,account=str(asset_account.pk),mode=funding,reference=d.get('reference',''),description=f'Purchase of {d["name"]}'),user)
    asset=FixedAsset.objects.create(code='ASSET-'+uuid.uuid4().hex[:10].upper(),branch=branch,name=d['name'],purchase_date=d['purchase_date'],cost=cost,salvage=salvage,life_months=life,method=method,rate=Decimal(d.get('rate') or 0),
                                    asset_account=asset_account,accumulated_account=accumulated,expense_account=expense)
    s.audit(user,'Fixed asset added',acquisition,branch=branch,asset=asset.code,name=asset.name,cost=str(cost))
    return acquisition


def depreciation_due(asset,through):
    """(amount, last month end covered) still to be depreciated up to `through`, or (0, None)."""
    if asset.status!='Active':return ZERO,None
    end=last_full_month_end(through)
    if end<month_end(asset.purchase_date) or (asset.depreciated_through and end<=asset.depreciated_through):return ZERO,None
    base=asset.cost-asset.salvage
    if asset.method=='Straight line':
        target=min(base,cents(base*months_inclusive(asset.purchase_date,end)/asset.life_months))
        amount=target-asset.accumulated
    else:
        book=asset.cost-asset.accumulated;amount=ZERO
        first=month_end(asset.purchase_date) if not asset.depreciated_through else month_end(asset.depreciated_through+timedelta(days=1))
        cursor=first
        while cursor<=end and book>asset.salvage:
            step=min(cents(book*asset.rate/1200),book-asset.salvage);amount+=step;book-=step
            cursor=month_end(cursor+timedelta(days=1))
    return max(amount,ZERO),end


@transaction.atomic
def depreciate(user,asset,through):
    asset=FixedAsset.objects.select_for_update(of=('self',)).select_related('branch','expense_account','accumulated_account').get(pk=asset.pk);_guard(user,asset.branch)
    if through>timezone.localdate():raise ValidationError('Depreciation cannot be run for a future date.')
    amount,end=depreciation_due(asset,through)
    if end is None:return None
    if amount<=0:
        FixedAsset.objects.filter(pk=asset.pk).update(depreciated_through=end);return None
    doc=_journal(user,asset.branch,end,f'Depreciation of {asset.name} to {end:%d %b %Y}',[(asset.expense_account,amount,ZERO),(asset.accumulated_account,ZERO,amount)],asset=asset.code,depreciation=True)
    FixedAsset.objects.filter(pk=asset.pk).update(accumulated=asset.accumulated+amount,depreciated_through=end)
    s.audit(user,'Depreciation posted',doc,branch=asset.branch,asset=asset.code,amount=str(amount))
    return doc


def depreciate_all(user,branch,through):
    docs=[]
    for asset in FixedAsset.objects.filter(branch=branch,status='Active').order_by('purchase_date'):
        doc=depreciate(user,asset,through)
        if doc:docs.append(doc)
    return docs


@transaction.atomic
def dispose_asset(user,asset,on,proceeds,mode,reference=''):
    asset=FixedAsset.objects.select_for_update(of=('self',)).select_related('branch','asset_account','accumulated_account').get(pk=asset.pk);_guard(user,asset.branch)
    if asset.status!='Active':raise ValidationError('This asset has already been disposed of.')
    proceeds=cents(proceeds or 0)
    if proceeds<0:raise ValidationError('Proceeds cannot be negative.')
    if on>timezone.localdate() or on<asset.purchase_date:raise ValidationError('Choose a disposal date between the purchase date and today.')
    if depreciation_due(asset,on)[0]>0:        # nothing may be waiting to be depreciated, or the book value (and the gain or loss) would be wrong; an asset sold in the month it was bought has nothing due
        raise ValidationError('Post depreciation up to the disposal date first, so the book value is right.')
    book=asset.cost-asset.accumulated;loss=book-proceeds
    if proceeds>0:
        vouchers.post(dict(branch=asset.branch,type='asset_sale',posting_date=on,amount=proceeds,account=str(asset.asset_account.pk),mode=mode,reference=reference,description=f'Sale of {asset.name}'),user)
    # The sale voucher above already took `proceeds` out of the asset account. What is left of the cost comes out here; when the asset sold for
    # more than it cost, the voucher took out too much and the difference goes back in (a ledger row is never negative).
    rest=asset.cost-proceeds
    lines=[(asset.accumulated_account,asset.accumulated,ZERO),(asset.asset_account,ZERO,rest) if rest>=0 else (asset.asset_account,-rest,ZERO)]
    if loss>0:lines.append((_default('COA-6900','loss on disposal'),loss,ZERO))
    elif loss<0:lines.append((_default('COA-4900','gain on disposal'),ZERO,-loss))
    lines=[l for l in lines if l[1] or l[2]]
    doc=_journal(user,asset.branch,on,f'Disposal of {asset.name}',lines,asset=asset.code,disposal=True,proceeds=str(proceeds))
    FixedAsset.objects.filter(pk=asset.pk).update(status='Disposed',disposed_on=on,disposal_amount=proceeds)
    s.audit(user,'Fixed asset disposed',doc,branch=asset.branch,asset=asset.code,book_value=str(book),proceeds=str(proceeds))
    return doc


def _default(code,label):
    account=Account.objects.filter(code=code,enabled=True,is_group=False).first()
    if not account:raise ValidationError(f'Install the standard chart of accounts first (needed for the {label}).')
    return account


# ------------------------------------------------------- prepaid expenses and accruals
@transaction.atomic
def add_deferral(user,branch,d):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=branch.pk);_guard(user,branch)
    kind=d['kind'];amount=cents(d['amount']);months=int(d.get('months') or 1)
    if kind not in ('Prepaid','Accrual'):raise ValidationError('Choose prepaid or accrual.')
    if amount<=0:raise ValidationError('Enter an amount greater than zero.')
    if d['start_date']>timezone.localdate():raise ValidationError('The date cannot be in the future.')
    expense=_account(d['expense_account'],('Expense',),'expense account')
    if kind=='Prepaid':
        if months<1:raise ValidationError('Enter how many months it covers.')
        prepaid=_account(d['balance_account'],('Asset',),'prepaid expenses account')
        doc=vouchers.post(dict(branch=branch,type='prepaid',posting_date=d['start_date'],amount=amount,account=str(prepaid.pk),mode=d.get('mode'),reference=d.get('reference',''),description=d['description']),user)
        item=Deferral.objects.create(code='DEF-'+uuid.uuid4().hex[:10].upper(),branch=branch,kind=kind,description=d['description'],amount=amount,start_date=d['start_date'],months=months,expense_account=expense,balance_account=prepaid)
    else:
        liability=_account(d['balance_account'],('Liability',),'accrued expenses account')
        reverse_on=d.get('reverse_on')
        if not reverse_on or reverse_on<=d['start_date']:raise ValidationError('Choose a reversal date after the accrual date (usually the first day of next month).')
        doc=_journal(user,branch,d['start_date'],f'Accrued expense: {d["description"]}',[(expense,amount,ZERO),(liability,ZERO,amount)],accrual=True)
        item=Deferral.objects.create(code='DEF-'+uuid.uuid4().hex[:10].upper(),branch=branch,kind=kind,description=d['description'],amount=amount,start_date=d['start_date'],months=1,expense_account=expense,balance_account=liability,reverse_on=reverse_on)
    s.audit(user,f'{kind} added',doc,branch=branch,deferral=item.code,amount=str(amount))
    return doc


def release_due(item,through):
    if item.kind!='Prepaid' or item.status!='Open':return ZERO,None
    end=last_full_month_end(through)
    if end<month_end(item.start_date) or (item.released_through and end<=item.released_through):return ZERO,None
    target=min(item.amount,cents(item.amount*months_inclusive(item.start_date,end)/item.months))
    return max(target-item.released,ZERO),end


@transaction.atomic
def release(user,item,through):
    item=Deferral.objects.select_for_update(of=('self',)).select_related('branch','expense_account','balance_account').get(pk=item.pk);_guard(user,item.branch)
    if through>timezone.localdate():raise ValidationError('Cannot release for a future date.')
    amount,end=release_due(item,through)
    if end is None:return None
    if amount<=0:
        Deferral.objects.filter(pk=item.pk).update(released_through=end);return None
    doc=_journal(user,item.branch,end,f'Prepaid expense used: {item.description}',[(item.expense_account,amount,ZERO),(item.balance_account,ZERO,amount)],deferral=item.code)
    done=item.released+amount
    Deferral.objects.filter(pk=item.pk).update(released=done,released_through=end,status='Complete' if done>=item.amount else 'Open')
    s.audit(user,'Prepaid released',doc,branch=item.branch,deferral=item.code,amount=str(amount))
    return doc


@transaction.atomic
def reverse_accrual(user,item,today=None):
    item=Deferral.objects.select_for_update(of=('self',)).select_related('branch','expense_account','balance_account').get(pk=item.pk);_guard(user,item.branch)
    today=today or timezone.localdate()
    if item.kind!='Accrual' or item.status!='Open':return None
    if item.reverse_on>today:raise ValidationError(f'This accrual reverses on {item.reverse_on:%d %b %Y}.')
    doc=_journal(user,item.branch,item.reverse_on,f'Reversal of accrual: {item.description}',[(item.balance_account,item.amount,ZERO),(item.expense_account,ZERO,item.amount)],deferral=item.code,reversal=True)
    Deferral.objects.filter(pk=item.pk).update(released=item.amount,status='Complete')
    s.audit(user,'Accrual reversed',doc,branch=item.branch,deferral=item.code)
    return doc


def process_deferrals(user,branch,through):
    docs=[]
    for item in Deferral.objects.filter(branch=branch,status='Open').order_by('start_date'):
        if item.kind=='Prepaid':doc=release(user,item,through)
        else:doc=reverse_accrual(user,item,through) if item.reverse_on and item.reverse_on<=through else None
        if doc:docs.append(doc)
    return docs


# ------------------------------------------------------------------------- loans
@transaction.atomic
def add_loan(user,branch,d):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=branch.pk);_guard(user,branch)
    principal=cents(d['principal']);rate=Decimal(d.get('annual_rate') or 0)
    if principal<=0:raise ValidationError('Enter the amount borrowed.')
    if not 0<=rate<=100:raise ValidationError('The yearly interest rate must be between 0 and 100 percent.')
    if not str(d['lender']).strip():raise ValidationError('Enter who lent the money.')
    liability=_account(d['liability_account'],('Liability',),'loan account');interest=_account(d['interest_account'],('Expense',),'interest expense account')
    doc=vouchers.post(dict(branch=branch,type='loan_in',posting_date=d['start_date'],amount=principal,account=str(liability.pk),mode=d.get('mode'),reference=d.get('reference',''),description=f'Loan from {d["lender"]}'),user)
    loan=Loan.objects.create(code='LOAN-'+uuid.uuid4().hex[:10].upper(),branch=branch,lender=d['lender'],principal=principal,annual_rate=rate,start_date=d['start_date'],term_months=int(d.get('term_months') or 12),liability_account=liability,interest_account=interest)
    s.audit(user,'Loan received',doc,branch=branch,loan=loan.code,principal=str(principal))
    return doc


def suggested_interest(loan):return cents(loan.outstanding*loan.annual_rate/1200)


@transaction.atomic
def repay_loan(user,loan,on,principal,interest,mode,reference=''):
    loan=Loan.objects.select_for_update(of=('self',)).select_related('branch','liability_account','interest_account').get(pk=loan.pk);_guard(user,loan.branch)
    principal=cents(principal or 0);interest=cents(interest or 0)
    if principal<0 or interest<0 or principal+interest<=0:raise ValidationError('Enter the principal and/or interest being paid.')
    if principal>loan.outstanding:raise ValidationError(f'Only PKR {loan.outstanding:,.2f} of principal is still owed.')
    if on<loan.start_date:raise ValidationError('A repayment cannot be before the loan date.')
    doc=None
    if principal>0:doc=vouchers.post(dict(branch=loan.branch,type='loan_out',posting_date=on,amount=principal,account=str(loan.liability_account.pk),mode=mode,reference=reference,description=f'Loan repayment to {loan.lender}'),user)
    if interest>0:doc=vouchers.post(dict(branch=loan.branch,type='expense',posting_date=on,amount=interest,account=str(loan.interest_account.pk),mode=mode,reference=reference,description=f'Loan interest to {loan.lender}'),user)
    Loan.objects.filter(pk=loan.pk).update(principal_paid=loan.principal_paid+principal,interest_paid=loan.interest_paid+interest)
    s.audit(user,'Loan repayment',doc,branch=loan.branch,loan=loan.code,principal=str(principal),interest=str(interest))
    return doc


# ------------------------------------------------------------------------ screens
def _allowed(user):
    if not permissions.can(user,'accounting'):raise PermissionDenied('You do not have access to this page.')


def _accounts(root,*types):
    qs=Account.objects.filter(enabled=True,is_group=False,root_type=root).order_by('code')
    return qs.filter(account_type__in=types) if types else qs


def _choices(qs,blank='Choose an account'):return [('',blank)]+[(str(a.pk),a.name) for a in qs]


def _modes(branch):
    config=ShopSettings.objects.filter(branch=branch).first()
    return [(m,m) for m in (config.payment_methods if config else [])]


class AssetForm(forms.Form):
    def __init__(self,*a,branch,**k):
        super().__init__(*a,**k)
        self.fields['name']=forms.CharField(max_length=180,label='Asset name',widget=forms.TextInput(attrs={'placeholder':'e.g. Display counter'}))
        self.fields['purchase_date']=forms.DateField(initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        self.fields['cost']=forms.DecimalField(label='Cost (PKR)',min_value=0,max_digits=15,decimal_places=2)
        self.fields['salvage']=forms.DecimalField(label='Scrap value (PKR)',required=False,min_value=0,max_digits=15,decimal_places=2,initial=0)
        self.fields['life_months']=forms.IntegerField(label='Useful life (months)',min_value=1,initial=60)
        self.fields['method']=forms.ChoiceField(choices=[('Straight line','Straight line'),('Reducing balance','Reducing balance')])
        self.fields['rate']=forms.DecimalField(label='Yearly rate % (reducing balance only)',required=False,min_value=0,max_value=100,decimal_places=2)
        self.fields['asset_account']=forms.ChoiceField(label='Asset account',choices=_choices(_accounts('Asset','Fixed Asset')))
        self.fields['accumulated_account']=forms.ChoiceField(label='Accumulated depreciation account',choices=_choices(_accounts('Asset','Accumulated Depreciation')))
        self.fields['expense_account']=forms.ChoiceField(label='Depreciation expense account',choices=_choices(_accounts('Expense','Depreciation')))
        self.fields['funding']=forms.ChoiceField(label='How was it paid?',choices=_modes(branch)+[('existing','Already owned (opening balance)')])
        self.fields['equity_account']=forms.ChoiceField(label='Owner equity account (already-owned assets)',required=False,choices=_choices(_accounts('Equity'),'—'))


class DeferralForm(forms.Form):
    def __init__(self,*a,branch,**k):
        super().__init__(*a,**k)
        self.fields['kind']=forms.ChoiceField(label='Type',choices=[('Prepaid','Prepaid: paid now, used over months'),('Accrual','Accrual: used now, paid later')])
        self.fields['description']=forms.CharField(max_length=240,widget=forms.TextInput(attrs={'placeholder':'e.g. 12 months shop rent paid in advance'}))
        self.fields['amount']=forms.DecimalField(label='Amount (PKR)',min_value=0,max_digits=15,decimal_places=2)
        self.fields['start_date']=forms.DateField(label='Date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        self.fields['months']=forms.IntegerField(label='Months covered (prepaid)',required=False,min_value=1,initial=12)
        self.fields['reverse_on']=forms.DateField(label='Reverse on (accrual)',required=False,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        self.fields['expense_account']=forms.ChoiceField(label='Expense account',choices=_choices(_accounts('Expense')))
        self.fields['balance_account']=forms.ChoiceField(label='Prepaid asset / accrued liability account',choices=_choices(_accounts('Asset','Current Asset')|_accounts('Liability','Current Liability')))
        self.fields['mode']=forms.ChoiceField(label='Paid from (prepaid)',required=False,choices=_modes(branch))


class LoanForm(forms.Form):
    def __init__(self,*a,branch,**k):
        super().__init__(*a,**k)
        self.fields['lender']=forms.CharField(max_length=180)
        self.fields['principal']=forms.DecimalField(label='Amount borrowed (PKR)',min_value=0,max_digits=15,decimal_places=2)
        self.fields['annual_rate']=forms.DecimalField(label='Yearly interest %',min_value=0,max_value=100,decimal_places=2,initial=0)
        self.fields['start_date']=forms.DateField(label='Date received',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        self.fields['term_months']=forms.IntegerField(label='Term (months)',min_value=1,initial=12)
        self.fields['liability_account']=forms.ChoiceField(label='Loan account',choices=_choices(_accounts('Liability')))
        self.fields['interest_account']=forms.ChoiceField(label='Interest expense account',choices=_choices(_accounts('Expense')))
        self.fields['mode']=forms.ChoiceField(label='Received into',choices=_modes(branch))


def _page(request,template,title,form_class,add,items_fn,actions):
    from .views import selected_branch,error_text
    _allowed(request.user)
    branch=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:return redirect('setup')
    form=form_class(request.POST if request.method=='POST' and request.POST.get('action','add')=='add' else None,branch=branch)
    if request.method=='POST':
        action=request.POST.get('action','add')
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
            def perform():
                if action=='add':
                    if not form.is_valid():raise _Invalid()
                    return add(request.user,branch,form.cleaned_data)
                return actions[action](request,branch)
            if action!='add' and action not in actions:raise KeyError(action)
            with transaction.atomic():result=s.once(key,request.user,{template:payload},perform)
            messages.success(request,f'Done: {result.code}.')
            return redirect(request.path+f'?branch={branch.pk}')
        except _Invalid:pass
        except (ValidationError,IntegrityError,ValueError,KeyError) as error:messages.error(request,error_text(error) if not isinstance(error,KeyError) else 'Unknown action.')
    return render(request,template,dict(title=title,form=form,branch=branch,today=timezone.localdate(),request_key=uuid.uuid4(),**items_fn(branch)))


class _Invalid(Exception):pass


def _date(request,name='through'):
    try:return date.fromisoformat(request.POST.get(name,''))
    except ValueError:raise ValidationError('Enter a valid date.')


def _need(result,message):
    """`once` stores a Document; an action that had nothing to post is reported as a validation message."""
    if result is None:raise ValidationError(message)
    return result


@login_required
def assets(request):
    def run_depreciation(request,branch):
        docs=depreciate_all(request.user,branch,_date(request))
        return _need(docs[-1] if docs else None,'Nothing is due for depreciation up to that date.')
    def dispose(request,branch):
        asset=FixedAsset.objects.get(pk=request.POST['asset'],branch=branch)
        return dispose_asset(request.user,asset,_date(request,'on'),request.POST.get('proceeds') or 0,request.POST.get('mode'),request.POST.get('reference',''))
    return _page(request,'assets.html','Fixed assets',AssetForm,add_asset,lambda b:dict(rows=FixedAsset.objects.filter(branch=b).order_by('-purchase_date'),modes=_modes(b)),dict(depreciate=run_depreciation,dispose=dispose))


@login_required
def deferrals(request):
    def process(request,branch):
        docs=process_deferrals(request.user,branch,_date(request))
        return _need(docs[-1] if docs else None,'Nothing is due up to that date.')
    return _page(request,'deferrals.html','Prepaid expenses & accruals',DeferralForm,add_deferral,lambda b:dict(rows=Deferral.objects.filter(branch=b).order_by('-start_date')),dict(process=process))


@login_required
def loans(request):
    def repay(request,branch):
        loan=Loan.objects.get(pk=request.POST['loan'],branch=branch)
        return repay_loan(request.user,loan,_date(request,'on'),request.POST.get('principal') or 0,request.POST.get('interest') or 0,request.POST.get('mode'),request.POST.get('reference',''))
    def items(b):
        rows=list(Loan.objects.filter(branch=b).order_by('-start_date'))
        for loan in rows:loan.suggested=suggested_interest(loan)
        return dict(rows=rows,modes=_modes(b))
    return _page(request,'loans.html','Loans',LoanForm,add_loan,items,dict(repay=repay))
