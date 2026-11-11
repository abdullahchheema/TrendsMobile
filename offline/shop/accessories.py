"""Accessories: covers, protectors, chargers, adapters, cables, headphones, AirPods and the like.

They are counted by quantity (no IMEI). Stock comes in through a normal purchase receipt and supplier bill, so the books, the supplier's
balance and the payment work exactly as for phones. Every item carries a selling price; the POS shows it and the cashier can still change it."""
import json
import uuid
from decimal import Decimal
from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError,transaction
from django.db.models import Sum
from django.shortcuts import get_object_or_404,redirect,render
from django.urls import reverse
from .models import Branch,Line,Product,StockMovement
from . import access,customer_names,easy_retail,shopdefaults,services as s
from .bulk import BulkForm

CATEGORIES=('Cover / Case','Screen Protector','Charger','Adapter','Cable','Handsfree / Headphones','AirPods / Earbuds','Power Bank','Smart Watch','Other')
MAX_ROWS=100


def stock_levels(branch):
    """{product id: (quantity, value)} in the shop's stock place, in one query."""
    warehouse=shopdefaults.warehouse(branch)
    rows=StockMovement.objects.filter(warehouse=warehouse,product__serialized=False).values('product_id').annotate(q=Sum('quantity'),v=Sum('value'))
    return {r['product_id']:(r['q'] or Decimal(0),r['v'] or Decimal(0)) for r in rows}


def parse_rows(raw):
    try:rows=json.loads(raw or '[]')
    except (TypeError,ValueError):raise ValidationError('The item list could not be read. Reload the page and try again.')
    if not isinstance(rows,list) or not rows:raise ValidationError('Add at least one item.')
    if len(rows)>MAX_ROWS:raise ValidationError(f'A purchase can have up to {MAX_ROWS} items.')
    clean=[]
    for position,row in enumerate(rows,1):
        if not isinstance(row,dict):raise ValidationError(f'Item {position} is not valid.')
        name=' '.join(str(row.get('name') or '').split())
        if not name:raise ValidationError(f'Item {position}: enter the item name.')
        if len(name)>180:raise ValidationError(f'Item {position}: the name is too long.')
        category=row.get('category') if row.get('category') in CATEGORIES else 'Other'
        try:qty=int(str(row.get('quantity') or '').strip())
        except ValueError:raise ValidationError(f'Item {position} ({name}): enter how many, as a whole number.')
        if not 1<=qty<=10000:raise ValidationError(f'Item {position} ({name}): the quantity must be between 1 and 10,000.')
        try:cost=s.payment_money(row.get('cost'));price=s.payment_money(row.get('price'))
        except ValidationError:raise ValidationError(f'Item {position} ({name}): enter the prices as amounts with at most two decimals.')
        if cost<=0:raise ValidationError(f'Item {position} ({name}): enter what each costs you (more than zero).')
        if price<=0:raise ValidationError(f'Item {position} ({name}): enter the selling price (more than zero).')
        clean.append(dict(name=name,category=category,qty=qty,cost=cost,price=price,brand=' '.join(str(row.get('brand') or '').split())[:100]))
    return clean


def find_or_create(row):
    """The accessory with this name (any letter case), created if it is new; its selling price follows the latest purchase."""
    product=Product.objects.filter(serialized=False,stock_item=True,name__iexact=row['name']).order_by('created').first()
    if product:
        changed=[]
        if product.selling_price!=row['price']:product.selling_price=row['price'];changed.append('selling_price')
        if not product.enabled:product.enabled=True;changed.append('enabled')
        if changed:product.save(update_fields=changed+['modified'])
        return product
    return Product.objects.create(code='ACC-'+uuid.uuid4().hex[:10].upper(),name=row['name'],group=row['category'],brand=row['brand'],serialized=False,stock_item=True,selling_price=row['price'])


@transaction.atomic
def post(data,rows,user):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk);access.require(user,branch,'can_buy')
    if data['warehouse'].branch_id!=branch.pk or not data['warehouse'].enabled:raise ValidationError('Choose this shop’s active stock location.')
    supplier=customer_names.resolve(data['party_name'],user,kind='Supplier',demo_batch=branch.demo_batch,branch=branch)
    receipt=s.new_doc('Purchase Receipt',branch,supplier=supplier,warehouse=data['warehouse'],buyer=data['staff'],posting_date=data['posting_date'],demo_batch=branch.demo_batch,notes=data.get('notes',''))
    lines=[];total_qty=0
    for row in rows:
        product=find_or_create(row)
        lines.append(Line(document=receipt,product=product,warehouse=data['warehouse'],quantity=row['qty'],rate=row['cost']));total_qty+=row['qty']
    Line.objects.bulk_create(lines)
    receipt=s.submit(receipt,user);bill=s.supplier_bill(receipt,user);bill.posting_date=data['posting_date']
    bill.data['easy_entry']={'kind':'purchase','quantity':total_qty,'accessories':True,'lines':len(rows)};bill.save();bill=s.submit(bill,user)
    paid=easy_retail.paid_amount(data,bill.total)
    if paid:
        entry=s.new_doc('Payment Entry',branch,supplier=supplier,reference=bill,handler=data['handler'],posting_date=bill.posting_date,demo_batch=branch.demo_batch,
            data=dict(direction='Pay',amount=str(paid),mode=data['method'],reference=data.get('payment_reference','')))
        s.submit(entry,user)
    s.audit(user,'Accessory purchase',bill,items=len(rows),units=total_qty,total=str(bill.total))
    bill.refresh_from_db();return bill


