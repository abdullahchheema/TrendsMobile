"""One-save counter entry using the existing stock, ledger and payment services."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Branch,Product,Device,Line
from . import access,services as s,customer_names


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


@transaction.atomic
def purchase(data,user,demo_batch=''):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=data['branch'].pk);access.require(user,branch,'can_buy')
    demo_batch=demo_batch or branch.demo_batch
    supplier=customer_names.resolve(data['party_name'],user,kind='Supplier',demo_batch=demo_batch)
    if data['warehouse'].branch_id!=branch.pk or not data['warehouse'].enabled:raise ValidationError('Choose this shop’s active stock location.')
    product=Product.objects.select_for_update().get(pk=data['product'].pk,enabled=True,serialized=True,stock_item=True)
    qty=int(data['quantity']);cost=s.payment_money(data['price']);asking=s.payment_money(data['asking_price'])
    if not 1<=qty<=20 or cost<=0 or asking<=0:raise ValidationError('Enter 1–20 phones, a buying price and an expected selling price.')
    if data['pta_status'] not in ('PTA','Non-PTA'):raise ValidationError('Choose PTA or Non-PTA.')
    receipt=s.new_doc('Purchase Receipt',branch,supplier=supplier,warehouse=data['warehouse'],buyer=data['staff'],posting_date=data['posting_date'],demo_batch=demo_batch,
        notes=data.get('notes',''),data={'device':dict(pta_status=data['pta_status'],device_type=data['device_type'],condition=data['condition'],inspection_status='Passed',inspection_date=str(data['posting_date']),asking_price=str(asking))})
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
    customer=customer_names.resolve(data['party_name'],user,demo_batch=demo_batch)
    Product.objects.select_for_update().get(pk=data['phone'].product_id,enabled=True,serialized=True)
    phone=Device.objects.select_for_update().get(pk=data['phone'].pk,branch=branch)
    if phone.status!='Available' or not phone.warehouse_id:raise ValidationError('This phone has already been sold. Choose another available phone.')
    if data['pta_status'] not in ('PTA','Non-PTA'):raise ValidationError('Choose PTA or Non-PTA.')
    phone.pta_status=data['pta_status'];phone.save(update_fields=['pta_status','modified'])
    price=s.payment_money(data['price'])
    if price<=0:raise ValidationError('Enter a selling price greater than zero.')
    doc=s.new_doc('Sales Invoice',branch,customer=customer,seller=data['staff'],warehouse=phone.warehouse,posting_date=data['posting_date'],demo_batch=demo_batch,
        notes=data.get('notes',''),data={'easy_entry':{'kind':'sale'}})
    Line.objects.create(document=doc,product=phone.product,device=phone,warehouse=phone.warehouse,quantity=1,rate=price)
    payments,paid=payment(data,price)
    return s.checkout(doc,user,payments,data['handler'])
