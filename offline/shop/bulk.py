"""Bulk purchase: many phone models and variants from one supplier in a single saved transaction.

Each row is a phone variant with a quantity, condition, grade, cost and the scanned IMEIs. Everything posts atomically as one
receipt, one supplier bill and (optionally) one payment, reusing the same services as the quick Buy Stock screen."""
import json
import uuid
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,transaction
from django.shortcuts import redirect,render
from django.utils import timezone
from .models import Branch,Device,IMEI,Product,Staff,ShopSettings,Warehouse
from . import access,catalog_entry,easy_retail,customer_names,shopdefaults,services as s

MAX_ROWS=200
MAX_PHONES=200
CONDITIONS=('Excellent','Very Good','Good','Fair','For Parts')
TYPES=('Used','New','Refurbished','Repaired')


def split_cost(total,quantity):
    """Share a row's total cost across its phones in whole paisa, largest remainder first, so the parts add up exactly."""
    cents=int(total*100);base,extra=divmod(cents,quantity)
    return [Decimal(base+(1 if i<extra else 0))/100 for i in range(quantity)]


def parse_rows(raw,require_imei=True,bulk_total=None):
    """Validate the JSON rows. Returns clean rows sorted the way the receipt will process them (by product), with per-phone lists.
    With `bulk_total` the whole purchase has one price: it is shared equally over every phone (to the paisa) and rows need no price of their own."""
    try:rows=json.loads(raw or '[]')
    except (TypeError,ValueError):raise ValidationError('The phone list could not be read. Reload the page and try again.')
    if not isinstance(rows,list) or not rows:raise ValidationError('Add at least one phone to the purchase.')
    if len(rows)>MAX_ROWS:raise ValidationError(f'A purchase can have up to {MAX_ROWS} phones.')
    clean=[];seen={}
    for position,row in enumerate(rows,1):
        if not isinstance(row,dict):raise ValidationError(f'Line {position} is not valid.')
        variant=None
        if row.get('product'):
            try:product=Product.objects.get(pk=row.get('product'),enabled=True,serialized=True,stock_item=True)
            except (Product.DoesNotExist,ValueError,ValidationError):raise ValidationError(f'Line {position}: choose a phone model and variant.')
            label=product.name
        else:       # typed details: the catalogue records are found or created when the purchase is saved
            product=None
            try:variant=catalog_entry.check(row)
            except ValidationError as error:raise ValidationError(f'Line {position}: {error.messages[0]}')
            label=f"{variant['brand']} {variant['model']}"
        try:qty=int(row.get('quantity'))
        except (TypeError,ValueError):raise ValidationError(f'Line {position}: enter how many phones.')
        if not 1<=qty<=100:raise ValidationError(f'Line {position}: quantity must be between 1 and 100.')
        if bulk_total is not None:costs=[]
        else:
            try:cost=s.payment_money(row.get('cost'))
            except Exception:raise ValidationError(f'Line {position}: enter the buying price.')
            if cost<=0:raise ValidationError(f'Line {position}: the buying price must be more than zero.')
            mode=row.get('cost_mode','each')
            if mode not in ('each','total'):raise ValidationError(f'Line {position}: choose whether the price is each or the line total.')
            costs=split_cost(cost,qty) if mode=='total' else [cost]*qty
        condition=row.get('condition','Good');kind=row.get('device_type','Used')
        if condition not in CONDITIONS or kind not in TYPES:raise ValidationError(f'Line {position}: choose a valid condition and phone type.')
        grade=row.get('grade')
        try:grade=int(grade) if grade not in (None,'') else None
        except (TypeError,ValueError):raise ValidationError(f'Line {position}: the grade must be a whole number from 1 to 10.')
        if grade is not None and not 1<=grade<=10:raise ValidationError(f'Line {position}: the grade must be from 1 to 10.')
        if row.get('pta_status') not in ('PTA','Non-PTA'):raise ValidationError(f'Line {position}: choose PTA or Non-PTA.')
        try:asking=s.payment_money(row.get('asking_price') or 0)       # the price the phone is listed at; the cashier can still change it at the sale
        except ValidationError:raise ValidationError(f'Line {position}: enter the selling price as an amount.')
        if asking<=0:raise ValidationError(f'Line {position}: enter the selling price (more than zero).')
        imeis=easy_retail.parse_imeis(row.get('imeis',''),qty) if (row.get('imeis') or '').strip() else []
        if require_imei and not imeis:raise ValidationError(f'Line {position} ({label}): scan or type {qty} IMEI(s), or tick “I will add IMEIs later”.')
        for index,pair in enumerate(imeis,1):
            for number in pair:
                if number in seen:raise ValidationError(f'IMEI {number} is used twice (line {seen[number]} and line {position}).')
                seen[number]=position
        clean.append(dict(product=product,variant=variant,notes=catalog_entry.tidy(row.get('notes')),qty=qty,costs=costs,condition=condition,device_type=kind,grade=grade,pta_status=row['pta_status'],asking=asking,imeis=imeis))
    if bulk_total is not None:
        shares=iter(split_cost(bulk_total,sum(r['qty'] for r in clean)))
        for r in clean:r['costs']=[next(shares) for _ in range(r['qty'])]
    return sorted(clean,key=lambda r:str(r['product'].pk) if r['product'] else '')        # the receipt processes lines grouped by product; keep our per-phone lists in that order


