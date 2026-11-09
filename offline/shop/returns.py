"""Line-linked physical returns and non-cash application of customer return credit."""
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import *


def sale_lines(original):
    from . import services as s
    if original.kind not in s.SELL or original.status!='Submitted' or original.is_return:
        raise ValidationError('Select an original submitted sale.')
    lines=list(original.lines.select_related('product','device','warehouse').order_by('pk'))
    from .pricing import frozen_lines
    if not frozen_lines(original,lines) and s.money(sum((s.money(line.quantity*line.rate) for line in lines),s.ZERO))!=s.money(original.total):
        raise ValidationError('This invoice has additional taxes, discounts or adjustments. A reviewed credit calculation is required.')
    return [line for line in lines if line.product.stock_item]


def used_quantities(original,lines=None):
    """Read old return history without modifying imported rows or guessing a price."""
    lines=sale_lines(original) if lines is None else lines
    by_id={str(line.pk):line for line in lines};used={key:Decimal(0) for key in by_id}
    history=Line.objects.filter(document__reference=original,document__kind__in=['Sales Invoice','POS Invoice'],document__is_return=True,document__status='Submitted').select_related('product').order_by('document__created','pk')
    for returned in history:
        if not returned.product.stock_item:continue
        marker=returned.snapshot.get('return_line')
        if marker is not None:
            candidates=[by_id[str(marker)]] if str(marker) in by_id else []
            if not candidates or candidates[0].product_id!=returned.product_id or candidates[0].device_id!=returned.device_id:
                raise ValidationError('A historical return has an invalid original-line link; review it before returning more stock.')
        elif returned.device_id:
            candidates=[line for line in lines if line.device_id==returned.device_id]
        else:
            candidates=[line for line in lines if line.product_id==returned.product_id and not line.device_id and line.rate==returned.rate]
            matching=[line for line in candidates if line.cost==returned.cost]
            if matching:candidates=matching
            elif len(candidates)>1:raise ValidationError('Historical accessory return costs need review before another return.')
        remaining=returned.quantity
        for line in candidates:
            available=line.quantity-used[str(line.pk)];take=min(max(available,0),remaining)
            used[str(line.pk)]+=take;remaining-=take
            if remaining==0:break
        if remaining:raise ValidationError('Historical returns exceed or do not match the original invoice lines. Review the history first.')
    return used


def options(original):
    lines=sale_lines(original);used=used_quantities(original,lines)
    for line in lines:
        line.returned_quantity=used[str(line.pk)];line.remaining_quantity=line.quantity-line.returned_quantity
        line.returnable=line.remaining_quantity>0 and (not line.product.serialized or bool(line.device_id and line.device.status=='Sold' and line.device.sale_id==original.pk))
    return lines


def plan(doc):
    from . import services as s
    original=doc.reference
    if not original or original.branch_id!=doc.branch_id or original.customer_id!=doc.customer_id:
        raise ValidationError('Use this customer’s original invoice in the same shop.')
    sources=sale_lines(original);used=used_quantities(original,sources);by_id={str(line.pk):line for line in sources}
    result=[]
    for line in doc.lines.select_related('product','device','warehouse').order_by('product_id','device_id','pk'):
        marker=line.snapshot.get('return_line')
        source=by_id.get(str(marker)) if marker is not None else None
        if marker is None:
            matches=[item for item in sources if item.product_id==line.product_id and item.device_id==line.device_id and item.rate==line.rate]
            if len(matches)==1:source=matches[0]
        if not source or source.product_id!=line.product_id or source.device_id!=line.device_id or source.rate!=line.rate:
            raise ValidationError('Return the exact original invoice line at its original price.')
        qty=s.quantity(line.quantity);prior=used[str(source.pk)]
        if qty+prior>source.quantity:raise ValidationError('Return quantity exceeds the remaining original invoice quantity.')
        if source.product.serialized and (not source.device_id or qty!=1):raise ValidationError('Return each exact phone once with quantity one.')
        # Incremental rounded totals preserve the final paisa on fractional returns.
        from .pricing import return_parts
        parts=return_parts(source,prior,qty);amount=parts['total'];line.return_parts=parts
        cost_value=s.money((prior+qty)*source.cost)-s.money(prior*source.cost)
        result.append((line,source,amount,cost_value));used[str(source.pk)]+=qty
    if not result:raise ValidationError('Select at least one physical item to return.')
    return result


