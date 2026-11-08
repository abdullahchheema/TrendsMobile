import csv
import json
import uuid
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.core.exceptions import ValidationError,PermissionDenied,ObjectDoesNotExist
from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction,IntegrityError
from django.db.models import Q,Sum
from django.http import JsonResponse,HttpResponse,Http404,FileResponse
from django.shortcuts import render,redirect,get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.views.decorators.http import require_POST
from .models import *
from . import access,services,reports as reporting,pricing
from .forms import DeviceForm,DocumentForm,LineSet,ReturnLineSet,MASTER_MODELS,DETAIL_FIELDS
from .report_filters import ReportFilters,defaults as report_defaults
from . import report_catalog,report_presentation
from . import setup as shop_setup
from . import fund_transfers as funds
from . import journals,customer_names
from .fund_transfer_forms import FundTransferForm
from .journal_forms import JournalForm,JournalRows
from .setup_forms import CreateShopForm,PostingAccountsForm,PaymentMethods,payment_mapping,StaffSetupForm,AccountSetupForm

def error_text(error):return '; '.join(error.messages) if isinstance(error,ValidationError) else str(error)
def scoped(user):
    places=access.branches(user)
    query=Q(branch__in=places)
    if access.manager(user):query|=Q(kind='Stock Entry',status='Submitted',data__target_branch__in=[str(pk) for pk in places.values_list('pk',flat=True)])
    return Document.objects.filter(query)
def scoped_devices(user):
    if user.is_superuser:return Device.objects.all()
    places=access.branches(user)
    return Device.objects.filter(branch__in=places)
def document(user,pk):
    doc=get_object_or_404(scoped(user),pk=pk)
    if doc.kind=='Stock Entry' and not access.manager(user):raise PermissionDenied('Stock transfer records require management access.')
    if funds.is_transfer(doc) and not funds.allowed(user):raise PermissionDenied('Internal transfer records require payment or management access.')
    if journals.is_manual(doc):shop_setup.owner(user)
    return doc
def selected_branch(user,value=None):
    places=access.branches(user)
    try:branch=places.filter(pk=value).first() if value else places.exclude(code='COV-DEMO').first() or places.first()
    except (ValueError,ValidationError):raise Http404('Invalid shop.')
    if value and not branch:raise Http404('Shop not available.')
    return branch

def health(request):
    from django.db import connection,DatabaseError
    try:
        with connection.cursor() as cursor:cursor.execute('SELECT 1')
    except DatabaseError:
        response=JsonResponse({'status':'unavailable'},status=503)
    else:response=JsonResponse({'status':'ok','backend':'Django transaction API','database':'Supabase PostgreSQL' if settings.SUPABASE_AUTH_ENABLED else 'PostgreSQL',
        'authentication':'Supabase Auth' if settings.SUPABASE_AUTH_ENABLED else 'Django','storage':'Supabase Storage' if settings.SUPABASE_AUTH_ENABLED else 'Local','offline_local_server':True})
    response['Cache-Control']='no-store';return response

@login_required
def device_check(request):
    access.staff(request.user)
    return render(request,'device_check.html',{'title':'PC, scanner & printer check'})

@login_required
def device_print_test(request):
    access.staff(request.user)
    paper=request.GET.get('paper','80')
    if paper not in ('58','80','a4'):raise Http404
    return render(request,'device_print_test.html',{'paper':paper,'sample_stock':'PHONE-DEMO-001'})

@login_required
def dashboard(request):
    devices=scoped_devices(request.user).select_related('product__model','warehouse').prefetch_related('imeis')
    branch=selected_branch(request.user,request.GET['branch']) if request.GET.get('branch') else None
    if branch:devices=devices.filter(branch=branch)
    all_devices=list(devices);available=[d for d in all_devices if d.status=='Available']
    search=request.GET.get('search','').strip().lower();status=request.GET.get('status','Available')
    rows=[d for d in all_devices if (status=='All' or d.status==status) and (not request.GET.get('model') or str(d.product.model_id)==request.GET['model'])
        and (not request.GET.get('condition') or d.condition==request.GET['condition']) and (not request.GET.get('pta_status') or d.pta_status==request.GET['pta_status'])
        and (not search or any(search in str(v).lower() for v in [d.code,d.product.name,d.product.storage,d.product.colour,*[i.number for i in d.imeis.all()]]))]
    summary=dict(available=len(available),stock_value=sum((d.purchase_cost for d in available),Decimal(0)),
        pending=sum(d.inspection_status!='Passed' for d in available),missing=sum(d.pta_status=='Not recorded' for d in available),
        sold_today=sum(d.status=='Sold' and d.sold_on==timezone.localdate() for d in all_devices))
    groups={}
    for d in rows:
        key=(d.product.model_id,d.product.storage,d.product.ram,d.product.colour)
        if key not in groups:groups[key]=dict(model_id=d.product.model_id,name=d.product.model.name if d.product.model_id else d.product.name,storage=d.product.storage,ram=d.product.ram,colour=d.product.colour,phones=[])
        groups[key]['phones'].append(d)
    for g in groups.values():g['min']=min(d.asking_price for d in g['phones']);g['max']=max(d.asking_price for d in g['phones'])
    query=request.GET.copy();query.pop('view',None)
    insight=dict(shop=branch or selected_branch(request.user))
    if insight['shop'] and access.costs(request.user):
        from . import dashboard_metrics as dm
        trend=dm.sales_trend(insight['shop'])
        manager_view=access.statements(request.user)
        insight.update(money=dm.finance(request.user,insight['shop']) if manager_view else None,balances=dm.balances(insight['shop']) if manager_view else [],trend=trend,
                       chart=dm.chart_geometry(trend),recent=dm.recent(insight['shop']),week=sum((p['net'] for p in trend[-7:]),Decimal(0)))
    return render(request,'dashboard.html',dict(insight=insight,title='Mobile Shop',devices=rows,summary=summary,groups=groups.values(),view_query=query.urlencode(),selected_shop=branch,branch=branch,
        demo_shop=access.branches(request.user).filter(code='COV-DEMO').first(),models=PhoneModel.objects.order_by('name'),demo=any(d.demo_batch for d in all_devices)))

