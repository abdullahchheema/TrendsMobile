import uuid
from . import catalog_entry
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.shortcuts import render,redirect,get_object_or_404
from django.utils import timezone
from .models import Device,Document,Branch,LedgerEntry
from . import access,services,easy_retail
from .easy_forms import EasyEntryForm,SalesAccountsForm


class InvalidEntry(Exception):pass


@login_required
def entry(request,kind):
    if kind not in ('sale','purchase'):raise Http404('Choose a sale or purchase.')
    from .views import selected_branch,scoped_devices,error_text
    branch=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:return redirect('setup')
    access.require(request.user,branch,'can_sell' if kind=='sale' else 'can_buy')
    phone=None
    if kind=='sale' and request.GET.get('phone') and not request.GET.get('branch') and request.method!='POST':
        phone=get_object_or_404(scoped_devices(request.user),pk=request.GET['phone'],status='Available');branch=phone.branch
    if kind=='sale' and request.GET.get('phone'):phone=get_object_or_404(scoped_devices(request.user),pk=request.GET['phone'],status='Available',branch=branch)
    form=EasyEntryForm(request.POST or None,user=request.user,branch=branch,kind=kind,phone=phone)
    if request.method=='POST':
        payload={key:request.POST.getlist(key) for key in request.POST if key not in ('csrfmiddlewaretoken','request_key')}
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():
                if not form.is_valid():raise InvalidEntry()
                return (easy_retail.sale if kind=='sale' else easy_retail.purchase)(form.cleaned_data,request.user)
            result=services.once(key,request.user,{'easy_entry':kind,'fields':payload},perform)
            messages.success(request,'Sale saved. Stock, payment and customer balance are updated.' if kind=='sale' else 'Purchase saved. Stock, payment and supplier balance are updated.')
            return redirect('easy_done',pk=result.pk)
        except InvalidEntry:pass
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    phones=[dict(id=str(p.pk),price=str(p.asking_price),pta=p.pta_status) for p in form.fields['phone'].queryset] if kind=='sale' else []
    return render(request,'easy_entry.html',dict(title='New Sale' if kind=='sale' else 'Buy Stock',kind=kind,form=form,branch=branch,phones=phones,catalog=catalog_entry.catalog() if kind=='purchase' else None))


@login_required
def done(request,pk):
    from .views import document
    doc=document(request.user,pk)
    access.require(request.user,doc.branch,'can_buy' if doc.kind=='Purchase Invoice' else 'can_sell')
    if doc.kind not in ('Sales Invoice','Purchase Invoice') or not doc.data.get('easy_entry'):return redirect('detail',pk=doc.pk)
    return render(request,'easy_done.html',dict(title='Sale saved' if doc.kind=='Sales Invoice' else 'Purchase saved',branch=doc.branch,doc=doc,paid=doc.total-doc.outstanding,
        purchase=doc.kind=='Purchase Invoice',lines=(doc.reference.lines if doc.kind=='Purchase Invoice' else doc.lines).select_related('product','device')))


@login_required
def sales_accounts(request):
    from datetime import date
    from . import financial_reports,reports as reporting
    from .views import selected_branch
    from django.urls import reverse
    from urllib.parse import urlencode
    branch=selected_branch(request.user,request.GET.get('branch'))
    if not branch:return redirect('setup')
    if not access.statements(request.user):return redirect('dashboard')
    filters=request.GET.copy();filters.setdefault('branch',str(branch.pk));filters.setdefault('from_date','2000-01-01');filters.setdefault('to_date',str(timezone.localdate()))
    form=SalesAccountsForm(filters,user=request.user,branch=branch)
    if not form.is_valid():return render(request,'sales_accounts.html',dict(title='Sales & Accounts',branch=branch,form=form),status=400)
    values=form.cleaned_data;branch=values['branch']
    start=values.get('from_date') or date(2000,1,1);end=values.get('to_date') or timezone.localdate()
    try:_,rows=financial_reports.run('Profit and Loss Statement',Branch.objects.filter(pk=branch.pk),start,end)
    except ValidationError as error:
        form.add_error(None,error);return render(request,'sales_accounts.html',dict(title='Sales & Accounts',branch=branch,form=form),status=400)
    summary=dict(financial_reports.summary('Profit and Loss Statement',rows))
    params={'branch':str(branch.pk),'to_date':str(end)}
    _,receivables=reporting.run('Accounts Receivable',request.user,params)
    _,payables=reporting.run('Accounts Payable',request.user,params)
    totals=dict(sales=summary['Income'],expenses=summary['Expenses'],profit=summary['Net profit / loss'],
        customers=sum((max(row['Outstanding'],0) for row in receivables),services.ZERO),
        suppliers=sum((max(row['Outstanding'],0) for row in payables),services.ZERO),
        customer_credit=sum((-min(row['Outstanding'],0) for row in receivables),services.ZERO),
        supplier_credit=sum((-min(row['Outstanding'],0) for row in payables),services.ZERO))
    reports=[dict(name=name,url=reverse('report',args=[name])+'?'+urlencode({**params,'from_date':str(start)})) for name in ('Sales Register','Purchase Register','Accounts Receivable','Accounts Payable','Profit and Loss Statement','Balance Sheet','Trial Balance')]
    return render(request,'sales_accounts.html',dict(title='Sales & Accounts',branch=branch,form=form,totals=totals,reports=reports,from_date=start,to_date=end))