@transaction.atomic
def post(data,rows,user):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk);access.require(user,branch,'can_buy')
    if data['warehouse'].branch_id!=branch.pk or not data['warehouse'].enabled:raise ValidationError('Choose this shop’s active stock location.')
    if sum(r['qty'] for r in rows)>MAX_PHONES:raise ValidationError(f'A single purchase can hold up to {MAX_PHONES} phones.')
    supplier=customer_names.resolve(data['party_name'],user,kind='Supplier',demo_batch=branch.demo_batch,branch=branch)
    found={}
    for r in rows:
        if not r['product']:
            key=tuple(str(r['variant'][k]).casefold() for k in ('brand','model','storage','colour'))
            if key not in found:found[key]=catalog_entry.resolve_variant(r['variant'])
            r['product']=found[key]
    rows=sorted(rows,key=lambda r:str(r['product'].pk))     # receipt lines are processed grouped by product
    line_attrs=[];line_imeis=[];has_imeis=all(r['imeis'] for r in rows)
    receipt=s.new_doc('Purchase Receipt',branch,supplier=supplier,warehouse=data['warehouse'],buyer=data['staff'],posting_date=data['posting_date'],demo_batch=branch.demo_batch,notes=data.get('notes',''))
    from .models import Line
    locked=set();new_lines=[]
    for r in rows:
        if r['product'].pk not in locked:Product.objects.select_for_update().get(pk=r['product'].pk);locked.add(r['product'].pk)
        for i in range(r['qty']):
            new_lines.append(Line(document=receipt,product=r['product'],warehouse=data['warehouse'],quantity=1,rate=r['costs'][i]))
            line_attrs.append(dict(condition=r['condition'],device_type=r['device_type'],grade=r['grade'],pta_status=r['pta_status'],asking_price=str(r['asking']),**({'details':dict(condition_notes=r['notes'])} if r['notes'] else {})))
            line_imeis.append(r['imeis'][i] if r['imeis'] else [])
    Line.objects.bulk_create(new_lines)
    receipt.data={'device':dict(inspection_status='Passed',inspection_date=str(data['posting_date']),line_attrs=line_attrs,line_imeis=line_imeis if has_imeis else [])};receipt.save()
    receipt=s.submit(receipt,user);bill=s.supplier_bill(receipt,user);bill.posting_date=data['posting_date']
    bill.data['easy_entry']={'kind':'purchase','quantity':len(line_attrs),'bulk':True,'lines':len(rows)};bill.save();bill=s.submit(bill,user)
    paid=easy_retail.paid_amount(data,bill.total)
    if paid:
        entry=s.new_doc('Payment Entry',branch,supplier=supplier,reference=bill,handler=data['handler'],posting_date=bill.posting_date,demo_batch=branch.demo_batch,
            data=dict(direction='Pay',amount=str(paid),mode=data['method'],reference=data.get('payment_reference','')))
        s.submit(entry,user)
    s.audit(user,'Bulk purchase',bill,phones=len(line_attrs),lines=len(rows),total=str(bill.total))
    bill.refresh_from_db();return bill


