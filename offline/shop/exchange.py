"""One-screen phone exchange: sell a phone, take the customer's old phone(s) in part payment, settle the difference.

Behind the scenes this posts the same documents the accountant already understands: a sales invoice for the new phone, a
trade-in receipt and supplier bill for the old phone(s) (valued at the agreed price), and an exchange settlement that offsets
the two and records the cash difference in the chosen payment method. Traded-in phones go to Holding: they are owned and
valued in stock but cannot be sold until a manager inspects and accepts them."""
import uuid
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.shortcuts import redirect,render
from django.utils import timezone
from .models import Branch,Device,Line,Product,ShopSettings,Staff,Warehouse
from . import access,customer_names,easy_retail,services as s

SLOTS=3
CONDITIONS=('Excellent','Very Good','Good','Fair','For Parts')


def parse_incoming(post):
    """Read up to three trade-in rows from the form. Returns validated dicts (empty rows are ignored)."""
    rows=[];seen=set()
    for i in range(1,SLOTS+1):
        product=post.get(f'in_product_{i}','').strip();value=post.get(f'in_value_{i}','').strip();text=post.get(f'in_imeis_{i}','')
        if not product and not value and not text.strip():continue
        try:variant=Product.objects.get(pk=product,enabled=True,serialized=True,stock_item=True)
        except (Product.DoesNotExist,ValueError,ValidationError):raise ValidationError(f'Old phone {i}: choose its model and variant.')
        try:valuation=s.payment_money(value)
        except Exception:raise ValidationError(f'Old phone {i}: enter the agreed value.')
        if valuation<=0:raise ValidationError(f'Old phone {i}: the agreed value must be more than zero.')
        imeis=easy_retail.parse_imeis(text,1)
        if not imeis:raise ValidationError(f'Old phone {i}: enter its IMEI. Exchanges need the IMEI of every phone received.')
        for number in imeis[0]:
            if number in seen:raise ValidationError(f'IMEI {number} is entered twice.')
            seen.add(number)
        condition=post.get(f'in_condition_{i}','Good')
        if condition not in CONDITIONS:raise ValidationError(f'Old phone {i}: choose a condition.')
        grade=post.get(f'in_grade_{i}','').strip()
        try:grade=int(grade) if grade else None
        except ValueError:raise ValidationError(f'Old phone {i}: the grade must be a number from 1 to 10.')
        if grade is not None and not 1<=grade<=10:raise ValidationError(f'Old phone {i}: the grade must be from 1 to 10.')
        pta=post.get(f'in_pta_{i}','PTA')
        if pta not in ('PTA','Non-PTA'):raise ValidationError(f'Old phone {i}: choose PTA or Non-PTA.')
        rows.append(dict(product=variant,value=valuation,imeis=imeis[0],condition=condition,grade=grade,pta=pta,notes=post.get(f'in_notes_{i}','')[:240]))
    if not rows:raise ValidationError('Enter at least one old phone being traded in.')
    return rows