@login_required
def collection(request,kind):
    if kind=='Account':shop_setup.owner(request.user)
    if kind=='Mobile Shop Settings':return redirect('settings')
    if kind=='Mobile Device':
        objects=scoped_devices(request.user).select_related('product','warehouse').prefetch_related('imeis').order_by('code')
        rows=[dict(id=d.pk,code=d.code,name=d.product.name,state=d.status,amount=d.asking_price,party=str(d.warehouse or '')) for d in objects]
    elif kind in MASTER_MODELS:
        model=MASTER_MODELS[kind][0];objects=model.objects.all()
        if kind in ('Customer','Supplier'):objects=objects.filter(kind=kind)
        if kind in ('Mobile Staff','Warehouse','Till'):objects=objects.filter(branch__in=access.branches(request.user))
        if kind=='Shop':objects=Branch.objects.all() if request.user.is_superuser else access.branches(request.user)
        rows=[dict(id=o.pk,code=o.code,name=o.name,state=getattr(o,'designation','') or ('Active' if getattr(o,'enabled',True) else 'Inactive'),party=str(getattr(o,'branch','')),amount=None) for o in objects.order_by('code')]
    elif kind in KINDS:
        objects=scoped(request.user).filter(kind__in=services.SELL if kind=='Sales Invoice' else [kind]).select_related('customer','supplier').order_by('-posting_date','-created')
        if kind in services.BUY and not access.costs(request.user):raise PermissionDenied
        if kind=='Stock Entry' and not access.manager(request.user):raise PermissionDenied
        if kind=='Payment Entry' and not funds.allowed(request.user):objects=objects.filter(Q(data__direction__isnull=True)|~Q(data__direction='Internal Transfer'))
        if kind=='Journal Entry' and not request.user.is_superuser:objects=objects.filter(Q(data__manual_journal__isnull=True)|~Q(data__manual_journal__version=1))
        rows=[dict(id=o.pk,code=o.code,name=str(o.customer or o.supplier or ''),state=o.status+(' · '+o.state if o.state else ''),amount=o.total,currency=o.currency,party=o.posting_date) for o in objects]
        if kind in ('POS Opening Entry','POS Closing Entry'):
            rows=[]
            for o in objects.select_related('till','handler'):
                reconciliation=o.data.get('reconciliation',{})
                name=(o.data.get('till_name') or reconciliation.get('till') or str(o.till or 'Legacy till'))+' · '+(o.staff_names.get('handler') or str(o.handler or ''))
                rows.append(dict(id=o.pk,code=o.code,name=name,state=o.status+(' · '+o.state if o.state else ''),amount=reconciliation.get('counted') if kind=='POS Closing Entry' else o.data.get('opening_float'),currency=o.currency,party=o.posting_date))
    else:raise Http404
    query=request.GET.get('search','').lower()
    if query:rows=[r for r in rows if any(query in str(value).lower() for value in r.values())]
    return render(request,'list.html',dict(title=kind,kind=kind,rows=rows))

@login_required
def master_edit(request,kind,pk=None):
    if kind not in MASTER_MODELS:raise Http404
    model,fields=MASTER_MODELS[kind]
    if kind in ('Shop','Account'):
        shop_setup.owner(request.user)
        if kind=='Shop' and not pk:return redirect('setup_new')
    if kind not in ('Customer','Supplier') and not access.manager(request.user):raise PermissionDenied('A manager must edit setup records.')
    instance=get_object_or_404(model,pk=pk) if pk else model()
    if kind in ('Customer','Supplier'):
        if pk and instance.kind!=kind:raise Http404
        instance.kind=kind
    if kind in ('Mobile Staff','Warehouse','Till') and pk:access.require(request.user,instance.branch)
    if kind=='Shop' and pk and not request.user.is_superuser and not access.branches(request.user).filter(pk=pk).exists():raise PermissionDenied
    Form=StaffSetupForm if kind=='Mobile Staff' else AccountSetupForm if kind=='Account' else forms.modelform_factory(model,fields=fields)
    form=Form(request.POST or None,instance=instance,**({'user':request.user} if kind=='Mobile Staff' else {}))
    if 'code' in form.fields and not pk:form.fields['code'].required=False
    if 'branch' in form.fields and not pk and request.GET.get('branch'):
        branch=selected_branch(request.user,request.GET['branch']);form.initial['branch']=branch.pk
        if kind=='Mobile Staff':form.fields['default_warehouse'].queryset=Warehouse.objects.filter(branch=branch,enabled=True)
    if 'branch' in form.fields:form.fields['branch'].queryset=access.branches(request.user)
    if 'user' in form.fields:
        from django.contrib.auth import get_user_model
        form.fields['user'].queryset=get_user_model().objects.filter(is_superuser=False).filter(Q(staff__isnull=True)|Q(staff=instance))
        form.fields['user'].queryset=form.fields['user'].queryset.filter(Q(is_active=True)|Q(pk=instance.user_id))
        if settings.SUPABASE_AUTH_ENABLED:form.fields['user'].queryset=form.fields['user'].queryset.filter(Q(pk__in=SupabaseIdentity.objects.values('user_id'))|Q(pk=instance.user_id))
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                if kind in ('Shop','Warehouse','Till','Mobile Staff'):
                    old=model.objects.filter(pk=pk).first() if pk else None
                    target=form.cleaned_data.get('branch')
                    ids={value for value in ([old.pk] if kind=='Shop' and old else [getattr(old,'branch_id',None),getattr(target,'pk',None)]) if value}
                    list(Branch.objects.select_for_update().filter(pk__in=ids).order_by('pk'))
                    if pk and kind!='Shop' and model.objects.get(pk=pk).branch_id not in ids:raise ValidationError('The shop assignment changed while editing. Reload this record.')
                if kind=='Account' and pk:Account.objects.select_for_update().get(pk=pk)
                obj=form.save(commit=False)
                if not obj.code:obj.code=services.code(kind.upper())
                obj.full_clean()
                obj.save()
                Audit.objects.create(user=request.user,action='Save setup record',details={'kind':kind,'id':str(obj.pk),'code':obj.code})
        except (ValidationError,IntegrityError) as error:form.add_error(None,error_text(error))
        else:
            messages.success(request,'Record saved.');return redirect('collection',kind=kind)
    return render(request,'form.html',dict(title=kind,form=form,master=True))