class AccessoryBuyForm(BulkForm):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for name in ('price_mode','bulk_total','later'):self.fields.pop(name,None)
    @property
    def visible_fields_tail(self):return [self[n] for n in ('payment_status','amount_paid','method','notes') if n in self.fields]
    def clean(self):
        data=forms.Form.clean(self)
        branch=data.get('branch')
        if branch:
            me=shopdefaults.person(self.user,branch)
            for name,default in (('warehouse',shopdefaults.warehouse(branch)),('staff',me),('handler',me)):
                if not data.get(name) or data[name].branch_id!=branch.pk:data[name]=default
        if data.get('payment_status') in ('full','part') and not data.get('method'):self.add_error('method','Choose a payment method.')
        return data


class _Invalid(Exception):pass


@login_required
def accessories(request):
    """Accessory stock of one shop: what is in stock, what each sells for, and what it cost."""
    from .views import selected_branch,ask_which_shop
    gate=ask_which_shop(request,'Accessories','Choose the shop whose accessories you want to see.')
    if gate:return gate
    branch=selected_branch(request.user,request.GET.get('branch'))
    if not branch:return redirect('setup')
    access.require(request.user,branch)
    see_cost=access.costs(request.user);levels=stock_levels(branch)
    query=request.GET.get('search','').strip().lower()
    rows=[]
    for p in Product.objects.filter(serialized=False,stock_item=True).order_by('group','name'):
        quantity,value=levels.get(p.pk,(Decimal(0),Decimal(0)))
        if not p.enabled and not quantity:continue
        if query and query not in f'{p.name} {p.group} {p.brand}'.lower():continue
        rows.append(dict(product=p,quantity=quantity,average=(value/quantity).quantize(Decimal('0.01')) if see_cost and quantity>0 else None,low=bool(p.reorder_level and quantity<=p.reorder_level)))
    can_buy=access.manager(request.user) or (access.staff(request.user) is None or access.staff(request.user).can_buy)
    return render(request,'accessories.html',dict(title='Accessories',branch=branch,rows=rows,see_cost=see_cost,can_buy=can_buy,search=request.GET.get('search','')))


@login_required
def accessory_buy(request):
    from .views import selected_branch,error_text,ask_which_shop
    gate=ask_which_shop(request,'Buy accessories','Choose the shop that will receive these accessories.')
    if gate:return gate
    branch=selected_branch(request.user,request.POST.get('branch') if request.method=='POST' else request.GET.get('branch'))
    if not branch:return redirect('setup')
    access.require(request.user,branch,'can_buy')
    form=AccessoryBuyForm(request.POST or None,user=request.user,branch=branch)
    if request.method=='POST':
        try:
            key=uuid.UUID(request.POST.get('request_key',''))
            def perform():
                if not form.is_valid():raise _Invalid()
                return post(form.cleaned_data,parse_rows(form.cleaned_data.get('rows_json')),request.user)
            payload={k:request.POST.getlist(k) for k in request.POST if k not in ('csrfmiddlewaretoken','request_key')}
            bill=s.once(key,request.user,{'accessory_purchase':payload},perform)
            messages.success(request,f'Purchase saved: {bill.data["easy_entry"]["quantity"]} items, PKR {bill.total:,.2f}. Stock, supplier bill and payment are updated.')
            return redirect('easy_done',pk=bill.pk)
        except _Invalid:pass
        except (ValidationError,IntegrityError,ValueError) as error:form.add_error(None,error_text(error))
    names=[dict(name=p.name,category=p.group,price=str(p.selling_price)) for p in Product.objects.filter(serialized=False,stock_item=True,enabled=True).order_by('name')]
    from . import money
    balances={r['method']:str(r['amount']) for r in money.balances([branch])}
    return render(request,'accessory_buy.html',dict(title='Buy accessories',balances=balances,form=form,branch=branch,names=names,categories=CATEGORIES))


@login_required
def accessory_edit(request,pk):
    """Change an accessory's name, category, selling price, low-stock level, or retire it."""
    product=get_object_or_404(Product,pk=pk,serialized=False,stock_item=True)
    from .views import selected_branch
    branch=selected_branch(request.user,request.GET.get('branch'))
    access.require(request.user,branch,'can_buy')
    class Form(forms.Form):
        name=forms.CharField(max_length=180)
        group=forms.ChoiceField(label='Category',choices=[(c,c) for c in CATEGORIES])
        selling_price=forms.DecimalField(label='Selling price (PKR)',min_value=Decimal('0.01'),max_digits=15,decimal_places=2)
        reorder_level=forms.DecimalField(label='Warn me when stock is this low (0 = no warning)',min_value=0,max_digits=12,decimal_places=3,required=False)
        enabled=forms.BooleanField(label='Sell this item',required=False)
    form=Form(request.POST or None,initial=dict(name=product.name,group=product.group if product.group in CATEGORIES else 'Other',selling_price=product.selling_price,reorder_level=product.reorder_level,enabled=product.enabled))
    if request.method=='POST' and form.is_valid():
        d=form.cleaned_data
        if Product.objects.filter(serialized=False,stock_item=True,name__iexact=d['name']).exclude(pk=product.pk).exists():form.add_error('name','Another accessory already has this name.')
        else:
            product.name=d['name'];product.group=d['group'];product.selling_price=d['selling_price'];product.reorder_level=d['reorder_level'] or 0;product.enabled=d['enabled'];product.save()
            messages.success(request,'Accessory saved.');return redirect(reverse('accessories')+'?branch='+str(branch.pk))
    return render(request,'form.html',dict(title=f'Edit {product.name}',form=form,master=True))
