"""Expense, owner capital/drawings and loan vouchers (posts through services._voucher)."""
import uuid
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.shortcuts import redirect,render
from django.utils import timezone
from .models import Account,Branch,Document,ShopSettings
from . import access,permissions,services as s


class VoucherForm(forms.Form):
    def __init__(self,*args,user,branch,**kwargs):
        super().__init__(*args,**kwargs)
        owner=user.is_superuser
        kinds=[(k,v[0]) for k,v in s.VOUCHER_TYPES.items() if k not in s.SYSTEM_VOUCHERS and (owner or k in s.SHOP_VOUCHERS)]
        self.fields['request_key']=forms.UUIDField(widget=forms.HiddenInput(),initial=uuid.uuid4)
        self.fields['branch']=forms.ModelChoiceField(queryset=access.branches(user),initial=branch,widget=forms.HiddenInput())
        self.fields['type']=forms.ChoiceField(label='What is this for?',choices=kinds,initial=kinds[0][0])
        self.fields['posting_date']=forms.DateField(label='Date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        self.fields['amount']=forms.DecimalField(label='Amount (PKR)',min_value=0,max_digits=15,decimal_places=2)
        accounts=access.visible_accounts(user).filter(enabled=True,is_group=False).exclude(root_type='').exclude(account_type__in=['Payable','Receivable','Stock','Cash','Bank','Stock Received But Not Billed','Cost of Goods Sold']).order_by('root_type','code')
        choices=[('','Choose an account')]
        for root,label in (('Expense','Expense accounts'),('Equity','Owner equity accounts'),('Liability','Loan and liability accounts')):
            choices.append((label,[(str(a.pk),f'{a.name}') for a in accounts if a.root_type==root]))
        self.fields['account']=forms.ChoiceField(label='Account / category',choices=choices,help_text='Expenses: pick the category (rent, salaries, electricity...). Install the standard chart first if the list is empty.')
        config=ShopSettings.objects.filter(branch=branch).first()
        self.fields['mode']=forms.ChoiceField(label='Paid from / received into',choices=[(m,m) for m in (config.payment_methods if config else [])],initial='Cash')
        self.fields['reference']=forms.CharField(label='Bank / wallet transaction reference',required=False,max_length=180)
        self.fields['description']=forms.CharField(label='Description',max_length=240,widget=forms.TextInput(attrs={'placeholder':'e.g. October shop rent, paid to landlord'}))


@transaction.atomic
def post(data,user):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk)
    access.require(user,branch)
    doc=s.new_doc('Voucher',branch,posting_date=data['posting_date'],notes=data['description'],data=dict(type=data['type'],amount=str(data['amount']),account=data['account'],mode=data['mode'],reference=data.get('reference',''),description=data['description']))
    return s.submit(doc,user)


def _allowed(user):return access.manager(user)


@login_required
def vouchers(request):
    from .views import selected_branch,paginate
    if not _allowed(request.user):raise PermissionDenied('You do not have access to this page.')
    branch=selected_branch(request.user,request.GET.get('branch'))
    rows=Document.objects.filter(branch__in=access.branches(request.user),kind='Voucher').select_related('branch').order_by('-posting_date','-created')
    page,pager_query=paginate(request,rows,50)
    return render(request,'vouchers.html',dict(title='Expenses & owner entries',rows=page.object_list,page_obj=page,pager_query=pager_query,branch=branch))


@login_required
def voucher_new(request):
    from .views import selected_branch,error_text
    if not _allowed(request.user):raise PermissionDenied('You do not have access to this page.')
    branch=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:return redirect('setup')
    from . import coa
    if not Account.objects.filter(code__startswith=coa.PREFIX).exists():coa.install_standard(request.user)       # expense categories ready on first use
    form=VoucherForm(request.POST or None,user=request.user,branch=branch)
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():
                if not form.is_valid():raise _Invalid()
                return post(form.cleaned_data,request.user)
            payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
            doc=s.once(key,request.user,{'voucher':payload},perform)
            messages.success(request,f'{doc.data.get("label","Voucher")} {doc.code} posted for PKR {doc.total:,.2f}.')
            return redirect('vouchers')
        except _Invalid:pass
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    return render(request,'voucher_form.html',dict(title='New voucher',form=form,branch=branch,roots={str(a.pk):a.root_type for a in Account.objects.exclude(root_type='')}))


class _Invalid(Exception):pass