@login_required
def device_edit(request,pk):
    obj=get_object_or_404(scoped_devices(request.user).select_related('product'),pk=pk)
    place=obj.branch
    access.require(request.user,place)
    person=access.staff(request.user)
    editable=person is None or person.manager or person.can_buy
    if request.method=='POST' and not editable:raise PermissionDenied('Purchasing staff or a manager must change device details.')
    form=DeviceForm(request.POST or None,request.FILES or None,instance=obj,user=request.user)
    if not editable:
        for field in form.fields.values():field.disabled=True
    if request.method=='POST' and form.is_valid():
        try:
            with transaction.atomic():
                locked=Device.objects.select_for_update().get(pk=obj.pk)
                access.require(request.user,locked.branch)
                values=form.cleaned_data
                for name in form._meta.fields:
                    if name in values:setattr(locked,name,values[name])
                locked.details.update({name:values[name] for name in DETAIL_FIELDS});locked.full_clean();locked.save()
                Audit.objects.create(user=request.user,action='Update device',details={'device':str(obj.pk)})
            messages.success(request,'Device details saved.');return redirect('collection',kind='Mobile Device')
        except (ValidationError,IntegrityError) as e:form.add_error(None,error_text(e))
    return render(request,'form.html',dict(title=f'{obj.code} · {obj.product.name}',form=form,device=obj,read_only=not editable))

@login_required
def create_document(request,kind,pk=None):
    if kind=='Journal Entry':return redirect('journal_edit',pk=pk) if pk else redirect('journal_new')
    if kind not in KINDS:raise Http404
    if kind=='Stock Entry' and not access.manager(request.user):raise PermissionDenied
    if kind in services.BUY and not access.costs(request.user):raise PermissionDenied('Purchasing access is required to view purchase forms.')
    obj=document(request.user,pk) if pk else Document(kind=kind)
    if pk and funds.is_transfer(obj):return redirect('fund_transfer_edit',pk=obj.pk)
    if pk and (obj.kind!=kind or obj.status!='Draft'):return redirect('detail',pk=obj.pk)
    original_branch_id=obj.branch_id
    initial=[]
    if not pk and kind=='Payment Entry' and request.GET.get('invoice'):
        invoice=document(request.user,request.GET['invoice'])
        if invoice.status!='Submitted' or invoice.kind not in services.SELL or not invoice.is_return or invoice.outstanding>=0:raise Http404
        access.require(request.user,invoice.branch,'can_handle_payments')
        if invoice.reference and invoice.reference.outstanding>0:return render(request,'empty.html',dict(title='Return credit',text='Apply return credit to the original unpaid invoice before recording a refund.'),status=400)
        obj.branch=invoice.branch;obj.customer=invoice.customer;obj.reference=invoice
        shift=services.active_shift(invoice.branch,request.user)
        if shift:obj.handler=shift.handler
        obj.data.update(direction='Pay',amount=str(-invoice.outstanding),mode='Cash' if 'Cash' in ShopSettings.objects.get(branch=invoice.branch).payment_methods else '')
    if not pk and kind=='POS Closing Entry' and request.GET.get('shift'):
        shift=get_object_or_404(scoped(request.user),pk=request.GET['shift'],kind='POS Opening Entry',status='Submitted',state='Open')
        if shift.posted_by_id!=request.user.pk and not access.manager(request.user):raise PermissionDenied('Close your own shift.')
        obj.reference=shift;obj.branch=shift.branch;obj.posting_date=shift.posting_date if shift.till_id else timezone.localdate();obj.handler=shift.handler
    if not pk and kind=='POS Opening Entry' and request.GET.get('branch'):obj.branch=get_object_or_404(access.branches(request.user),pk=request.GET['branch'])
    if not pk and kind in services.SELL and request.GET.get('device'):
        phone=get_object_or_404(scoped_devices(request.user),pk=request.GET['device'],status='Available')
        obj.warehouse=phone.warehouse;obj.branch=phone.warehouse.branch
        initial=[dict(product=phone.product,device=phone,warehouse=phone.warehouse,quantity=1,rate=phone.asking_price)]
    form=DocumentForm(request.POST or None,instance=obj,user=request.user,kind=kind)
    if not pk and kind=='Payment Entry' and request.GET.get('invoice'):
        form.initial.update({key:value for key,value in obj.data.items() if key in form.fields})
    use_lines=kind in services.BUY|services.SELL|{'Stock Entry'}
    line_set=ReturnLineSet if obj.is_return else LineSet
    lines=line_set(request.POST or None,instance=obj,prefix='lines',initial=initial,form_kwargs={'user':request.user,'kind':kind,'is_return':obj.is_return}) if use_lines else None
    if request.method=='POST' and form.is_valid() and (not lines or lines.is_valid()):
        try:
            with transaction.atomic():
                if pk:
                    places=[original_branch_id,form.cleaned_data['branch'].pk]
                    list(Branch.objects.select_for_update(no_key=True).filter(pk__in=places).order_by('pk'))
                    current=Document.objects.select_for_update(no_key=True).get(pk=pk)
                    if current.status!='Draft' or current.branch_id!=original_branch_id:raise ValidationError('This draft was posted or changed while editing. Reload it before saving.')
                else:Branch.objects.select_for_update(no_key=True).get(pk=form.cleaned_data['branch'].pk)
                draft=form.save(commit=False);draft.kind=kind;services.authorize(request.user,draft)
                if not draft.code:draft.code=services.code('COV')
                form.apply_customers(draft,request.user)
                form.apply_data(draft)
                if not access.costs(request.user) and draft.kind in services.BUY:raise PermissionDenied
                draft.save()
                if lines:lines.instance=draft;lines.save()
                if draft.is_return:
                    draft.lines.update(warehouse=draft.warehouse)
                if not draft.lines.exists() and use_lines:raise ValidationError('Add at least one item.')
                if kind=='Warranty Claim':
                    phone=Device.objects.get(pk=draft.data['device'])
                    if not phone.sale_id or phone.sale.branch_id!=draft.branch_id or phone.customer_id!=draft.customer_id or draft.reference_id!=phone.sale_id:raise ValidationError('Link this phone’s original customer and sales invoice.')
                if draft.is_return:
                    from .returns import plan,capture_totals
                    capture_totals(draft,[line.return_parts for line,source,amount,cost in plan(draft)])
                elif kind in services.SELL:
                    draft.data['pricing'],_=pricing.calculate(draft);draft.total=services.money(draft.data['pricing']['grand_total'])
                else:draft.total=sum((services.money(l.quantity*l.rate) for l in draft.lines.all()),services.ZERO)
                draft.save()
                services.audit(request.user,'Save draft',draft)
            return redirect('detail',pk=draft.pk)
        except (ValidationError,IntegrityError,KeyError) as e:form.add_error(None,error_text(e))
    return render(request,'form.html',dict(title='Return credit note' if obj.is_return else kind,form=form,lines=lines,kind=kind,return_edit=obj.is_return,reconciliation=form.pos_reconciliation))