class BulkForm(forms.Form):
    def __init__(self,*args,user,branch,**kwargs):
        super().__init__(*args,**kwargs)
        config=ShopSettings.objects.filter(branch=branch).first();people=Staff.objects.filter(branch=branch,enabled=True)
        self.fields['request_key']=forms.UUIDField(widget=forms.HiddenInput(),initial=uuid.uuid4)
        self.fields['branch']=forms.ModelChoiceField(queryset=access.branches(user),initial=branch,widget=forms.HiddenInput())
        self.fields['party_name']=forms.CharField(label='Supplier',max_length=180,widget=forms.TextInput(attrs={'placeholder':'Type supplier name','autocomplete':'off'}))
        places=Warehouse.objects.filter(branch=branch,enabled=True);person=access.staff(user)
        self.fields['warehouse']=forms.ModelChoiceField(queryset=places,initial=shopdefaults.warehouse(branch).pk,required=False,widget=forms.HiddenInput())
        self.fields['posting_date']=forms.DateField(label='Purchase date',initial=timezone.localdate,widget=forms.DateInput(format='%Y-%m-%d',attrs={'type':'date'}))
        buyers=people.filter(can_buy=True)
        me=shopdefaults.person(user,branch)
        if buyers.filter(crew=True).exists():self.fields['staff']=forms.ModelChoiceField(label='Bought by',queryset=buyers,required=True,empty_label='Choose who is buying…')       # a shop with staff always records who bought
        else:self.fields['staff']=forms.ModelChoiceField(queryset=buyers,initial=me.pk,required=False,widget=forms.HiddenInput())
        can_pay=person is None or person.manager or person.can_handle_payments
        choices=[('full','Paid in full'),('part','Part payment'),('unpaid','Pay later / on credit')] if can_pay else [('unpaid','Pay later')]
        self.fields['payment_status']=forms.ChoiceField(label='Payment',choices=choices,initial='unpaid' if not can_pay else 'full')
        self.fields['amount_paid']=forms.DecimalField(label='Amount paid now (PKR)',min_value=0,max_digits=15,decimal_places=2,required=False)
        self.fields['method']=forms.ChoiceField(label='Paid from',choices=[(m,m) for m in (config.payment_methods if config else [])],initial='Cash',required=False)
        self.fields['payment_reference']=forms.CharField(label='Bank / wallet reference',required=False,max_length=180)
        handlers=people.filter(can_handle_payments=True)
        self.fields['handler']=forms.ModelChoiceField(queryset=handlers,required=False,initial=me.pk,widget=forms.HiddenInput())
        self.user=user
        self.fields['notes']=forms.CharField(label='Note (optional)',required=False,widget=forms.Textarea(attrs={'rows':2}))
        self.fields['price_mode']=forms.ChoiceField(label='How is the price given?',choices=[('each','Each phone has its own price'),('bulk','One price for all the phones')],initial='each',required=False,widget=forms.RadioSelect)
        self.fields['bulk_total']=forms.DecimalField(label='Total price for all phones (PKR)',min_value=0,max_digits=15,decimal_places=2,required=False)
        self.fields['later']=forms.BooleanField(label='I will add IMEIs later (not recommended)',required=False)
        self.fields['rows_json']=forms.CharField(widget=forms.HiddenInput(),required=False)

    @property
    def visible_fields_head(self):return [self[n] for n in ('party_name','posting_date','warehouse','staff')]
    @property
    def visible_fields_tail(self):return [self[n] for n in ('payment_status','amount_paid','method','later','notes','handler')]

    def clean(self):
        data=super().clean()
        branch=data.get('branch')
        if branch:
            me=shopdefaults.person(self.user,branch)
            for name,default in (('warehouse',shopdefaults.warehouse(branch)),('staff',me),('handler',me)):
                if not data.get(name) or data[name].branch_id!=branch.pk:data[name]=default
        if data.get('price_mode')=='bulk' and not (data.get('bulk_total') or 0)>0 and 'bulk_total' not in self.errors:self.add_error('bulk_total','Enter the total price for all the phones.')
        if data.get('payment_status') in ('full','part'):
            if not data.get('method'):self.add_error('method','Choose a payment method.')
        return data


@login_required
def bulk_purchase(request):
    from .views import selected_branch,error_text,ask_which_shop
    gate=ask_which_shop(request,'Bulk purchase','Choose the shop that will receive these phones.')
    if gate:return gate
    branch=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:return redirect('setup')
    access.require(request.user,branch,'can_buy')
    form=BulkForm(request.POST or None,user=request.user,branch=branch)
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():
                if not form.is_valid():raise _Invalid()
                bulk=form.cleaned_data.get('bulk_total') if form.cleaned_data.get('price_mode')=='bulk' else None
                rows=parse_rows(form.cleaned_data.get('rows_json'),require_imei=not form.cleaned_data.get('later'),bulk_total=bulk)
                return post(form.cleaned_data,rows,request.user)
            payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
            bill=s.once(key,request.user,{'bulk_purchase':payload},perform)
            messages.success(request,f'Purchase saved: {bill.data["easy_entry"]["quantity"]} phones, PKR {bill.total:,.2f}. Stock, supplier bill and payment are updated.')
            return redirect('easy_done',pk=bill.pk)
        except _Invalid:pass
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    products=[dict(id=str(p.pk),name=p.name,model=p.model.name if p.model_id else '',brand=p.model.brand if p.model_id else '') for p in Product.objects.filter(enabled=True,serialized=True,stock_item=True).select_related('model').order_by('name')]
    from . import money
    balances={r['method']:str(r['amount']) for r in money.balances([branch])}
    return render(request,'bulk_purchase.html',dict(title='Bulk purchase',balances=balances,form=form,branch=branch,products=products,catalog=catalog_entry.catalog(),conditions=CONDITIONS,types=TYPES))


class _Invalid(Exception):pass