@transaction.atomic
def post(data,incoming,user):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk)
    access.require(user,branch,'can_sell')
    if not access.manager(user):raise PermissionDenied('A manager must value and approve a phone exchange.')
    phone=Device.objects.select_for_update().get(pk=data['phone'].pk,branch=branch)
    if phone.status!='Available' or not phone.warehouse_id:raise ValidationError('This phone is no longer available. Choose another phone.')
    price=s.payment_money(data['price'])
    if price<=0:raise ValidationError('Enter the selling price of the new phone.')
    trade_value=sum((r['value'] for r in incoming),Decimal('0.00'))
    customer=customer_names.resolve(data['party_name'],user,demo_batch=branch.demo_batch,branch=branch)
    trader=customer_names.resolve(data['party_name'],user,kind='Supplier',demo_batch=branch.demo_batch,branch=branch)
    if data.get('pta_status') in ('PTA','Non-PTA'):phone.pta_status=data['pta_status'];phone.save(update_fields=['pta_status','modified'])
    # 1. the sale, left open so the settlement can offset the trade-in value against it
    sale=s.new_doc('Sales Invoice',branch,customer=customer,seller=data['staff'],warehouse=phone.warehouse,posting_date=data['posting_date'],demo_batch=branch.demo_batch,notes=data.get('notes',''),data={'exchange':True})
    Line.objects.create(document=sale,product=phone.product,device=phone,warehouse=phone.warehouse,quantity=1,rate=price)
    sale=s.checkout(sale,user,[],data['handler'])
    # 2. the old phone(s), received into Holding at the agreed value
    receipt=s.new_doc('Purchase Receipt',branch,supplier=trader,warehouse=data['warehouse'],buyer=data['buyer'],posting_date=data['posting_date'],demo_batch=branch.demo_batch,
        notes='Trade-in from '+customer.name,data={'trade_in_customer':str(customer.pk),'purchase_source':'Customer Trade-In','device':dict(device_type='Used',holding=True,inspection_status='Pending',line_attrs=[],line_imeis=[])})
    rows=sorted(incoming,key=lambda r:str(r['product'].pk))      # receipt lines are processed grouped by product
    for r in rows:
        Line.objects.create(document=receipt,product=r['product'],warehouse=data['warehouse'],quantity=1,rate=r['value'])
        receipt.data['device']['line_attrs'].append(dict(condition=r['condition'],grade=r['grade'],pta_status=r['pta'],asking_price='0',holding=True,inspection_status='Pending',details=dict(trade_in_notes=r['notes'],traded_for=sale.code)))
        receipt.data['device']['line_imeis'].append(r['imeis'])
    receipt.save();receipt=s.submit(receipt,user)
    bill=s.supplier_bill(receipt,user);bill.posting_date=data['posting_date'];bill.save();bill=s.submit(bill,user)
    # 3. the exchange links them and settles: offsets receivable against payable and pays/receives the difference
    exchange=s.new_doc('Mobile Exchange',branch,customer=customer,handler=data['handler'],posting_date=data['posting_date'],demo_batch=branch.demo_batch,
        data=dict(sales_invoice=str(sale.pk),purchase_invoice=str(bill.pk),mode=data['method'],reference=data.get('payment_reference',''),trade_in_value=str(trade_value),difference=str(price-trade_value)))
    exchange=s.settle_exchange(exchange,user)
    s.audit(user,'Phone exchange',exchange,sale=sale.code,trade_in=bill.code,new_phone_price=str(price),trade_in_value=str(trade_value),difference=str(price-trade_value))
    return exchange