@login_required
def detail(request,pk):
    doc=document(request.user,pk)
    if doc.kind in services.BUY and not access.costs(request.user):raise PermissionDenied
    config=ShopSettings.objects.get(branch=doc.branch)
    related=doc.linked.all()
    open_invoices=scoped(request.user).filter(status='Submitted',outstanding__gt=0,kind__in=['Sales Invoice','POS Invoice','Purchase Invoice'])
    open_invoices=open_invoices.filter(customer=doc.customer) if doc.customer_id else open_invoices.filter(supplier=doc.supplier) if doc.supplier_id else open_invoices.none()
    reconciliation=doc.data.get('reconciliation')
    return_rows=[];return_warning=''
    if doc.kind in services.SELL and doc.status=='Submitted' and not doc.is_return:
        from .returns import options
        try:return_rows=options(doc)
        except ValidationError as error:return_warning=error_text(error)
    person=access.staff(request.user);can_refund=person is None or person.manager or person.can_handle_payments
    credit_available=max(-doc.outstanding,services.ZERO) if doc.is_return and doc.kind in services.SELL and doc.status=='Submitted' else services.ZERO
    original_due=max(doc.reference.outstanding,services.ZERO) if credit_available and doc.reference_id else services.ZERO
    credit_to_apply=min(credit_available,original_due)
    if doc.kind=='POS Opening Entry' and doc.status=='Submitted':
        closing=doc.linked.filter(kind='POS Closing Entry',status='Submitted').first()
        reconciliation=closing.data.get('reconciliation') if closing else services.till_balance(doc)
    elif doc.kind=='POS Closing Entry' and doc.status=='Draft' and doc.reference_id:reconciliation=services.till_balance(doc.reference)
    return render(request,'detail.html',dict(title=doc.code,doc=doc,reconciliation=reconciliation,lines=doc.lines.select_related('product','device','warehouse'),open_invoices=open_invoices,
        has_advance=doc.kind=='Payment Entry' and services.money(doc.data.get('unallocated'))>0,
        related=related,methods=config.payment_methods,cash_modes=services.cash_modes(doc.branch),cash_summary=services.cash_summary(doc),staff=Staff.objects.filter(branch=doc.branch,enabled=True,can_handle_payments=True),
        request_key=uuid.uuid4(),can_checkout=doc.kind in services.SELL and not doc.is_return,kind=doc.kind,
        return_rows=return_rows,return_warning=return_warning,is_sale_return=doc.is_return and doc.kind in services.SELL,has_returnable=any(line.returnable for line in return_rows),
        return_warehouses=Warehouse.objects.filter(branch=doc.branch,enabled=True).order_by('code'),credit_to_apply=credit_to_apply,credit_available=credit_available,can_refund=can_refund,
        internal_transfer=funds.is_transfer(doc),frozen_fund_transfer=funds.is_transfer(doc) and bool(doc.source),
        owner_journal=journals.is_manual(doc),journal_rows=doc.data.get('manual_journal',{}).get('lines',[]) if journals.is_manual(doc) else [],
        can_correct_journal=journals.is_manual(doc) and doc.status=='Submitted' and not doc.source and not doc.data['manual_journal'].get('reversal_of'),
        can_reverse_funds=funds.is_transfer(doc) and doc.status=='Submitted' and not doc.source and funds.allowed(request.user) and not doc.linked.filter(status='Submitted',data__direction='Internal Transfer').exists(),
        can_cancel=access.manager(request.user) and not doc.source and not doc.is_return and not funds.is_transfer(doc) and doc.kind not in ('Mobile Daily Closing','POS Opening Entry','POS Closing Entry','Mobile Exchange','Journal Entry','Stock Entry')))

