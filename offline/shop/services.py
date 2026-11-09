"""Atomic retail operations. All financial amounts are PKR Decimal values."""
import hashlib
import json
import uuid
from decimal import Decimal,InvalidOperation
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q,Sum
from django.utils import timezone
from .models import *
from .access import require,manager,staff

ZERO=Decimal('0.00')
BUY={'Mobile Intake','Purchase Receipt','Purchase Invoice','Purchase Order'}
SELL={'Sales Invoice','POS Invoice'}
MANAGER={'Journal Entry','Mobile Exchange','Mobile Daily Closing','Stock Entry','Stock Adjustment'}
ADJUST_REASONS=('Damaged','Lost / stolen','Count correction','Other')

def money(value):
    try:result=Decimal(str(value or 0))
    except (InvalidOperation,TypeError):raise ValidationError('Enter a valid amount.')
    if not result.is_finite() or result.copy_abs()>Decimal('9999999999999'):raise ValidationError('Amount is outside the supported range.')
    return result.quantize(Decimal('0.01'))

def payment_money(value):
    result=money(value)
    if Decimal(str(value or 0))!=result:raise ValidationError('Payment and cash amounts must have at most two decimal places.')
    return result

def cash_modes(branch):
    conf=ShopSettings.objects.filter(branch=branch).first()
    if not conf:return []
    ids={str(a.pk) for a in Account.objects.filter(pk__in=list(conf.payment_methods.values()),account_type='Cash',enabled=True,is_group=False)}
    return [name for name,pk in conf.payment_methods.items() if str(pk) in ids]

def cash_summary(doc):
    rows=doc.linked.filter(kind='Payment Entry',status='Submitted',data__has_key='cash_tendered')
    handed=change=applied=ZERO;count=0
    for payment in rows:
        count+=1;handed+=money(payment.data['cash_tendered']);change+=money(payment.data.get('change_returned'));applied+=payment.total
    return dict(recorded=bool(count),cash_tendered=handed,change_returned=change,cash_applied=applied)

def quantity(value):
    try:result=Decimal(str(value))
    except (InvalidOperation,TypeError):raise ValidationError('Enter a valid quantity.')
    if not result.is_finite() or result<=0 or result.as_tuple().exponent < -3:raise ValidationError('Quantity must be positive with at most three decimals.')
    return result