class ExchangeForm(forms.Form):
    def __init__(self,*args,user,branch,**kwargs):
        super().__init__(*args,**kwargs)
        config=ShopSettings.objects.filter(branch=branch).first();people=Staff.objects.filter(branch=branch,enabled=True)
        self.fields['request_key']=forms.UUIDField(widget=forms.HiddenInput(),initial=uuid.uuid4)
        self.fields['branch']=forms.ModelChoiceField(queryset=access.branches(user),initial=branch,widget=forms.HiddenInput())
        self.fields['party_name']=forms.CharField(label='Customer name',max_length=180,widget=forms.TextInput(attrs={'autocomplete':'off','placeholder':'Type full name'}))
        phones=Device.objects.filter(branch=branch,status='Available',warehouse__isnull=False,warehouse__enabled=True,product__enabled=True).select_related('product').prefetch_related('imeis')
        self.fields['phone']=forms.ModelChoiceField(label='New phone the customer is buying',queryset=phones,empty_label='Choose a phone from stock')
        self.fields['phone'].label_from_instance=lambda d:f'{d.product.name} · '+(('IMEI '+' / '.join(i.number for i in d.imeis.all())) if d.imeis.all() else d.code)+f' · asking PKR {d.asking_price:,.0f}'
        self.fields['pta_status']=forms.ChoiceField(label='PTA status of the new phone',choices=[('PTA','PTA'),('Non-PTA','Non-PTA')])
        self.fields['price']=forms.DecimalField(label='Selling price of the new phone (PKR)',min_value=Decimal('0.01'),max_digits=15,decimal_places=2)
        self.fields['posting_date']=forms.DateField(label='Date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        sellers=people.filter(can_sell=True);buyers=people.filter(can_buy=True);handlers=people.filter(can_handle_payments=True)
        from . import shopdefaults
        me=shopdefaults.person(user,branch);self.user=user
        for name,qs in (('staff',sellers),('buyer',buyers),('handler',handlers)):
            self.fields[name]=forms.ModelChoiceField(queryset=qs,initial=me.pk,required=False,widget=forms.HiddenInput())
        self.fields['warehouse']=forms.ModelChoiceField(queryset=Warehouse.objects.filter(branch=branch,enabled=True),initial=shopdefaults.warehouse(branch).pk,required=False,widget=forms.HiddenInput())
        self.fields['method']=forms.ChoiceField(label='Difference paid / received by',choices=[(m,m) for m in (config.payment_methods if config else [])],initial='Cash')
        self.fields['payment_reference']=forms.CharField(label='Bank / wallet reference (if not cash)',required=False,max_length=180)
        self.fields['notes']=forms.CharField(label='Note (optional)',required=False,widget=forms.Textarea(attrs={'rows':2}))

    @property
    def visible_fields_new(self):return [self[n] for n in ('party_name','phone','pta_status','price','posting_date','staff','buyer','handler','warehouse')]
    @property
    def visible_fields_settle(self):return [self[n] for n in ('method','payment_reference','notes')]

    def clean(self):
        data=super().clean()
        if data.get('branch'):
            from . import shopdefaults
            me=shopdefaults.person(self.user,data['branch'])
            for name in ('staff','buyer','handler'):
                if not data.get(name) or data[name].branch_id!=data['branch'].pk:data[name]=me
            if not data.get('warehouse') or data['warehouse'].branch_id!=data['branch'].pk:data['warehouse']=shopdefaults.warehouse(data['branch'])
        if data.get('method') and data['method'] not in s.cash_modes(data['branch']) and not data.get('payment_reference'):self.add_error('payment_reference','Enter the bank or wallet transaction reference.')
        return data


@login_required
def exchange(request):
    from .views import selected_branch,error_text
    branch=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:return redirect('setup')
    access.require(request.user,branch,'can_sell')
    if not access.manager(request.user):raise PermissionDenied('A manager must value and approve a phone exchange.')
    form=ExchangeForm(request.POST or None,user=request.user,branch=branch)
    incoming_error=None;posted=request.POST
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():
                if not form.is_valid():raise _Invalid()
                return post(form.cleaned_data,parse_incoming(request.POST),request.user)
            payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
            doc=s.once(key,request.user,{'exchange':payload},perform)
            diff=Decimal(doc.data.get('difference','0'))
            messages.success(request,f'Exchange {doc.code} done. '+(f'Customer paid PKR {diff:,.2f}.' if diff>0 else f'Customer received PKR {-diff:,.2f}.' if diff<0 else 'Even swap, no money changed hands.')+' Old phones are in Holding until inspected.')
            return redirect('detail',pk=doc.pk)
        except _Invalid:
            try:parse_incoming(request.POST)
            except ValidationError as error:incoming_error=error_text(error)
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    products=Product.objects.filter(enabled=True,serialized=True,stock_item=True).select_related('model').order_by('name')
    slots=[dict(n=i,product=posted.get(f'in_product_{i}',''),value=posted.get(f'in_value_{i}',''),imeis=posted.get(f'in_imeis_{i}',''),condition=posted.get(f'in_condition_{i}','Good'),grade=posted.get(f'in_grade_{i}',''),pta=posted.get(f'in_pta_{i}','PTA'),notes=posted.get(f'in_notes_{i}','')) for i in range(1,SLOTS+1)]
    return render(request,'exchange.html',dict(title='Phone exchange',form=form,branch=branch,products=products,slots=slots,conditions=CONDITIONS,incoming_error=incoming_error))


class _Invalid(Exception):pass