@login_required
@require_POST
def action(request,pk,name):
    doc=document(request.user,pk)
    try:
        key=uuid.UUID(request.POST['request_key'])
        payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
        def perform():
            if name=='submit':return services.submit(doc,request.user)
            if name=='bill':return services.supplier_bill(doc,request.user)
            if name=='checkout':
                payments=[]
                from itertools import zip_longest
                tenders=request.POST.getlist('payment_cash_value') if 'payment_cash_value' in request.POST else request.POST.getlist('payment_tendered')
                for mode,amount,reference,tendered in zip_longest(request.POST.getlist('payment_mode'),request.POST.getlist('payment_amount'),request.POST.getlist('payment_reference'),tenders,fillvalue=''):
                    if (amount and services.money(amount)) or tendered:
                        payment=dict(mode=mode,amount=amount,reference=reference)
                        if tendered:payment['cash_tendered']=tendered
                        payments.append(payment)
                handler=Staff.objects.get(pk=request.POST['handler']) if request.POST.get('handler') else None
                return services.checkout(doc,request.user,payments,handler)
            if name=='return':
                quantities={key[len('return_qty_'):]:value for key,value in request.POST.items() if key.startswith('return_qty_') and value.strip() and value.strip() not in ('0','0.0','0.00','0.000')}
                target=get_object_or_404(Warehouse,pk=request.POST['return_warehouse'],branch=doc.branch,enabled=True) if request.POST.get('return_warehouse') else None
                return services.return_sale(doc,request.user,request.POST.getlist('devices'),request.POST.get('condition',''),request.POST.get('reason',''),quantities,target)
            if name=='apply-return-credit':return services.apply_return_credit(doc,request.POST.get('amount'),request.user)
            if name=='settle':return services.settle_exchange(doc,request.user)
            if name=='cancel':return services.cancel(doc,request.user)
            if name=='reverse-funds':return funds.reverse(doc,request.user)
            if name in ('reverse-journal','replace-journal'):return journals.prepare(doc,request.user,replacement=name=='replace-journal')
            if name=='allocate':return services.allocate_advance(doc,document(request.user,request.POST['invoice']),request.POST['amount'],request.user)
            if name=='claim':
                services.authorize(request.user,doc)
                if doc.kind!='Warranty Claim':raise ValidationError('Select a warranty claim.')
                if request.POST.get('state') not in ('Open','Work In Progress','Closed','Cancelled'):raise ValidationError('Invalid claim status.')
                doc.state=request.POST['state'];doc.data['resolution']=request.POST.get('resolution','');doc.save()
                services.audit(request.user,'Update warranty',doc);return doc
            raise Http404
        result=services.once(key,request.user,{'document':str(doc.pk),'action':name,**payload},perform)
        if name=='reverse-funds':
            messages.success(request,'Reverse transfer draft created. Enter the actual reference and review cash custody before submitting.')
            return redirect('fund_transfer_edit',pk=result.pk)
        if name in ('reverse-journal','replace-journal'):
            messages.success(request,'Journal correction draft prepared. Review its date, supporting reference and reason before posting.')
            return redirect('journal_edit',pk=result.pk)
        messages.success(request,'Action completed.');return redirect('detail',pk=result.pk)
    except (ValidationError,IntegrityError,KeyError,ValueError,ObjectDoesNotExist) as error:
        messages.error(request,error_text(error));return redirect('detail',pk=doc.pk)

@login_required
def journal_list(request):
    shop_setup.owner(request.user)
    branch=selected_branch(request.user,request.GET.get('branch')) if request.GET.get('branch') else None
    docs=scoped(request.user).filter(kind='Journal Entry',data__manual_journal__version=1).select_related('branch','reference')
    if branch:docs=docs.filter(branch=branch)
    return render(request,'journals.html',dict(title='Accounting adjustments',journals=docs,selected_shop=branch))


@login_required
def journal_edit(request,pk=None):
    shop_setup.owner(request.user)
    doc=document(request.user,pk) if pk else None
    if doc and (not journals.is_manual(doc) or doc.source):raise PermissionDenied('Only new owner journal drafts can be edited here.')
    if doc and doc.status!='Draft':return redirect('detail',pk=doc.pk)
    places=access.branches(request.user)
    branch=doc.branch if doc else selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:raise Http404('Configure an active shop first.')
    form=JournalForm(request.POST or None,places=places,branch=branch,doc=doc)
    initial=doc.data['manual_journal']['lines'] if doc else []
    protected=bool(doc and doc.data['manual_journal'].get('reversal_of'))
    rows=JournalRows(request.POST or None,prefix='rows',initial=initial,protected=protected,form_kwargs=dict(branch=branch,protected=protected))
    if request.method=='POST' and form.is_valid() and rows.is_valid():
        try:
            values={**form.cleaned_data,'rows':rows.rows()}
            payload={key:request.POST.getlist(key) for key in request.POST if key not in ('csrfmiddlewaretoken','request_key')}
            saved=services.once(values['request_key'],request.user,dict(operation='save_owner_journal',document=str(doc.pk) if doc else None,fields=payload),lambda:journals.save(values,request.user,doc))
            messages.success(request,'Journal draft saved. Review all debits and credits before submitting.');return redirect('detail',pk=saved.pk)
        except (ValidationError,IntegrityError) as error:form.add_error(None,error_text(error))
    return render(request,'journal_form.html',dict(title='Journal correction draft' if doc and doc.reference_id else 'Accounting adjustment draft',form=form,rows=rows,doc=doc,selected_shop=branch,protected_rows=protected))


@login_required
def fund_transfer_list(request):
    if not funds.allowed(request.user):raise PermissionDenied
    branch=selected_branch(request.user,request.GET.get('branch')) if request.GET.get('branch') else None
    docs=scoped(request.user).filter(kind='Payment Entry',data__direction='Internal Transfer').select_related('branch','handler','till')
    if branch:docs=docs.filter(branch=branch)
    return render(request,'fund_transfers.html',dict(title='Cash & bank transfers',transfers=docs.order_by('-posting_date','-created'),selected_shop=branch))