def capture_totals(doc,parts):
    from . import services as s
    totals={key:sum((item[key] for item in parts),s.ZERO) for key in ('base','discount','net','tax','total')}
    doc.data['pricing']={**doc.reference.data.get('pricing',{}),'version':1,'subtotal':str(-totals['base']),'discount':str(-totals['discount']),
        'net_total':str(-totals['net']),'tax_total':str(-totals['tax']),'grand_total':str(-totals['total'])}
    doc.total=-totals['total']
    return totals


def post(doc):
    from . import services as s
    original=Document.objects.select_for_update().get(pk=doc.reference_id) if doc.reference_id else None
    doc.reference=original
    if not original:raise ValidationError('Select the original submitted customer sale.')
    planned=plan(doc);total=cost_total=s.ZERO
    reversed_totals={key:s.ZERO for key in ('base','discount','net','tax','total')}
    for line,source,amount,cost_value in planned:
        Product.objects.select_for_update().get(pk=line.product_id)
        line.snapshot={**source.snapshot,'return_line':str(source.pk),'return_amount':str(amount),'return_cost_value':str(cost_value)}
        for key,value in line.return_parts.items():
            line.snapshot['return_'+key]=str(value);reversed_totals[key]+=value
        line.cost=source.cost
        if line.product.serialized:
            device=Device.objects.select_for_update().get(pk=line.device_id)
            if device.branch_id!=doc.branch_id or device.sale_id!=original.pk or device.status!='Sold':
                raise ValidationError('The phone is not an unreturned device from this sale and shop.')
            device.warehouse=line.warehouse or doc.warehouse;device.status='Holding';device.sale=None;device.customer=None
            device.sold_on=None;device.sold_price=0;device.inspection_status='Pending'
            device.details['inspection_notes']='Returned: '+doc.notes;device.save()
        movement=s.stock(doc,line,line.quantity,line.cost)
        movement.value=cost_value;movement.save(update_fields=['value']);line.save()
        total+=amount;cost_total+=cost_value
    doc.seller=original.seller
    if original.staff_names.get('seller'):doc.staff_names['seller']=original.staff_names['seller']
    elif original.seller:doc.staff_names['seller']=original.seller.name
    actor=s.staff(doc.posted_by);doc.staff_names['returned_by']=actor.name if actor else doc.posted_by.get_username()
    doc.data['receipt']=original.data.get('receipt',{});doc.total=-total;doc.outstanding=-total
    capture_totals(doc,[reversed_totals])
    s.entry(doc,'receivable',credit=total,party=doc.customer,reference=doc);s.entry(doc,'sales',debit=reversed_totals['net'])
    if reversed_totals['tax']:
        from .pricing import tax_account
        s.entry(doc,tax_account(original),debit=reversed_totals['tax'])
    s.entry(doc,'inventory',debit=cost_total);s.entry(doc,'cogs',credit=cost_total)


