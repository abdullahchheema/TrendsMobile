"""One-save counter entry using the existing stock, ledger and payment services."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Branch,Product,Device,Line
from . import access,catalog_entry,services as s,customer_names


def paid_amount(data,total):
    mode=data['payment_status']
    if mode=='full':paid=total
    elif mode=='unpaid':paid=s.ZERO
    elif mode=='part':paid=s.payment_money(data.get('amount_paid') or 0)
    else:raise ValidationError('Choose how much has been paid.')
    if paid<0 or paid>total or (mode=='part' and not 0<paid<total):raise ValidationError('A part payment must be greater than zero and less than the total.')
    return paid


def payment(data,total):
    paid=paid_amount(data,total)
    return ([dict(mode=data['method'],amount=str(paid),reference=data.get('payment_reference',''))] if paid else []),paid


def parse_imeis(text,quantity):
    """Turn the IMEI box into one list of 1–2 numbers per phone. Blank is allowed; otherwise every phone needs IMEI 1,
    every number must pass the check-digit test, and none may repeat or already exist in the system."""
    from .models import IMEI
    rows=[line.replace(',',' ').split() for line in (text or '').replace(';','\n').splitlines() if line.strip()]
    if not rows:return []
    if len(rows)!=quantity:raise ValidationError(f'You entered {len(rows)} IMEI line(s) for {quantity} phone(s). Enter one line per phone, or leave the box empty.')
    seen={}
    for position,row in enumerate(rows,1):
        if len(row)>2:raise ValidationError(f'Phone {position}: enter at most two IMEIs (one per SIM).')
        for number in row:
            if not s.valid_imei(number):raise ValidationError(f'Phone {position}: “{number}” is not a valid 15-digit IMEI. Check for a typing mistake.')
            if number in seen:raise ValidationError(f'IMEI {number} appears twice (phones {seen[number]} and {position}).')
            seen[number]=position
    for found in IMEI.objects.filter(number__in=list(seen)).select_related('device'):
        raise ValidationError(f'IMEI {found.number} is already registered in the system. The same phone cannot be bought twice.')
    return rows


@transaction.atomic
def purchase(data,user,demo_batch=''):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk);access.require(user,branch,'can_buy')
    demo_batch=demo_batch or branch.demo_batch
    supplier=customer_names.resolve(data['party_name'],user,kind='Supplier',demo_batch=demo_batch,branch=branch)
    if data['warehouse'].branch_id!=branch.pk or not data['warehouse'].enabled:raise ValidationError('Choose this shop’s active stock location.')
    chosen=data.get('product') or catalog_entry.resolve_variant(data.get('variant') or {})
    product=Product.objects.select_for_update().get(pk=chosen.pk,enabled=True,serialized=True,stock_item=True)
    qty=int(data['quantity']);cost=s.payment_money(data['price']);asking=s.payment_money(data.get('asking_price') or 0)
    if not 1<=qty<=20 or cost<=0 or asking<0:raise ValidationError('Enter 1–20 phones and a buying price.')
    if data['pta_status'] not in ('PTA','Non-PTA'):raise ValidationError('Choose PTA or Non-PTA.')
    receipt=s.new_doc('Purchase Receipt',branch,supplier=supplier,warehouse=data['warehouse'],buyer=data['staff'],posting_date=data['posting_date'],demo_batch=demo_batch,
        notes=data.get('notes',''),data={'device':dict(pta_status=data['pta_status'],device_type=data['device_type'],condition=data['condition'],inspection_status='Passed',inspection_date=str(data['posting_date']),asking_price=str(asking),line_imeis=data.get('imeis') or [],**({'details':dict(condition_notes=catalog_entry.tidy(data['phone_notes']))} if catalog_entry.tidy(data.get('phone_notes')) else {}))})
    for _ in range(qty):Line.objects.create(document=receipt,product=product,warehouse=data['warehouse'],quantity=1,rate=cost)
    receipt=s.submit(receipt,user);bill=s.supplier_bill(receipt,user);bill.posting_date=data['posting_date'];bill.data['easy_entry']={'kind':'purchase','quantity':qty};bill.save();bill=s.submit(bill,user)
    paid=paid_amount(data,bill.total)
    if paid:
        entry=s.new_doc('Payment Entry',branch,supplier=supplier,reference=bill,handler=data['handler'],posting_date=bill.posting_date,demo_batch=demo_batch,
            data=dict(direction='Pay',amount=str(paid),mode=data['method'],reference=data.get('payment_reference','')))
        s.submit(entry,user)
    bill.refresh_from_db();return bill


@transaction.atomic
def sale(data,user,demo_batch=''):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk);access.require(user,branch,'can_sell')
    demo_batch=demo_batch or branch.demo_batch
    customer=customer_names.resolve(data['party_name'],user,demo_batch=demo_batch,branch=branch)
    Product.objects.select_for_update().get(pk=data['phone'].product_id,enabled=True,serialized=True)
    phone=Device.objects.select_for_update().filter(pk=data['phone'].pk,branch=branch).first()
    if phone is None:raise ValidationError('This phone is not in this shop’s stock. A shop can only sell its own phones.')
    if phone.status!='Available' or not phone.warehouse_id:raise ValidationError('This phone has already been sold. Choose another available phone.')
    if data['pta_status'] not in ('PTA','Non-PTA'):raise ValidationError('Choose PTA or Non-PTA.')
    phone.pta_status=data['pta_status'];phone.save(update_fields=['pta_status','modified'])
    price=s.payment_money(data['price'])
    if price<=0:raise ValidationError('Enter a selling price greater than zero.')
    doc=s.new_doc('Sales Invoice',branch,customer=customer,seller=data['staff'],warehouse=phone.warehouse,posting_date=data['posting_date'],demo_batch=demo_batch,
        notes=data.get('notes',''),data={'easy_entry':{'kind':'sale'}})
    Line.objects.create(document=doc,product=phone.product,device=phone,warehouse=phone.warehouse,quantity=1,rate=price)
    if data.get('override_approver') or data.get('override_pin'):
        from . import overrides
        overrides.apply(doc,user,data.get('override_approver'),data.get('override_pin'),'Counter sale')
    payments,paid=payment(data,price)
    if data.get('instalments') and not data.get('due_date'):raise ValidationError('Choose the first instalment date.')
    sale=s.checkout(doc,user,payments,data['handler'])
    if sale.outstanding>0 and data.get('due_date'):
        from . import credit
        credit.plan(sale,user,data.get('instalments') or 0,data['due_date']);sale.refresh_from_db()
    return sale