@login_required
def fund_transfer_edit(request,pk=None):
    if not funds.allowed(request.user):raise PermissionDenied
    doc=document(request.user,pk) if pk else None
    if doc and (not funds.is_transfer(doc) or doc.source):raise PermissionDenied('Only new internal-transfer drafts can be edited here.')
    if doc and doc.status!='Draft':return redirect('detail',pk=doc.pk)
    branch=selected_branch(request.user,request.GET.get('branch')) if request.GET.get('branch') else None
    form=FundTransferForm(request.POST or None,user=request.user,doc=doc,branch=branch)
    if request.method=='POST' and form.is_valid():
        try:
            payload={key:request.POST.getlist(key) for key in request.POST if key not in ('csrfmiddlewaretoken','request_key')}
            saved=services.once(form.cleaned_data['request_key'],request.user,dict(operation='save_fund_transfer',document=str(doc.pk) if doc else None,fields=payload),lambda:funds.save(form.cleaned_data,request.user,doc))
            messages.success(request,'Transfer draft saved. Review it before submitting the completed movement.')
            return redirect('detail',pk=saved.pk)
        except (ValidationError,IntegrityError) as error:form.add_error(None,error_text(error))
    return render(request,'fund_transfer_form.html',dict(title='Reverse transfer draft' if doc and doc.reference_id else 'Internal transfer draft',form=form,doc=doc,selected_shop=form.branch))

@login_required
def users(request):
    if not access.manager(request.user):raise PermissionDenied
    from django.contrib.auth.forms import UserCreationForm
    class LoginForm(UserCreationForm):
        staff_member=forms.ModelChoiceField(queryset=Staff.objects.filter(branch__in=access.branches(request.user),user__isnull=True,enabled=True))
        if settings.SUPABASE_AUTH_ENABLED:username=forms.EmailField(label='Email')
    form=LoginForm(request.POST or None)
    form.fields['staff_member'].label_from_instance=lambda person:f'{person.name} · {person.branch.name}'
    if request.method=='POST' and form.is_valid():
        from . import supabase
        external=None
        try:
            with transaction.atomic():
                selected=Staff.objects.select_for_update().get(pk=form.cleaned_data['staff_member'].pk)
                if selected.user_id:raise ValidationError('This staff member already has a login.')
                else:
                    if settings.SUPABASE_AUTH_ENABLED:external=supabase.create_user(form.cleaned_data['username'],form.cleaned_data['password1'])
                    account=form.save()
                    if external:
                        account.email=form.cleaned_data['username'];account.set_unusable_password();account.save()
                        SupabaseIdentity.objects.create(auth_id=external['id'],user=account)
                    selected.user=account;selected.save()
        except (ValidationError,supabase.ProviderUnavailable,IntegrityError) as error:
            if external:
                try:supabase.delete_user(external['id'])
                except (ValidationError,supabase.ProviderUnavailable):pass
            form.add_error(None,error_text(error))
        else:
            messages.success(request,'Staff login created. Responsibilities come from the Staff record.');return redirect('users')
    return render(request,'form.html',dict(title='Staff login accounts',form=form,master=True))

@login_required
def report_center(request):
    access.staff(request.user)
    search=request.GET.get('search','')[:200]
    groups=report_catalog.groups(request.user,search)
    return render(request,'report_center.html',dict(title='Reports',report_groups=groups,report_count=sum(len(group['reports']) for group in groups),search=search))


@login_required
def report(request,name):
    reporting.authorize(name,request.user)
    form=ReportFilters(request.GET if request.GET else report_defaults(name),user=request.user,name=name)
    columns=[];rows=[];error=None;statement_summary=[]
    if form.is_valid():
        try:
            columns,rows=reporting.run(name,request.user,form.params())
            from .financial_reports import summary
            statement_summary=[dict(label=label,value=format(value,',.2f')) for label,value in summary(name,rows)]
            rows=report_presentation.select_rows(columns,rows,form.params())
        except ValidationError as failure:error=error_text(failure)
    else:error='Check the report filters below.'
    if error:return render(request,'report.html',dict(title=name,filter_form=form,error=error),status=400)
    if request.GET.get('format')=='csv':
        response=HttpResponse(content_type='text/csv');response['Content-Disposition']='attachment; filename="'+slugify(name)+'.csv"'
        writer=csv.writer(response);writer.writerow(columns)
        for row in rows:
            writer.writerow([("'"+value) if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) else value for value in (row[c] for c in columns)])
        return response
    presentation=report_presentation.context(name,columns,rows,form.params(),scoped(request.user),scoped_devices(request.user),request.user)
    included=access.branches(request.user)
    if form.cleaned_data.get('branch'):included=included.filter(pk=form.cleaned_data['branch'].pk)
    presentation['includes_training']=included.filter(code='COV-DEMO').exists()
    applied=[dict(label=form.fields[key].label or key.replace('_',' ').title(),value=str(value)) for key,value in form.cleaned_data.items() if value not in (None,'',False) and key not in ('sort_by','sort_dir')]
    return render(request,'report.html',dict(title=report_catalog.label(name),filter_form=form,columns=columns,rows=[[r.get(c,'') for c in columns] for r in rows],query=request.GET.urlencode(),statement_summary=statement_summary,inventory_report=name in ('Mobile Inventory','Mobile Stock Ageing','Mobile Device Economics'),applied_filters=applied,**presentation))

@login_required
def shop_settings(request):
    if not access.manager(request.user):raise PermissionDenied
    branch=selected_branch(request.user,request.GET.get('branch'))
    if not branch:return render(request,'empty.html',{'title':'Shop Settings','text':'Create a shop and accounts before configuration.'})
    obj,_=ShopSettings.objects.get_or_create(branch=branch)
    Form=forms.modelform_factory(ShopSettings,fields=['shop_phone','shop_address','receipt_terms','urdu_receipt_note','ageing_days'])
    form=Form(request.POST or None,instance=obj)
    if request.method=='POST' and form.is_valid():form.save();messages.success(request,'Shop settings saved.');return redirect(reverse('settings')+'?branch='+str(branch.pk))
    return render(request,'form.html',dict(title='Shop Settings',form=form,master=True,settings_branch=branch,settings_shops=access.branches(request.user)))