@transaction.atomic
def create(original,user,device_ids,condition,reason,quantities=None,warehouse=None):
    from . import services as s
    original=Document.objects.get(pk=original.pk);s.authorize(user,original)
    Branch.objects.select_for_update(no_key=True).get(pk=original.branch_id)
    original=Document.objects.select_for_update().get(pk=original.pk);s.authorize(user,original)
    lines=sale_lines(original)
    if not isinstance(condition,str) or not condition.strip() or not isinstance(reason,str) or not reason.strip():
        raise ValidationError('Record returned condition and reason.')
    if not isinstance(device_ids,(list,tuple)):
        raise ValidationError('Select each returned phone once.')
    values=[str(value) for value in device_ids]
    if len(values)!=len(set(values)):raise ValidationError('Select each returned phone once.')
    device_ids=set(values)
    selected={str(line.pk):Decimal(1) for line in lines if line.device_id and str(line.device_id) in device_ids}
    if len(selected)!=len(device_ids):raise ValidationError('Select phones from this invoice only.')
    if quantities is not None and not isinstance(quantities,dict):raise ValidationError('Select original invoice lines and quantities.')
    by_id={str(line.pk):line for line in lines}
    for key,value in (quantities or {}).items():
        key=str(key)
        if key not in by_id or by_id[key].product.serialized:raise ValidationError('Choose accessory quantities from this invoice only.')
        if key in selected:raise ValidationError('Select an invoice line once.')
        selected[key]=s.quantity(value)
    if not selected:raise ValidationError('Select at least one phone or accessory quantity.')
    target=warehouse or (original.warehouse if original.warehouse_id and original.warehouse.enabled else None) or Warehouse.objects.filter(branch=original.branch,enabled=True).order_by('code').first()
    if not target or not target.enabled or target.branch_id!=original.branch_id:raise ValidationError('Select an active receiving warehouse in this shop.')
    doc=s.new_doc('Sales Invoice',original.branch,customer=original.customer,seller=original.seller,is_return=True,reference=original,
        warehouse=target,notes=condition.strip()+'\n'+reason.strip(),demo_batch=original.demo_batch,
        data=dict(receipt=original.data.get('receipt',{}),returned_condition=condition.strip(),return_reason=reason.strip(),return_created_by=user.get_username()))
    for key,qty in selected.items():
        source=by_id[key]
        if qty>source.quantity:raise ValidationError('Return quantity exceeds the original invoice line.')
        Line.objects.create(document=doc,product=source.product,device=source.device,warehouse=target,quantity=qty,rate=source.rate,cost=source.cost,
            snapshot={**source.snapshot,'return_line':key})
    # Stale phone-only drafts remain reviewable; submission always checks actual remaining quantities.
    from .pricing import return_parts
    parts=[return_parts(by_id[str(line.snapshot['return_line'])],Decimal(0),line.quantity) for line in doc.lines.all()]
    if quantities:parts=[line.return_parts for line,source,amount,cost in plan(doc)]
    capture_totals(doc,parts)
    doc.save();s.audit(user,'Create return draft',doc,original=str(original.pk));return doc


@transaction.atomic
def apply_credit(credit,amount,user):
    from . import services as s
    credit=Document.objects.get(pk=credit.pk);s.require(user,credit.branch,'can_handle_payments')
    Branch.objects.select_for_update(no_key=True).get(pk=credit.branch_id)
    ids=[credit.pk,credit.reference_id]
    locked={doc.pk:doc for doc in Document.objects.select_for_update().filter(pk__in=[pk for pk in ids if pk]).order_by('pk')}
    credit=locked[credit.pk];original=locked.get(credit.reference_id)
    if credit.kind not in s.SELL or not credit.is_return or credit.status!='Submitted' or not original or original.status!='Submitted' or original.is_return or original.branch_id!=credit.branch_id or original.customer_id!=credit.customer_id:
        raise ValidationError('Apply a submitted return credit to its original customer invoice in the same shop.')
    amount=s.payment_money(amount)
    if amount<=0 or amount>original.outstanding or amount>-credit.outstanding:raise ValidationError('Credit application exceeds the remaining return credit or unpaid invoice balance.')
    doc=s.new_doc('Journal Entry',credit.branch,customer=credit.customer,reference=credit,posted_by=user,
        notes='Return credit '+credit.code+' applied to '+original.code,demo_batch=credit.demo_batch,data={'return_credit':str(credit.pk),'original_invoice':str(original.pk)})
    s.open_date(doc)
    s.entry(doc,'receivable',debit=amount,party=credit.customer,reference=credit)
    s.entry(doc,'receivable',credit=amount,party=credit.customer,reference=original)
    evidence=dict(amount=str(amount),journal=str(doc.pk),date=str(doc.posting_date),applied_by=user.get_username())
    original.data.setdefault('return_credit_applications',[]).append({**evidence,'reference_code':credit.code})
    credit.data.setdefault('credit_applications',[]).append({**evidence,'reference_code':original.code})
    original.outstanding-=amount;credit.outstanding+=amount;original.save();credit.save()
    doc.total=amount;doc.status='Submitted';doc.save();s.balanced(doc);s.audit(user,'Apply return credit',doc);return doc