def valid_imei(value):
    if len(str(value))!=15 or not str(value).isascii() or not str(value).isdigit():return False
    digits=[int(c) for c in value]
    return sum((n*2//10+n*2%10) if i%2 else n for i,n in enumerate(digits))%10==0

def code(prefix):return f'{prefix}-{timezone.localdate():%Y%m%d}-{uuid.uuid4().hex[:12].upper()}'

def authorize(user,doc):
    if doc.kind=='Journal Entry':
        from .setup import owner
        owner(user)
    require(user,doc.branch,'can_buy' if doc.kind in BUY else 'can_sell' if doc.kind in SELL else 'can_handle_payments' if doc.kind in ('Payment Entry','POS Opening Entry','POS Closing Entry') else None)
    if doc.kind in MANAGER and not manager(user):raise PermissionDenied('A shop manager must perform this action.')

def transfer_target(doc):
    try:target=Warehouse.objects.select_related('branch').get(pk=doc.data.get('target_warehouse'))
    except (Warehouse.DoesNotExist,ValueError,ValidationError):raise ValidationError('Select an active destination warehouse.')
    return target

def _transfer(doc,user,target):
    expected_branch=target.branch_id
    places={w.pk:w for w in Warehouse.objects.select_for_update(of=('self',)).filter(pk__in=[doc.warehouse_id,target.pk]).select_related('branch').order_by('pk')}
    source=places.get(doc.warehouse_id);target=places[target.pk]
    if target.branch_id!=expected_branch:raise ValidationError('The destination warehouse changed shops. Reload the transfer before submitting.')
    if not source or not source.enabled or source.branch_id!=doc.branch_id or not source.branch.enabled:raise ValidationError('Select an active source warehouse in this shop.')
    if not target.enabled or not target.branch.enabled or target.pk==source.pk:raise ValidationError('Select a different active destination warehouse.')
    cross=target.branch_id!=doc.branch_id
    if cross:
        if not user.is_superuser:raise PermissionDenied('Only the owner can transfer stock between shops.')
        if not doc.branch.company.strip() or doc.branch.company.strip().casefold()!=target.branch.company.strip().casefold():raise ValidationError('Transfers are limited to shops in the same business / company.')
        if not doc.notes.strip():raise ValidationError('Record the reason for this inter-shop transfer.')
        open_date(Document(branch=target.branch,posting_date=doc.posting_date))
    else:require(user,target.branch)
    if not doc.lines.exists():raise ValidationError('Add at least one stock item to transfer.')
    total=ZERO;seen=set()
    for line in doc.lines.select_related('product').order_by('product_id','device_id','pk'):
        product=Product.objects.select_for_update().get(pk=line.product_id)
        if not product.enabled or not product.stock_item:raise ValidationError('Transfers require active stock items.')
        if line.warehouse_id and line.warehouse_id!=source.pk:raise ValidationError('Every transfer line must use the selected source warehouse.')
        qty=quantity(line.quantity);available,value=on_hand(product,source)
        if qty>available or available<=0 or value<0:raise ValidationError('Insufficient stock or invalid valuation to transfer.')
        cost=money(value/available)
        if product.serialized:
            if not line.device_id or qty!=1 or line.device_id in seen:raise ValidationError('Select each exact phone once, with quantity one.')
            device=Device.objects.select_for_update().get(pk=line.device_id);seen.add(device.pk)
            if device.product_id!=product.pk or device.status!='Available' or device.branch_id!=doc.branch_id or device.warehouse_id!=source.pk:raise ValidationError('Phone is unavailable in the source warehouse.')
            cost=device.purchase_cost;line.snapshot=snapshot(device)
            device.branch=target.branch;device.warehouse=target;device.save(update_fields=['branch','warehouse','modified'])
            moved=money(qty*cost)
        else:
            if line.device_id:raise ValidationError('Accessory transfers cannot reference a phone.')
            moved=money(value if qty==available else value*qty/available)
        if moved<0 or moved>value:raise ValidationError('Source valuation does not cover this stock transfer.')
        movement=stock(doc,line,-qty,cost,source)
        # Preserve the exact value (including final accessory rounding residuals).
        movement.value=-moved;movement.save(update_fields=['value'])
        StockMovement.objects.create(document=doc,posting_date=doc.posting_date,product=product,device=line.device,
            warehouse=target,quantity=qty,value=moved)
        line.cost=cost;line.rate=0;line.warehouse=source;line.snapshot.update(transfer_value=str(moved));line.save()
        total+=moved
    if cross:
        from .setup import transfer_accounts
        source_inventory,source_clearing=transfer_accounts(doc.branch)
        target_inventory,target_clearing=transfer_accounts(target.branch)
        for branch,debit_account,credit_account in [(doc.branch,source_clearing,source_inventory),(target.branch,target_inventory,target_clearing)]:
            LedgerEntry.objects.create(document=doc,branch=branch,posting_date=doc.posting_date,account=debit_account,debit=total)
            LedgerEntry.objects.create(document=doc,branch=branch,posting_date=doc.posting_date,account=credit_account,credit=total)
    doc.total=total;doc.state='Completed'
    doc.data.update(target_branch=str(target.branch_id),transfer=dict(source_shop=doc.branch.name,source_shop_code=doc.branch.code,
        source_warehouse=source.name,target_shop=target.branch.name,target_shop_code=target.branch.code,target_warehouse=target.name,
        inter_shop=cross,value=str(total),completed_at=timezone.now().isoformat(),completed_by=user.get_username()))
    person=staff(user);doc.staff_names['transferred_by']=person.name if person else user.get_username()

def audit(user,action,doc,**details):
    from .requestctx import client_ip
    branch=details.pop('branch',None) or (doc.branch if doc else None)
    Audit.objects.create(user=user,action=action,document=doc,branch=branch if isinstance(branch,Branch) else None,ip=client_ip(),details=details)

def assigned(doc,field,capability,required=True):
    person=getattr(doc,field)
    if not person and required:raise ValidationError(f'Select the responsible staff member for {field}.')
    if person and (not person.enabled or person.branch_id!=doc.branch_id or not getattr(person,capability)):
        raise ValidationError(f'Select active {field} staff enabled for this responsibility in this shop.')
    if person:doc.staff_names[field]=person.name

def settings(doc):return ShopSettings.objects.get(branch=doc.branch)

def account(doc,key):
    value=settings(doc).accounts.get(key)
    if not value:raise ValidationError(f'Configure the {key} account in Shop Settings.')
    result=Account.objects.get(pk=value)
    if result.is_group or not result.enabled:raise ValidationError('Posting account must be enabled and not a group.')
    return result

def entry(doc,key,debit=0,credit=0,party=None,reference=None):
    return LedgerEntry.objects.create(document=doc,branch=doc.branch,posting_date=doc.posting_date,
        account=key if isinstance(key,Account) else account(doc,key),debit=money(debit),credit=money(credit),party=party,reference=reference)

def balanced(doc):
    rows=doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'))
    if money(rows['d'])!=money(rows['c']):raise ValidationError('The transaction does not balance. Nothing was posted.')

def open_date(doc):
    if doc.posting_date>timezone.localdate():raise ValidationError('Cannot post a future transaction.')
    locked=Branch.objects.filter(pk=doc.branch_id).values_list('lock_through',flat=True).first()
    if locked and doc.posting_date<=locked:raise ValidationError(f'The accounting period up to {locked:%d %b %Y} is locked. Ask the owner or accountant to review it.')
    if Document.objects.filter(branch=doc.branch,kind='Mobile Daily Closing',status='Submitted',posting_date__gte=doc.posting_date).exists():
        raise ValidationError('This accounting date has already been closed. Ask a manager to review it.')

def new_doc(kind,branch,**values):
    values.setdefault('demo_batch',branch.demo_batch)
    return Document.objects.create(kind=kind,branch=branch,code=code({'Sales Invoice':'SALE','POS Invoice':'POS','Purchase Receipt':'RECEIPT','Purchase Invoice':'BILL','Payment Entry':'PAY','Journal Entry':'JOURNAL','Stock Adjustment':'ADJ','Voucher':'VCH','Purchase Return':'PRET'}.get(kind,'COV')),**values)

def copy_lines(source,target):
    for line in source.lines.all():
        Line.objects.create(document=target,product=line.product,device=line.device,warehouse=line.warehouse,
            quantity=line.quantity,rate=line.rate,cost=line.cost,snapshot=line.snapshot)

def stock(doc,line,qty,cost,warehouse=None):
    place=warehouse or line.warehouse or doc.warehouse
    if not place or not place.enabled:raise ValidationError('Select an active warehouse.')
    if place.branch_id!=doc.branch_id:raise ValidationError('Warehouse belongs to another shop.')
    return StockMovement.objects.create(document=doc,posting_date=doc.posting_date,product=line.product,device=line.device,
        warehouse=place,quantity=qty,value=money(qty*cost))

def on_hand(product,warehouse):
    result=StockMovement.objects.filter(product=product,warehouse=warehouse).aggregate(q=Sum('quantity'),v=Sum('value'))
    return result['q'] or ZERO,result['v'] or ZERO

def snapshot(device):
    return dict(device=device.code,condition=device.condition,battery=device.battery if device.battery_checked else None,
        pta_status=device.pta_status,faults=device.faults,warranty_days=device.warranty_days,warranty_terms=device.warranty_terms)

@transaction.atomic
def update_imeis(device,values,user):
    Device.objects.select_for_update().get(pk=device.pk)
    numbers=[str(v.get('number','')).strip() for v in values if v.get('number')]
    if len(numbers)!=len(set(numbers)):raise ValidationError('IMEI 1 and IMEI 2 must differ.')
    if numbers and not values[0].get('number'):raise ValidationError('Enter IMEI 1 before IMEI 2.')
    for slot,value in enumerate(values,1):
        number=str(value.get('number','')).strip()
        old=device.imeis.filter(slot=slot).first()
        changing=bool(old and old.number!=number)
        if changing:old.delete();old=None
        if not number:continue
        obj=old or IMEI(number=number,device=device,slot=slot)
        if IMEI.objects.filter(number=number).exclude(device=device).exists():raise ValidationError('IMEI is already registered to another device.')
        # Changing an identifier always clears the old verification metadata.
        obj.pta_status='Not Checked' if changing else value.get('pta_status','Not Checked')
        obj.verified_on=None if changing else value.get('verified_on') or None
        obj.verified_by=user.get_username() if obj.pta_status!='Not Checked' else ''
        if value.get('evidence'):obj.evidence=value['evidence']
        obj.full_clean();obj.save()

def _receive(doc):
    assigned(doc,'buyer','can_buy')
    if not doc.supplier or doc.supplier.kind!='Supplier':raise ValidationError('Select a supplier.')
    if not doc.lines.exists():raise ValidationError('Add purchase items.')
    if doc.reference_id:
        order=Document.objects.select_for_update().get(pk=doc.reference_id)
        if order.kind!='Purchase Order' or order.status!='Submitted' or order.branch_id!=doc.branch_id or order.supplier_id!=doc.supplier_id:raise ValidationError('Select this supplier’s submitted purchase order.')
        for product_id in doc.lines.values_list('product_id',flat=True).distinct():
            ordered=order.lines.filter(product_id=product_id).aggregate(q=Sum('quantity'))['q'] or ZERO
            received=Line.objects.filter(document__reference=order,document__kind='Purchase Receipt',document__status='Submitted',product_id=product_id).aggregate(q=Sum('quantity'))['q'] or ZERO
            incoming=doc.lines.filter(product_id=product_id).aggregate(q=Sum('quantity'))['q'] or ZERO
            if received+incoming>ordered:raise ValidationError('Receipt quantity exceeds the remaining purchase order quantity.')
    total=ZERO;phone_index=0
    for line in doc.lines.select_related('product').order_by('product_id','pk'):
        Product.objects.select_for_update().get(pk=line.product_id)
        if line.quantity<=0 or line.rate<0:raise ValidationError('Purchase quantities must be positive and rates cannot be negative.')
        if line.product.serialized:
            if line.quantity!=1:raise ValidationError('Receive one physical phone per line.')
            if line.device_id:raise ValidationError('This phone has already been received.')
            attrs=doc.data.get('device',{})
            per_line=attrs.get('line_attrs') or []
            if phone_index<len(per_line):attrs=dict(attrs,**per_line[phone_index])      # bulk purchases give each phone its own condition, grade, price
            device=Device(code=code('PHONE'),branch=doc.branch,product=line.product,warehouse=line.warehouse or doc.warehouse,status='Holding' if attrs.get('holding') else 'Available',
                purchase_cost=line.rate,received_on=doc.posting_date,demo_batch=doc.demo_batch,
                device_type=attrs.get('device_type','Used'),condition=attrs.get('condition','Not Inspected'),
                pta_status=attrs.get('pta_status','Not recorded'),grade=attrs.get('grade') or None,
                inspection_status=attrs.get('inspection_status','Pending'),inspection_date=attrs.get('inspection_date') or None,
                battery_checked=bool(attrs.get('battery_checked')),battery=int(attrs.get('battery') or 0),
                asking_price=money(attrs.get('asking_price')),minimum_price=money(attrs.get('minimum_price')),
                wholesale_price=money(attrs.get('wholesale_price')),warranty_days=int(attrs.get('warranty_days') or 0),
                warranty_terms=attrs.get('warranty_terms',''),faults=attrs.get('faults',''),details=attrs.get('details',{}))
            per_phone=attrs.get('line_imeis') or []
            numbers=[{'number':n} for n in per_phone[phone_index]] if phone_index<len(per_phone) else attrs.get('imeis',[{},{}]);phone_index+=1
            device.full_clean();device.save();update_imeis(device,numbers,doc.posted_by)
            line.device=device;line.cost=line.rate;line.save()
        if line.product.stock_item:stock(doc,line,line.quantity,line.rate)
        total+=money(line.quantity*line.rate)
    doc.total=total
    entry(doc,'inventory',debit=total);entry(doc,'stock_received',credit=total)

def _adjust(doc):
    """Stock write-off / count correction. Line quantity is the signed change (negative = stock leaves).
    Phones can only be written off one exact device at a time; accessories take the change at the location's average
    cost (a write-off of the last units takes the exact remaining value so no rounding residue is left behind).
    Ledger: write-offs Dr expense / Cr inventory, gains Dr inventory / Cr expense — always balanced."""
    if doc.data.get('reason') not in ADJUST_REASONS:raise ValidationError('Choose a reason for the stock adjustment.')
    if not doc.lines.exists():raise ValidationError('Add at least one stock line.')
    loss=gain=ZERO;seen=set()
    for line in doc.lines.select_related('product','device','warehouse').order_by('product_id','pk'):
        product=Product.objects.select_for_update().get(pk=line.product_id)
        place=line.warehouse or doc.warehouse
        if not product.stock_item:raise ValidationError('Only stock-tracked products can be adjusted.')
        qty=quantity_signed(line.quantity)
        if product.serialized:
            if not line.device_id or qty!=-1 or line.device_id in seen:raise ValidationError('Write off one exact phone per line.')
            device=Device.objects.select_for_update().get(pk=line.device_id);seen.add(device.pk)
            if device.product_id!=product.pk or device.status!='Available' or device.warehouse_id!=place.pk:raise ValidationError('This exact phone is not available in that stock location.')
            value=-money(device.purchase_cost);line.cost=device.purchase_cost
            device.status='Written off';device.warehouse=None;device.details=dict(device.details,written_off=dict(document=doc.code,reason=doc.data['reason']));device.save()
        else:
            on_hand_qty,on_hand_value=on_hand(product,place)
            if qty<0:
                if -qty>on_hand_qty:raise ValidationError(f'Only {on_hand_qty:g} of {product.name} in stock there; cannot remove {-qty:g}.')
                value=-on_hand_value if -qty==on_hand_qty else -money(-qty*(on_hand_value/on_hand_qty))
            else:
                unit=(on_hand_value/on_hand_qty) if on_hand_qty>0 else money(line.rate)
                if unit<=0:raise ValidationError('Enter the unit cost for stock that is being added.')
                value=money(qty*unit)
            line.cost=money(abs(value)/abs(qty))
        line.warehouse=place;line.save()
        stock(doc,line,qty,(value/qty) if qty else ZERO,warehouse=place)
        # stock() rounds qty*cost; pin the exact value so the movement equals the ledger posting
        movement=doc.stock.order_by('-pk').first();movement.value=value;movement.save(update_fields=['value'])
        if value<0:loss+=-value
        else:gain+=value
    if loss:entry(doc,'expense',debit=loss);entry(doc,'inventory',credit=loss)
    if gain:entry(doc,'inventory',debit=gain);entry(doc,'expense',credit=gain)
    doc.total=loss+gain;doc.data['loss']=str(loss);doc.data['gain']=str(gain)

def quantity_signed(value):
    value=Decimal(str(value))
    if value==0:raise ValidationError('A stock change of zero is not an adjustment.')
    return value


VOUCHER_TYPES={
    # code: (label, money flow relative to the shop, section the chosen account must belong to)
    'expense':('Shop expense','out','Expense'),
    'capital':('Owner capital introduced','in','Equity'),
    'drawings':('Owner drawings','out','Equity'),
    'loan_in':('Loan received','in','Liability'),
    'loan_out':('Loan repayment','out','Liability'),
    # posted only by the advanced accounting screens (fixed assets and prepaid expenses), never from the manual voucher form
    'asset':('Fixed asset purchase','out','Asset'),
    'asset_sale':('Fixed asset sale proceeds','in','Asset'),
    'prepaid':('Prepaid expense paid','out','Asset'),
}
SYSTEM_VOUCHERS={'asset','asset_sale','prepaid'}


def account_balance(branch,account):
    rows=LedgerEntry.objects.filter(branch=branch,account=account).aggregate(d=Sum('debit'),c=Sum('credit'))
    return money(rows['d'])-money(rows['c'])


def _voucher(doc):
    """One-step money voucher: a shop expense, owner capital or drawings, a loan received or repaid.
    Money moves through one of the shop's cash, bank or wallet accounts, so cash and bank balances always match the ledger.
    Credit purchases of services stay on Supplier Bills so they keep a supplier balance."""
    from . import permissions
    spec=doc.data;kind=spec.get('type')
    if kind not in VOUCHER_TYPES:raise ValidationError('Choose what this voucher is for.')
    label,flow,root=VOUCHER_TYPES[kind]
    user=doc.posted_by
    if not (permissions.can(user,'accounting') or (kind=='expense' and manager(user))):raise PermissionDenied('You do not have permission to record this kind of voucher.')
    amount=payment_money(spec.get('amount'))
    if amount<=0:raise ValidationError('Enter an amount greater than zero.')
    try:account=Account.objects.get(pk=spec.get('account'))
    except (Account.DoesNotExist,ValueError,ValidationError):raise ValidationError('Choose the account for this voucher.')
    if not account.enabled or account.is_group or account.root_type!=root:raise ValidationError(f'Choose an active {root.lower()} account for a {label.lower()}.')
    if account.account_type in ('Payable','Receivable','Stock','Cash','Bank','Stock Received But Not Billed','Cost of Goods Sold'):raise ValidationError('That account is a control account with its own workflow. Choose another.')
    mode=spec.get('mode','Cash');conf=settings(doc).payment_methods.get(mode)
    if not conf:raise ValidationError('Choose a payment method set up for this shop.')
    cash=Account.objects.get(pk=conf)
    if cash.account_type not in ('Cash','Bank') or not cash.enabled or cash.is_group:raise ValidationError('The payment method must use an enabled cash or bank account.')
    if cash.account_type=='Bank' and not str(spec.get('reference','')).strip():raise ValidationError('Enter the bank or wallet transaction reference.')
    if not str(spec.get('description','')).strip():raise ValidationError('Describe what this voucher is for.')
    if flow=='out':
        available=account_balance(doc.branch,cash)
        if available<amount:raise ValidationError(f'{mode} has only PKR {available:,.2f} recorded for this shop, so PKR {amount:,.2f} cannot be paid from it. Record owner capital or a deposit first.')
    shift=active_shift(doc.branch,user)
    cash_change=ZERO
    if cash.account_type=='Cash':
        cash_change=amount if flow=='in' else -amount
        if shift and shift.till_id and shift.posting_date==doc.posting_date:
            shift=Document.objects.select_for_update().get(pk=shift.pk)
            if shift.state=='Open':doc.pos_shift=shift;doc.till=shift.till
    doc.data['direction']='Receive' if flow=='in' else 'Pay'
    doc.data['mode']=mode
    doc.data['till_cash']=str(cash_change) if doc.pos_shift_id else '0.00'
    doc.data['label']=label;doc.data['account_name']=account.name
    doc.total=amount
    if flow=='out':entry(doc,account,debit=amount);entry(doc,cash,credit=amount)
    else:entry(doc,cash,debit=amount);entry(doc,account,credit=amount)


def _sale(doc):
    if doc.is_return:
        from .returns import post
        return post(doc)
    assigned(doc,'seller','can_sell')
    if not doc.customer or doc.customer.kind!='Customer':raise ValidationError('Select a customer.')
    if not doc.lines.exists():raise ValidationError('Add sale items.')
    if doc.kind=='POS Invoice':
        shift=active_shift(doc.branch,doc.posted_by)
        if not shift or not shift.till_id:raise ValidationError('Start a reconciled POS shift first. Ask a manager to review any legacy open shift.')
        if shift.posting_date!=doc.posting_date:raise ValidationError('The POS invoice must use the shift date.')
        doc.pos_shift=shift;doc.till=shift.till
    seen=set();total=ZERO;total_cost=ZERO
    from . import pricing
    invoice_totals,allocations=pricing.calculate(doc)
    from . import overrides
    approved=overrides.check_sale(doc,invoice_totals)
    shop=settings(doc)
    doc.data['receipt']=dict(shop_phone=shop.shop_phone,shop_address=shop.shop_address,receipt_terms=shop.receipt_terms,urdu_receipt_note=shop.urdu_receipt_note)
    for line in doc.lines.select_related('product','warehouse').order_by('product_id','device_id'):
        Product.objects.select_for_update().get(pk=line.product_id)
        qty=quantity(line.quantity);cost=ZERO
        if line.rate<0:raise ValidationError('Selling rates cannot be negative.')
        if line.product.serialized:
            if not line.device_id or qty!=1 or line.device_id in seen:raise ValidationError('Select one unique physical phone per invoice line.')
            device=Device.objects.select_for_update().get(pk=line.device_id);seen.add(device.pk)
            if device.product_id!=line.product_id:raise ValidationError('Phone does not match the selected variant.')
            if device.status!='Available' or device.warehouse_id!=(line.warehouse_id or doc.warehouse_id):raise ValidationError('This exact phone is no longer available in that warehouse.')
            if money(allocations[line.pk]['net'])<device.minimum_price and not manager(doc.posted_by) and not approved:raise PermissionDenied('A manager must approve a price below the minimum, including invoice discounts. Enter their manager code and PIN under Manager override.')
            cost=device.purchase_cost;line.snapshot=snapshot(device)
            device.status='Sold';device.warehouse=None;device.customer=doc.customer;device.sale=doc
            device.sold_price=money(allocations[line.pk]['net']);device.sold_on=doc.posting_date;device.save()
        elif line.product.stock_item:
            available,value=on_hand(line.product,line.warehouse or doc.warehouse)
            if qty>available:raise ValidationError('Insufficient accessory or spare-part stock.')
            cost=money(value/available)
        if line.product.stock_item:stock(doc,line,-qty,cost)
        line.snapshot['pricing']=allocations[line.pk]
        line.cost=cost;line.save();total+=money(qty*line.rate);total_cost+=money(qty*cost)
    doc.data['pricing']=invoice_totals
    doc.total=money(invoice_totals['grand_total']);doc.outstanding=doc.total
    entry(doc,'receivable',debit=doc.total,party=doc.customer,reference=doc);entry(doc,'sales',credit=invoice_totals['net_total'])
    if money(invoice_totals['tax_total']):entry(doc,pricing.tax_account(doc),credit=invoice_totals['tax_total'])
    entry(doc,'cogs',debit=total_cost);entry(doc,'inventory',credit=total_cost)

def _bill(doc):
    assigned(doc,'buyer','can_buy')
    if not doc.supplier or doc.supplier.kind!='Supplier':raise ValidationError('Select a supplier.')
    if doc.total<=0:raise ValidationError('Supplier bill amount must be positive.')
    receipt=doc.reference
    if receipt:
        if receipt.kind!='Purchase Receipt' or receipt.status!='Submitted' or receipt.branch_id!=doc.branch_id or receipt.supplier_id!=doc.supplier_id:raise ValidationError('Select a submitted receipt from this supplier and shop.')
        Document.objects.select_for_update().get(pk=receipt.pk)
        if receipt.linked.filter(kind='Purchase Invoice',status='Submitted').exclude(pk=doc.pk).exists():raise ValidationError('This receipt has already been billed.')
        if doc.total!=receipt.total:raise ValidationError('The supplier bill must match the receipt total.')
        def contents(record):return sorted((str(l.product_id),str(l.quantity),str(money(l.rate))) for l in record.lines.all())
        if contents(doc)!=contents(receipt):raise ValidationError('The supplier bill items must match the referenced receipt.')
    else:
        if not doc.lines.exists() or any(l.product.stock_item for l in doc.lines.select_related('product')):raise ValidationError('Stock purchases need a purchase receipt; use service items for expenses.')
        if doc.lines.filter(device__isnull=False).exclude(device__branch=doc.branch).exists():raise ValidationError('Repair expenses must reference a phone from this shop.')
    doc.outstanding=doc.total
    entry(doc,'stock_received' if receipt else 'expense',debit=doc.total)
    entry(doc,'payable',credit=doc.total,party=doc.supplier,reference=doc)

def _payment(doc):
    if doc.data.get('direction')=='Internal Transfer':
        from .fund_transfers import post
        return post(doc)
    assigned(doc,'handler','can_handle_payments')
    party=doc.customer or doc.supplier
    if not party or bool(doc.customer)==bool(doc.supplier):raise ValidationError('Select exactly one customer or supplier.')
    amount=payment_money(doc.data.get('amount'))
    if amount<=0:raise ValidationError('Payment amount must be positive.')
    direction=doc.data.get('direction')
    if direction not in ('Receive','Pay'):raise ValidationError('Select Receive or Pay.')
    method=doc.data.get('mode','Cash');conf=settings(doc).payment_methods.get(method)
    if not conf:raise ValidationError('Configure this payment method first.')
    cash=Account.objects.get(pk=conf)
    if cash.account_type not in ('Cash','Bank') or not cash.enabled or cash.is_group:raise ValidationError('Select an enabled cash or bank account.')
    if cash.account_type=='Bank' and not str(doc.data.get('reference','')).strip():raise ValidationError('Enter the actual non-cash transaction reference.')
    shift=doc.pos_shift or active_shift(doc.branch,doc.posted_by)
    if shift:
        shift=Document.objects.select_for_update().get(pk=shift.pk)
        if shift.kind!='POS Opening Entry' or shift.branch_id!=doc.branch_id or shift.status!='Submitted' or shift.state!='Open' or not shift.till_id:raise ValidationError('Payments need an open reconciled shift in this shop; review legacy shifts first.')
        if shift.posted_by_id!=doc.posted_by_id and not manager(doc.posted_by):raise PermissionDenied('Use your own POS shift.')
        if shift.handler_id!=doc.handler_id:raise ValidationError('The payment handler must be the shift cashier.')
        if shift.posting_date!=doc.posting_date:raise ValidationError('The payment must use the shift date.')
        doc.pos_shift=shift;doc.till=shift.till
    doc.data['till_cash']=str(amount if direction=='Receive' else -amount) if shift and cash.account_type=='Cash' else '0.00'
    if doc.reference_id:
        invoice=Document.objects.select_for_update().get(pk=doc.reference_id)
        expected_party=invoice.customer if invoice.kind in SELL else invoice.supplier
        if invoice.status!='Submitted' or invoice.kind not in SELL|{'Purchase Invoice'} or expected_party!=party or invoice.branch_id!=doc.branch_id:raise ValidationError('Allocate only to this party’s submitted invoice in this shop.')
        if invoice.is_return and direction=='Pay' and invoice.reference_id:
            original=Document.objects.select_for_update().get(pk=invoice.reference_id)
            if original.outstanding>0:raise ValidationError('Apply return credit to the unpaid original invoice before issuing a refund.')
        if amount>abs(invoice.outstanding):raise ValidationError('Payment exceeds invoice outstanding. Record an advance separately.')
        reduce=(party.kind=='Customer' and direction=='Receive') or (party.kind=='Supplier' and direction=='Pay')
        if (invoice.outstanding>0)!=reduce:raise ValidationError('Payment direction does not match the invoice or refund balance.')
        invoice.outstanding+=-amount if reduce else amount;invoice.save(update_fields=['outstanding','modified'])
    supplied=doc.data.get('cash_tendered') not in (None,'')
    cash_sale=bool(cash.account_type=='Cash' and direction=='Receive' and doc.reference_id and invoice.kind in SELL and not invoice.is_return)
    if supplied and not cash_sale:raise ValidationError('Cash handed can only be recorded for a cash receipt against a sale.')
    doc.data.pop('change_returned',None)
    if cash_sale:
        tendered=payment_money(doc.data['cash_tendered']) if supplied else amount
        if tendered<amount:raise ValidationError('Cash handed cannot be less than the amount applied. Reduce the applied amount for a partial payment.')
        doc.data.update(cash_tendered=str(tendered),change_returned=str(tendered-amount))
    else:doc.data.pop('cash_tendered',None)
    key='receivable' if party.kind=='Customer' else 'payable'
    if direction=='Receive':entry(doc,cash,debit=amount);entry(doc,key,credit=amount,party=party,reference=doc.reference)
    else:entry(doc,key,debit=amount,party=party,reference=doc.reference);entry(doc,cash,credit=amount)
    doc.total=amount;doc.data['unallocated']=str(amount if not doc.reference_id else ZERO)

def balances(branch,date):
    entries=LedgerEntry.objects.filter(branch=branch,posting_date__lte=date,account__account_type__in=['Cash','Bank'])
    return [dict(account=str(a.pk),name=a.name,expected=str(money(entries.filter(account=a).aggregate(d=Sum('debit'))['d'])-money(entries.filter(account=a).aggregate(c=Sum('credit'))['c'])))
        for a in Account.objects.filter(pk__in=list(entries.values_list('account_id',flat=True))).order_by('name')]

def active_shift(branch,user):
    return Document.objects.filter(branch=branch,kind='POS Opening Entry',status='Submitted',state='Open',posted_by=user).select_related('till','handler').first()

def till_balance(shift):
    """Use explicitly linked submitted payments, never a company account balance."""
    legacy=not shift.till_id or 'opening_float' not in shift.data
    rows=[];received=ZERO;paid=ZERO;bank_received=ZERO;bank_paid=ZERO
    transferred_in=ZERO;transferred_out=ZERO;internal_bank_in=ZERO;internal_bank_out=ZERO
    for payment in shift.shift_transactions.filter(kind__in=['Payment Entry','Voucher'],status='Submitted').order_by('created'):
        cash=money(payment.data.get('till_cash'))
        received+=max(cash,ZERO);paid+=max(-cash,ZERO)
        internal=payment.data.get('direction')=='Internal Transfer'
        if internal:
            transferred_in+=max(cash,ZERO);transferred_out+=max(-cash,ZERO)
            if payment.data.get('target_type')=='Bank':internal_bank_in+=payment.total
            if payment.data.get('source_type')=='Bank':internal_bank_out+=payment.total
        elif not cash:
            if payment.data.get('direction')=='Receive':bank_received+=payment.total
            else:bank_paid+=payment.total
        rows.append(dict(document=payment.code,id=str(payment.pk),mode=payment.data.get('mode',''),direction=payment.data.get('direction',''),amount=str(payment.total),cash_change=str(cash),
            cash_tendered=payment.data.get('cash_tendered'),change_returned=payment.data.get('change_returned')))
    opening=money(shift.data.get('opening_float'))
    return dict(legacy_review_required=legacy,shift=str(shift.pk),shift_code=shift.code,
        till=shift.data.get('till_name') or str(shift.till or 'Legacy shift — till not recorded'),
        cashier=shift.staff_names.get('handler') or str(shift.handler or ''),
        opening_float=None if legacy else str(opening),cash_received=str(received),cash_paid=str(paid),
        bank_received=str(bank_received),bank_paid=str(bank_paid),expected=None if legacy else str(opening+received-paid),payments=rows,
        internal_cash_in=str(transferred_in),internal_cash_out=str(transferred_out),internal_bank_in=str(internal_bank_in),internal_bank_out=str(internal_bank_out))

@transaction.atomic
def submit(doc,user):
    initial=Document.objects.select_related('branch').get(pk=doc.pk)
    authorize(user,initial)
    if initial.status!='Draft':raise ValidationError('Only a draft can be submitted.')
    places=[initial.branch_id]
    if initial.kind=='Stock Entry':
        target=transfer_target(initial)
        if target.branch_id!=initial.branch_id and not user.is_superuser:raise PermissionDenied('Only the owner can transfer stock between shops.')
        places.append(target.branch_id)
    # All writers lock shops before documents. Non-key locks preserve FK reads.
    locked_branches={branch.pk:branch for branch in Branch.objects.select_for_update(no_key=True).filter(pk__in=places).order_by('pk')}
    doc=Document.objects.select_for_update(of=('self',),no_key=True).select_related('branch').get(pk=initial.pk)
    if doc.branch_id!=initial.branch_id:raise ValidationError('The draft changed shops. Reload it before submitting.')
    authorize(user,doc)
    if doc.status!='Draft':raise ValidationError('Only a draft can be submitted.')
    if doc.currency!='PKR':raise ValidationError('New trading transactions must use PKR. Imported historical currencies are read-only.')
    doc.branch=locked_branches[doc.branch_id]
    if doc.kind=='Stock Entry':
        target=transfer_target(doc)
        if target.branch_id not in locked_branches:raise ValidationError('The transfer route changed. Reload it before submitting.')
    open_date(doc);doc.posted_by=user
    doc.total=sum((money(l.quantity*l.rate) for l in doc.lines.all()),ZERO)
    if doc.kind=='Mobile Intake':
        assigned(doc,'buyer','can_buy')
        if doc.lines.count()!=1 or not doc.lines.first().product.serialized:raise ValidationError('A phone intake requires one serialized phone variant.')
        receipt=new_doc('Purchase Receipt',doc.branch,supplier=doc.supplier,warehouse=doc.warehouse,buyer=doc.buyer,
            posting_date=doc.posting_date,data=doc.data.copy(),demo_batch=doc.demo_batch)
        copy_lines(doc,receipt);receipt=submit(receipt,user)
        doc.data['purchase_receipt']=str(receipt.pk);doc.data['device']=str(receipt.lines.get().device_id)
        doc.total=receipt.total
    elif doc.kind=='Purchase Receipt':_receive(doc)
    elif doc.kind=='Stock Adjustment':_adjust(doc)
    elif doc.kind=='Voucher':_voucher(doc)
    elif doc.kind in SELL:_sale(doc)
    elif doc.kind=='Purchase Invoice':_bill(doc)
    elif doc.kind=='Purchase Order':
        assigned(doc,'buyer','can_buy')
        if not doc.supplier or not doc.lines.exists():raise ValidationError('Select supplier and items.')
        for line in doc.lines.all():
            quantity(line.quantity)
            if line.rate<0:raise ValidationError('Purchase rates cannot be negative.')
    elif doc.kind=='Payment Entry':_payment(doc)
    elif doc.kind=='Stock Entry':
        _transfer(doc,user,target)
    elif doc.kind=='Mobile Daily Closing':
        assigned(doc,'handler','can_handle_payments')
        if Document.objects.filter(branch=doc.branch,kind='POS Opening Entry',status='Submitted',state='Open',posting_date__lte=doc.posting_date).exists():raise ValidationError('Close POS shifts through this date first.')
        expected=balances(doc.branch,doc.posting_date);counts=doc.data.get('counts',{})
        if set(counts)!=set(r['account'] for r in expected):raise ValidationError('Load all current balances and enter each counted amount.')
        for row in expected:
            value=counts[row['account']];row['counted']=str(money(value.get('counted')));row['notes']=value.get('notes','')
            row['difference']=str(money(row['counted'])-money(row['expected']))
            if money(row['difference']) and not row['notes'].strip():raise ValidationError('Explain each closing difference.')
        doc.data['balances']=expected
        over_short=Account.objects.filter(code='COA-6950',enabled=True,is_group=False).first()
        if over_short:
            # Book each counted difference so the ledger agrees with the cash and bank that were actually counted.
            for row in expected:
                diff=money(row['difference'])
                if not diff:continue
                counted_account=Account.objects.get(pk=row['account'])
                if diff<0:entry(doc,over_short,debit=-diff);entry(doc,counted_account,credit=-diff)
                else:entry(doc,counted_account,debit=diff);entry(doc,over_short,credit=diff)
            doc.data['differences_posted']=True
    elif doc.kind=='POS Opening Entry':
        assigned(doc,'handler','can_handle_payments')
        person=staff(doc.posted_by)
        if person and not manager(doc.posted_by) and person.pk!=doc.handler_id:raise PermissionDenied('Open a shift under your own staff name.')
        if not doc.till_id or not doc.till.enabled or doc.till.branch_id!=doc.branch_id:raise ValidationError('Select an active till in this shop.')
        if 'opening_float' not in doc.data or doc.data['opening_float'] in (None,''):raise ValidationError('Enter the counted opening float, including zero when empty.')
        opening=money(doc.data['opening_float'])
        if opening<0:raise ValidationError('Opening float cannot be negative.')
        if Document.objects.filter(branch=doc.branch,kind='POS Opening Entry',status='Submitted',state='Open').filter(Q(till=doc.till)|Q(handler=doc.handler)|Q(posted_by=user)).exclude(pk=doc.pk).exists():raise ValidationError('This till, cashier or login already has an open shift.')
        doc.data.update(opening_float=str(opening),till_name=doc.till.name,opened_at=timezone.now().isoformat())
        doc.state='Open'
    elif doc.kind=='POS Closing Entry':
        assigned(doc,'handler','can_handle_payments')
        shift=Document.objects.select_for_update().filter(pk=doc.reference_id,kind='POS Opening Entry',status='Submitted',state='Open',branch=doc.branch).first()
        if not shift:raise ValidationError('Select an open shift in this shop.')
        if shift.posted_by_id!=user.pk and not manager(user):raise PermissionDenied('Close your own shift.')
        if not manager(user) and staff(user).pk!=doc.handler_id:raise PermissionDenied('Record your own staff name as the closing cashier.')
        if shift.till_id and doc.posting_date!=shift.posting_date:raise ValidationError('The closing must use the shift date.')
        if doc.posting_date<shift.posting_date:raise ValidationError('Closing cannot predate the opening shift.')
        if 'counted_cash' not in doc.data or doc.data['counted_cash'] in (None,''):raise ValidationError('Enter the physically counted closing cash, including zero when empty.')
        counted=money(doc.data['counted_cash']);note=str(doc.data.get('difference_notes','')).strip()
        if counted<0:raise ValidationError('Counted cash cannot be negative.')
        reconciliation=till_balance(shift)
        difference=None if reconciliation['legacy_review_required'] else str(counted-money(reconciliation['expected']))
        if reconciliation['legacy_review_required']:
            if not manager(user):raise PermissionDenied('A manager must review this legacy shift.')
            if not note:raise ValidationError('Explain the legacy shift review; its opening float and historical till movements are unknown.')
        elif money(difference) and not note:raise ValidationError('Explain the cash shortage or excess before closing.')
        reconciliation.update(counted=str(counted),difference=difference,notes=note)
        doc.data.update(closed_at=timezone.now().isoformat(),reconciliation=reconciliation)
        doc.pos_shift=shift;doc.till=shift.till
        shift.state='Closed';shift.save()
    elif doc.kind=='Warranty Claim':doc.state=doc.state or 'Open'
    elif doc.kind=='Journal Entry':
        from .journals import post
        post(doc)
    elif doc.kind=='Mobile Exchange':raise ValidationError('Use the dedicated exchange settlement action.')
    else:raise ValidationError('Unsupported transaction type.')
    origins=set()
    for line in doc.lines.select_related('product','device'):
        if line.device and line.device.demo_batch:origins.add(line.device.demo_batch)
        if line.product.demo_batch:origins.add(line.product.demo_batch)
    for party in (doc.customer,doc.supplier):
        if party and party.demo_batch:origins.add(party.demo_batch)
    if origins:doc.data['initial_record_batches']=sorted(origins)
    # A master record's origin must not put a new operating-shop transaction
    # into demo mode. Only an explicit transaction/shop mode does that.
    doc.demo_batch=doc.demo_batch or doc.branch.demo_batch
    balanced(doc);doc.status='Submitted';doc.save();audit(user,'Submit',doc)
    return doc

@transaction.atomic
def supplier_bill(receipt,user):
    receipt=Document.objects.select_for_update().get(pk=receipt.pk)
    authorize(user,receipt)
    if receipt.kind=='Mobile Intake':receipt=Document.objects.get(pk=receipt.data['purchase_receipt'])
    if receipt.kind!='Purchase Receipt' or receipt.status!='Submitted':raise ValidationError('Select a submitted purchase receipt.')
    existing=receipt.linked.filter(kind='Purchase Invoice').exclude(status='Cancelled').first()
    if existing:return existing
    doc=new_doc('Purchase Invoice',receipt.branch,supplier=receipt.supplier,warehouse=receipt.warehouse,buyer=receipt.buyer,
        reference=receipt,total=receipt.total,demo_batch=receipt.demo_batch)
    copy_lines(receipt,doc);audit(user,'Create supplier bill',doc);return doc

@transaction.atomic
def checkout(doc,user,payments,handler=None):
    if doc.kind not in SELL or doc.is_return:raise ValidationError('Select a draft sale for checkout.')
    if not isinstance(payments,list) or len(payments)>30:raise ValidationError('Use at most 30 payment rows.')
    for payment in payments:
        if not isinstance(payment,dict) or not isinstance(payment.get('mode'),str) or not isinstance(payment.get('reference',''),str):raise ValidationError('Enter a payment method, amount and text reference for each payment row.')
    doc=submit(doc,user)
    for payment in payments:
        payment_data=dict(amount=payment.get('amount'),direction='Receive',mode=payment['mode'],reference=payment.get('reference',''))
        if payment.get('cash_tendered') not in (None,''):payment_data['cash_tendered']=payment['cash_tendered']
        pe=new_doc('Payment Entry',doc.branch,customer=doc.customer,reference=doc,handler=handler,demo_batch=doc.demo_batch,
            posting_date=doc.posting_date,pos_shift=doc.pos_shift,
            data=payment_data)
        submit(pe,user)
    doc.refresh_from_db()
    from . import credit
    credit.check_limit(doc)
    return doc

def return_sale(original,user,device_ids,condition,reason,quantities=None,warehouse=None):
    from .returns import create
    return create(original,user,device_ids,condition,reason,quantities,warehouse)

def apply_return_credit(credit,amount,user):
    from .returns import apply_credit
    return apply_credit(credit,amount,user)

@transaction.atomic
def settle_exchange(doc,user):
    doc=Document.objects.get(pk=doc.pk);authorize(user,doc)
    Branch.objects.select_for_update(no_key=True).get(pk=doc.branch_id)
    doc=Document.objects.select_for_update().get(pk=doc.pk);authorize(user,doc)
    if doc.kind!='Mobile Exchange' or doc.state=='Settled':raise ValidationError('Select an unsettled exchange.')
    sale=Document.objects.select_for_update().get(pk=doc.data['sales_invoice'])
    purchase=Document.objects.select_for_update().get(pk=doc.data['purchase_invoice'])
    if sale.kind not in SELL or purchase.kind!='Purchase Invoice' or sale.status!='Submitted' or purchase.status!='Submitted' or sale.is_return or sale.branch_id!=doc.branch_id or purchase.branch_id!=doc.branch_id or sale.customer_id!=doc.customer_id:raise ValidationError('Select this customer’s submitted sale and the incoming submitted supplier bill.')
    receipt=purchase.reference
    if not receipt or str(receipt.data.get('trade_in_customer',''))!=str(doc.customer_id):raise ValidationError('The incoming receipt must identify this trade-in customer.')
    if sale.outstanding<0 or purchase.outstanding<0 or not (sale.outstanding or purchase.outstanding):raise ValidationError('Invoices are already settled or require a refund review.')
    open_date(doc);doc.posted_by=user
    offset=min(sale.outstanding,purchase.outstanding)
    if offset:
        entry(doc,'payable',debit=offset,party=purchase.supplier,reference=purchase)
        entry(doc,'receivable',credit=offset,party=sale.customer,reference=sale)
        sale.outstanding-=offset;purchase.outstanding-=offset;sale.save();purchase.save()
    difference=sale.outstanding-purchase.outstanding
    if difference:
        invoice=sale if difference>0 else purchase
        pe=new_doc('Payment Entry',doc.branch,customer=sale.customer if difference>0 else None,
            supplier=purchase.supplier if difference<0 else None,reference=invoice,handler=doc.handler,
            demo_batch=doc.demo_batch or sale.demo_batch or purchase.demo_batch,
            data=dict(amount=str(abs(difference)),direction='Receive' if difference>0 else 'Pay',mode=doc.data.get('mode'),reference=doc.data.get('reference','')))
        submit(pe,user);doc.data['difference_payment']=str(pe.pk)
    balanced(doc);doc.status='Submitted';doc.state='Settled';doc.save();audit(user,'Settle exchange',doc);return doc

@transaction.atomic
def once(key,user,payload,operation):
    fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()
    record,created=RequestKey.objects.get_or_create(key=key,defaults=dict(actor=user,fingerprint=fingerprint))
    record=RequestKey.objects.select_for_update().get(pk=record.pk)
    if record.actor_id!=user.pk or record.fingerprint!=fingerprint:raise ValidationError('This request key belongs to another operation.')
    if not created:
        if record.document:return record.document
        raise ValidationError('The previous operation has not completed.')
    result=operation();record.document=result;record.save();return result

@transaction.atomic
def allocate_advance(advance,invoice,amount,user):
    advance=Document.objects.get(pk=advance.pk);authorize(user,advance)
    Branch.objects.select_for_update(no_key=True).get(pk=advance.branch_id)
    advance=Document.objects.select_for_update().get(pk=advance.pk)
    invoice=Document.objects.select_for_update().get(pk=invoice.pk)
    authorize(user,advance);require(user,invoice.branch,'can_handle_payments')
    amount=money(amount);party=advance.customer or advance.supplier
    expected_party=invoice.customer if invoice.kind in SELL else invoice.supplier
    direction='Receive' if party and party.kind=='Customer' else 'Pay'
    if advance.kind!='Payment Entry' or advance.status!='Submitted' or advance.data.get('direction')!=direction or not party or expected_party!=party or invoice.kind not in SELL|{'Purchase Invoice'} or invoice.status!='Submitted' or invoice.is_return or invoice.branch_id!=advance.branch_id:
        raise ValidationError('Select this party’s open invoice and an eligible advance in the same shop.')
    available=money(advance.data.get('unallocated'))
    if amount<=0 or amount>available or amount>invoice.outstanding:raise ValidationError('Allocation exceeds the available advance or invoice balance.')
    doc=new_doc('Journal Entry',invoice.branch,customer=invoice.customer,supplier=invoice.supplier,reference=invoice,posted_by=user,
        demo_batch=advance.demo_batch or invoice.demo_batch,notes='Advance allocation from '+advance.code)
    open_date(doc);key='receivable' if party.kind=='Customer' else 'payable'
    if party.kind=='Customer':entry(doc,key,debit=amount,party=party,reference=advance);entry(doc,key,credit=amount,party=party,reference=invoice)
    else:entry(doc,key,debit=amount,party=party,reference=invoice);entry(doc,key,credit=amount,party=party,reference=advance)
    invoice.outstanding-=amount;invoice.save();advance.data['unallocated']=str(available-amount)
    advance.data.setdefault('allocations',[]).append(dict(invoice=str(invoice.pk),amount=str(amount),journal=str(doc.pk)));advance.save()
    doc.total=amount;doc.status='Submitted';doc.data['advance']=str(advance.pk);doc.save();balanced(doc);audit(user,'Allocate advance',doc);return doc

@transaction.atomic
def cancel(doc,user):
    doc=Document.objects.get(pk=doc.pk);require(user,doc.branch)
    Branch.objects.select_for_update(no_key=True).get(pk=doc.branch_id)
    doc=Document.objects.select_for_update().get(pk=doc.pk);require(user,doc.branch)
    if not manager(user):raise PermissionDenied('A manager must cancel a posted transaction.')
    if doc.status!='Submitted':raise ValidationError('Select a submitted transaction.')
    if doc.source:raise ValidationError('Imported historical transactions are preserved read-only. Use reviewed correcting transactions.')
    if doc.kind=='Payment Entry' and doc.data.get('direction')=='Internal Transfer':raise ValidationError('Record a new reverse internal transfer instead of cancelling a completed movement.')
    open_date(doc)
    if doc.is_return:raise ValidationError('Posted returns require a stock and credit review rather than cancellation.')
    if doc.kind in ('Mobile Daily Closing','POS Opening Entry','POS Closing Entry','Mobile Exchange','Journal Entry','Purchase Return'):
        raise ValidationError('This record requires a dedicated reconciliation review rather than cancellation.')
    if doc.linked.exclude(status='Cancelled').exists() or doc.allocations.exclude(document=doc).exists():raise ValidationError('Reverse linked invoices, returns or payments first.')
    for exchange in Document.objects.filter(kind='Mobile Exchange').exclude(state='Cancelled'):
        if str(doc.pk) in (exchange.data.get('sales_invoice'),exchange.data.get('purchase_invoice')):raise ValidationError('This invoice belongs to an exchange; review its settlement first.')
    if doc.kind=='Payment Entry':
        if doc.pos_shift_id and doc.pos_shift.state!='Open':raise ValidationError('This payment belongs to a closed till shift. Use a reviewed current-period correction.')
        if doc.data.get('allocations'):raise ValidationError('An allocated advance needs an accounting review before cancellation.')
        if doc.reference_id:
            invoice=Document.objects.select_for_update().get(pk=doc.reference_id)
            party=doc.customer or doc.supplier
            reduce=(party.kind=='Customer' and doc.data.get('direction')=='Receive') or (party.kind=='Supplier' and doc.data.get('direction')=='Pay')
            invoice.outstanding+=doc.total if reduce else -doc.total;invoice.save()
    elif doc.kind in SELL:
        for line in doc.lines.select_related('device','product').order_by('product_id','device_id'):
            Product.objects.select_for_update().get(pk=line.product_id)
            if line.device:
                device=Device.objects.select_for_update().get(pk=line.device_id)
                if doc.is_return:raise ValidationError('Returned-phone cancellation requires a condition and stock review.')
                if device.sale_id!=doc.pk or device.status!='Sold':raise ValidationError('The device has subsequent stock activity.')
                device.status='Available';device.warehouse=line.warehouse or doc.warehouse;device.customer=None;device.sale=None;device.sold_on=None;device.sold_price=ZERO;device.save()
    elif doc.kind=='Purchase Receipt':
        for line in doc.lines.select_related('device','product').order_by('product_id','device_id'):
            Product.objects.select_for_update().get(pk=line.product_id)
            available,_=on_hand(line.product,line.warehouse or doc.warehouse)
            if available<line.quantity:raise ValidationError('Purchased stock has already been used.')
            if line.device:
                device=Device.objects.select_for_update().get(pk=line.device_id)
                if device.status!='Available' or device.warehouse_id!=(line.warehouse_id or doc.warehouse_id):raise ValidationError('Phone has subsequent stock activity.')
                released=[i.number for i in device.imeis.all()]
                if released:device.details=dict(device.details,released_imeis=released);device.imeis.all().delete()  # free the numbers for a corrected re-entry; kept on the device for audit
                device.status='Unavailable';device.warehouse=None;device.save()
    elif doc.kind=='Stock Adjustment':
        for line in doc.lines.select_related('device'):
            if line.device_id:
                device=Device.objects.select_for_update().get(pk=line.device_id)
                if device.status!='Written off':raise ValidationError('The written-off phone has subsequent stock activity.')
                device.status='Available';device.warehouse=line.warehouse or doc.warehouse;device.details={k:v for k,v in device.details.items() if k!='written_off'};device.save()
            else:
                product=Product.objects.select_for_update().get(pk=line.product_id)
                if line.quantity>0:
                    available,_=on_hand(product,line.warehouse or doc.warehouse)
                    if available<line.quantity:raise ValidationError('Added stock has already been used.')
    elif doc.kind=='Mobile Intake':
        receipt=Document.objects.get(pk=doc.data['purchase_receipt']);cancel(receipt,user)
    elif doc.kind=='Stock Entry':raise ValidationError('Create a reverse stock transfer to preserve its movement history.')
    reversal=new_doc('Journal Entry',doc.branch,reference=doc,posted_by=user,notes='Cancellation reversal of '+doc.code,demo_batch=doc.demo_batch,status='Submitted')
    for original in doc.ledger.all():entry(reversal,original.account,debit=original.credit,credit=original.debit,party=original.party,reference=original.reference)
    for movement in doc.stock.all():StockMovement.objects.create(document=reversal,posting_date=reversal.posting_date,product=movement.product,device=movement.device,warehouse=movement.warehouse,quantity=-movement.quantity,value=-movement.value)
    doc.status='Cancelled';doc.outstanding=ZERO;doc.save();balanced(reversal);audit(user,'Cancel',doc,reversal=str(reversal.pk));return doc