@login_required
def setup_dashboard(request):
    shop_setup.owner(request.user)
    rows=[shop_setup.readiness(branch) for branch in access.branches(request.user).order_by('code')]
    return render(request,'setup.html',dict(title='Shop setup',rows=rows,ready_count=sum(row['ready'] for row in rows)))

@login_required
def setup_new(request):
    shop_setup.owner(request.user);form=CreateShopForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:branch=shop_setup.create_shop(form.cleaned_data,request.user)
        except ValidationError as error:form.add_error(None,error_text(error))
        except IntegrityError:form.add_error(None,'A shop or account code is already in use. Choose a different shop code.')
        else:
            messages.success(request,'Shop, warehouse, till and accounts created. Add staff and login assignments next.');return redirect('setup')
    return render(request,'form.html',dict(title='Create shop setup',form=form,master=True))

@login_required
def setup_finance(request,pk):
    shop_setup.owner(request.user);branch=selected_branch(request.user,pk)
    configuration=ShopSettings.objects.filter(branch=branch).first() or ShopSettings(branch=branch)
    form=PostingAccountsForm(request.POST or None,settings=configuration)
    methods=PaymentMethods(request.POST or None,prefix='methods',initial=[{'label':name,'account':account} for name,account in configuration.payment_methods.items()])
    if request.method=='POST':
        form_valid=form.is_valid();methods_valid=methods.is_valid()
        if form_valid and methods_valid:
            try:shop_setup.configure(branch,form.mapping(),payment_mapping(methods),request.user)
            except (ValidationError,IntegrityError) as error:form.add_error(None,error_text(error))
            else:
                messages.success(request,'Posting accounts and payment methods saved.');return redirect('setup_finance',pk=branch.pk)
    return render(request,'setup_finance.html',dict(title='Posting accounts & payments',branch=branch,form=form,methods=methods,
        locked=LedgerEntry.objects.filter(branch=branch).exists(),has_shift=Document.objects.filter(branch=branch,kind='POS Opening Entry',state='Open',status='Submitted').exists()))

@login_required
def receipt(request,pk):
    doc=document(request.user,pk)
    if doc.kind not in services.SELL or doc.status!='Submitted':raise Http404
    payments=[dict(mode=p.data.get('mode',''),amount=p.total,direction=p.data.get('direction','Receive'),reference=p.data.get('reference',''),handler=p.staff_names.get('handler',''),cash_tendered=p.data.get('cash_tendered')) for p in doc.linked.filter(kind='Payment Entry',status='Submitted')]
    imported=bool(doc.source and (doc.source.get('is_pos') or doc.kind=='POS Invoice'))
    if imported:
        for row in doc.source.get('payments',[]):
            amount=services.money(row.get('base_amount',row.get('amount')))
            if amount:payments.append(dict(mode=row.get('mode_of_payment',''),amount=abs(amount),direction='Pay' if doc.is_return else 'Receive',reference=row.get('reference_no') or '',handler=doc.staff_names.get('handler') or doc.source.get('covison_collector_name','')))
    change=(abs(services.money(doc.source.get('base_change_amount'))) if imported else services.ZERO)+services.cash_summary(doc)['change_returned']
    lines=[]
    for line in doc.lines.select_related('product','device'):
        raw=line.snapshot.get('battery')
        line.receipt_battery=f'{raw}%' if type(raw) in (int,float,Decimal) else str(raw or 'not checked')
        line.receipt_amount=(-services.money(line.snapshot.get('return_base',line.snapshot.get('return_amount'))) if doc.is_return and 'return_amount' in line.snapshot else services.money(line.quantity*line.rate)*(-1 if doc.is_return else 1));lines.append(line)
    return render(request,'receipt.html',dict(doc=doc,lines=lines,shop=doc.data.get('receipt',{}),payments=payments,change=change,
        credit_applications=doc.data.get('credit_applications' if doc.is_return else 'return_credit_applications',[]),
        receipt_format='a4' if request.GET.get('paper')=='a4' else '58' if request.GET.get('paper')=='58' else 'thermal'))

@login_required
def pos(request):
    places=access.branches(request.user)
    branch=selected_branch(request.user,request.GET.get('branch'))
    if not branch:return redirect('collection',kind='Shop')
    access.require(request.user,branch,'can_sell')
    configuration=ShopSettings.objects.filter(branch=branch).first()
    if not configuration or not configuration.accounts or not configuration.payment_methods:
        return render(request,'empty.html',{'title':'Point of Sale setup','text':'This shop needs its payment and posting accounts configured by an administrator before POS can start.'})
    items=[]
    for d in scoped_devices(request.user).filter(warehouse__branch=branch,status='Available').select_related('product','warehouse').prefetch_related('imeis'):
        items.append(dict(product=str(d.product_id),device=str(d.pk),warehouse=str(d.warehouse_id),code=d.code,
            name=d.product.name,rate=str(d.asking_price),stock=1,pta_status=d.pta_status,barcode=d.code,barcodes=[d.code],warehouse_name=d.warehouse.name,serialized=True))
    for p in Product.objects.filter(enabled=True,serialized=False):
        for warehouse in Warehouse.objects.filter(branch=branch,enabled=True):
            qty,_=services.on_hand(p,warehouse)
            if qty>0 or not p.stock_item:items.append(dict(product=str(p.pk),device='',warehouse=str(warehouse.pk),code=p.code,name=p.name,rate=str(p.selling_price),stock=str(qty),stock_item=p.stock_item,warehouse_name=warehouse.name,barcode=' '.join(p.barcodes),barcodes=p.barcodes,serialized=False))
    shift=services.active_shift(branch,request.user)
    return render(request,'pos.html',dict(title='Point of Sale',items=items,customers=Party.objects.filter(kind='Customer',enabled=True),
        sellers=Staff.objects.filter(branch=branch,enabled=True,can_sell=True),handlers=Staff.objects.filter(branch=branch,enabled=True,can_handle_payments=True),cash_modes=services.cash_modes(branch),
        branch=branch,methods=configuration.payment_methods,request_key=uuid.uuid4(),tax_rules=pricing.rules(branch),
        shift=shift,reconciliation=services.till_balance(shift) if shift else None))

@login_required
@require_POST
def pos_checkout(request):
    try:
        data=json.loads(request.body);branch=get_object_or_404(access.branches(request.user),pk=data['branch'])
        access.require(request.user,branch,'can_sell')
        def perform():
            Branch.objects.select_for_update(no_key=True).get(pk=branch.pk)
            if not Document.objects.filter(branch=branch,kind='POS Opening Entry',status='Submitted',state='Open',posted_by=request.user).exists():raise ValidationError('Start your POS shift first.')
            customer=customer_names.resolve(data['customer_name'],request.user,demo_batch=branch.demo_batch) if 'customer_name' in data else Party.objects.get(pk=data['customer'],kind='Customer',enabled=True)
            seller=Staff.objects.get(pk=data['seller']);handler=Staff.objects.get(pk=data['handler']) if data.get('handler') else None
            doc=services.new_doc('POS Invoice',branch,customer=customer,seller=seller)
            adjustments=data.get('adjustments',{})
            if not isinstance(adjustments,dict):raise ValidationError('Invalid invoice adjustment values.')
            pricing.inputs(doc,adjustments.get('discount_type','amount'),adjustments.get('discount_value','0'),adjustments.get('tax_rule',''),adjustments.get('tax_version',''))
            doc.save()
            for row in data['items']:
                device=get_object_or_404(scoped_devices(request.user),pk=row['device']) if row.get('device') else None
                place=Warehouse.objects.get(pk=row['warehouse'],branch=branch)
                product=Product.objects.select_for_update().get(pk=row['product'],enabled=True)
                if device and 'pta_status' in row:
                    if row['pta_status'] not in ('PTA','Non-PTA'):raise ValidationError('Choose PTA or Non-PTA for each phone.')
                    device=Device.objects.select_for_update().get(pk=device.pk);device.pta_status=row['pta_status'];device.save(update_fields=['pta_status','modified'])
                Line.objects.create(document=doc,product=product,device=device,warehouse=place,
                    quantity=services.quantity(row['quantity']),rate=services.money(row['rate']))
            return services.checkout(doc,request.user,data.get('payments',[]),handler)
        result=services.once(uuid.UUID(data['request_key']),request.user,data,perform)
        return JsonResponse(dict(id=str(result.pk),url=reverse('detail',args=[result.pk]),receipt=reverse('receipt',args=[result.pk])))
    except (ValidationError,IntegrityError,ValueError,KeyError,TypeError,AttributeError,ObjectDoesNotExist) as error:  # ValueError covers malformed JSON
        return JsonResponse({'error':'Invalid checkout request.' if isinstance(error,(TypeError,AttributeError)) else error_text(error)},status=400)

@login_required
def evidence(request,pk):
    obj=get_object_or_404(IMEI,pk=pk,device__in=scoped_devices(request.user))
    if not obj.evidence:raise Http404
    return FileResponse(obj.evidence.open('rb'),as_attachment=True)

@login_required
def attachment(request,name):
    obj=get_object_or_404(IMEI,evidence=name,device__in=scoped_devices(request.user))
    return FileResponse(obj.evidence.open('rb'),as_attachment=True)

@require_POST
def sign_out(request):
    from django.contrib.auth import logout
    from . import supabase
    if settings.SUPABASE_AUTH_ENABLED and request.session.get('supabase'):
        try:supabase.sign_out(request.session['supabase']['access_token'])
        except (supabase.ProviderUnavailable,ValidationError):pass
    logout(request);return redirect('login')

@login_required
def change_password(request):
    from django.contrib.auth import views as auth_views
    if not settings.SUPABASE_AUTH_ENABLED:return auth_views.PasswordChangeView.as_view(template_name='password.html',success_url='/desk/mobile-shop')(request)
    from django.contrib.auth.password_validation import validate_password
    from . import supabase
    class PasswordForm(forms.Form):
        old_password=forms.CharField(widget=forms.PasswordInput)
        new_password1=forms.CharField(label='New password',widget=forms.PasswordInput)
        new_password2=forms.CharField(label='Confirm new password',widget=forms.PasswordInput)
        def clean(self):
            values=super().clean()
            if values.get('new_password1')!=values.get('new_password2'):self.add_error('new_password2','Passwords must match.')
            if values.get('new_password1'):validate_password(values['new_password1'],request.user)
            return values
    form=PasswordForm(request.POST or None)
    if request.method=='POST' and form.is_valid():
        try:
            current=request.session['supabase'];actual=supabase.user(current['access_token'])
            verified=supabase.sign_in(actual['email'],form.cleaned_data['old_password'])
            if str(request.user.supabase_identity.auth_id)!=verified['user']['id']:raise ValidationError('Account does not match.')
            supabase.sign_out(verified['access_token'])
            supabase.call('/auth/v1/user',method='PUT',payload={'password':form.cleaned_data['new_password1']},token=current['access_token'])
            supabase.tokens(request,supabase.sign_in(actual['email'],form.cleaned_data['new_password1']))
            request.session.cycle_key();messages.success(request,'Password changed.');return redirect('dashboard')
        except (ValidationError,supabase.ProviderUnavailable) as error:form.add_error(None,error_text(error))
    return render(request,'password.html',{'title':'Change password','form':form})

@login_required
def native_route(request,slug,code=None):
    kinds=['Mobile Device','Mobile Model','Item','Customer','Supplier','Mobile Staff']+KINDS
    kind=next((k for k in kinds if slugify(k)==slug),None)
    if not kind:raise Http404
    if code:
        if kind=='Mobile Device':return redirect('device',pk=get_object_or_404(scoped_devices(request.user),code=code).pk)
        if kind in KINDS:return redirect('detail',pk=get_object_or_404(scoped(request.user),code=code).pk)
    return redirect('collection',kind=kind)
