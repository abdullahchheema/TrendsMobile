"""Same-shop cash/bank movements, separate from customer or supplier payments."""
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from .models import *


def is_transfer(doc):return doc.kind=='Payment Entry' and doc.data.get('direction')=='Internal Transfer'

def allowed(user):
    from .access import staff
    person=staff(user)
    return person is None or person.manager or person.can_handle_payments

def methods(branch):
    config=ShopSettings.objects.filter(branch=branch).first()
    if not config:return {}
    accounts={str(a.pk):a for a in Account.objects.filter(pk__in=list(config.payment_methods.values()),enabled=True,is_group=False,account_type__in=['Cash','Bank'])}
    return {name:accounts[str(pk)] for name,pk in config.payment_methods.items() if str(pk) in accounts}

def resolve(branch,source,target,lock=False):
    choices=methods(branch)
    if not isinstance(source,str) or not isinstance(target,str) or source not in choices or target not in choices:raise ValidationError('Select active payment methods configured for this shop.')
    first,last=choices[source],choices[target]
    if first.pk==last.pk:raise ValidationError('These methods use the same account. Select two different accounts.')
    if first.account_type==last.account_type=='Cash':raise ValidationError('Cash-to-cash custody needs a separate till review. Use cash/bank or bank/bank accounts here.')
    if lock:
        accounts={a.pk:a for a in Account.objects.select_for_update().filter(pk__in=[first.pk,last.pk]).order_by('pk')}
        first,last=accounts[first.pk],accounts[last.pk]
        if not first.enabled or not last.enabled or first.is_group or last.is_group or {first.account_type,last.account_type}-{'Cash','Bank'}:raise ValidationError('Use active Cash or Bank posting accounts.')
    return first,last

def validate(values,user,branch):
    from . import services as s
    s.require(user,branch,'can_handle_payments')
    if not branch.enabled:raise ValidationError('Select an active shop.')
    source,target=resolve(branch,values.get('source_method'),values.get('target_method'))
    amount=s.payment_money(values.get('amount'))
    if amount<=0:raise ValidationError('Transfer amount must be positive.')
    if not str(values.get('transfer_reference','')).strip() or not str(values.get('notes','')).strip():raise ValidationError('Record the actual transaction reference and transfer reason.')
    scope=values.get('cash_scope','till') if 'Cash' in (source.account_type,target.account_type) else 'bank'
    if scope not in ('till','shop','bank') or (scope=='bank' and 'Cash' in (source.account_type,target.account_type)):raise ValidationError('Select the cash custody for this transfer.')
    if scope=='shop':
        if not s.manager(user):raise PermissionDenied('Only a manager can record shop cash outside POS tills.')
        if values.get('outside_till_confirmed') is not True:raise ValidationError('Confirm this cash is outside every POS till.')
    return source,target,amount,scope

@transaction.atomic
def save(values,user,doc=None):
    from . import services as s
    branch=values['branch'];s.require(user,branch,'can_handle_payments')
    branch=Branch.objects.select_for_update(no_key=True).get(pk=branch.pk)
    if doc:
        doc=Document.objects.select_for_update().get(pk=doc.pk)
        if not is_transfer(doc) or doc.status!='Draft' or doc.source or doc.branch_id!=branch.pk:raise ValidationError('Edit a new draft internal transfer in its original shop.')
    source,target,amount,scope=validate(values,user,branch)
    if not doc:doc=s.new_doc('Payment Entry',branch,data={'direction':'Internal Transfer'})
    shift=s.active_shift(branch,user) if scope=='till' else None
    doc.posting_date=values['posting_date'];doc.handler=values['handler'];doc.notes=values['notes'].strip();doc.total=amount
    doc.data.update(direction='Internal Transfer',source_method=values['source_method'],target_method=values['target_method'],
        source_account=str(source.pk),target_account=str(target.pk),amount=str(amount),cash_scope=scope,
        outside_till_confirmed=values.get('outside_till_confirmed') is True,transfer_reference=values['transfer_reference'].strip(),
        source_name=str(source),target_name=str(target),expected_shift=str(shift.pk) if shift else None,
        expected_till=str(shift.till) if shift and shift.till_id else None)
    s.assigned(doc,'handler','can_handle_payments')
    doc.demo_batch=doc.demo_batch or doc.handler.demo_batch or branch.demo_batch
    doc.save();s.audit(user,'Save internal transfer draft',doc);return doc

def post(doc):
    from . import services as s
    if doc.source:raise ValidationError('Imported transfer history is read-only; use a reviewed new movement.')
    if doc.customer_id or doc.supplier_id or doc.is_return or doc.lines.exists():raise ValidationError('An internal transfer cannot include a party, invoice items or a return.')
    branch=Branch.objects.get(pk=doc.branch_id)
    source,target,amount,scope=validate({**doc.data,'notes':doc.notes},doc.posted_by,branch)
    source,target=resolve(branch,doc.data['source_method'],doc.data['target_method'],lock=True)
    if (str(source.pk),str(target.pk))!=(doc.data.get('source_account'),doc.data.get('target_account')):raise ValidationError('Payment account mappings changed. Edit and save the draft to review them.')
    s.assigned(doc,'handler','can_handle_payments')
    if doc.reference_id:
        original=Document.objects.select_for_update().get(pk=doc.reference_id)
        if not is_transfer(original) or original.status!='Submitted' or original.source or original.branch_id!=doc.branch_id or doc.data.get('reverse_of')!=str(original.pk):raise ValidationError('Link the original submitted internal transfer for a reverse movement.')
        if (str(source.pk),str(target.pk),amount)!=(original.data.get('target_account'),original.data.get('source_account'),original.total):raise ValidationError('A reverse transfer must use the original accounts in reverse and the same amount.')
        if original.linked.filter(kind='Payment Entry',status='Submitted',data__direction='Internal Transfer').exclude(pk=doc.pk).exists():raise ValidationError('This transfer already has a posted reverse movement.')
    elif doc.data.get('reverse_of'):raise ValidationError('The original reverse-transfer reference is missing.')
    doc.pos_shift=None;doc.till=None;net=s.ZERO
    if scope=='till':
        shift=s.active_shift(branch,doc.posted_by)
        if not shift:raise ValidationError('Start your counted POS shift before transferring cash to or from its till.')
        shift=Document.objects.select_for_update().get(pk=shift.pk)
        if doc.data.get('expected_shift') and doc.data['expected_shift']!=str(shift.pk):raise ValidationError('Your till shift changed since this draft was saved. Edit and save it again to review the custody.')
        if shift.state!='Open' or shift.status!='Submitted' or not shift.till_id or 'opening_float' not in shift.data:raise ValidationError('Use an open reconciled shift; review legacy shifts first.')
        if shift.handler_id!=doc.handler_id:raise ValidationError('The transfer handler must be the current till cashier.')
        if shift.posting_date!=doc.posting_date:raise ValidationError('Till transfers must use the open shift date.')
        if source.account_type=='Cash' and amount>s.money(s.till_balance(shift)['expected']):raise ValidationError('The transfer exceeds the cash available in this till.')
        net=amount if target.account_type=='Cash' else -amount;doc.pos_shift=shift;doc.till=shift.till
    doc.data.update(source_name=str(source),target_name=str(target),source_type=source.account_type,target_type=target.account_type,
        cash_scope=scope,till_cash=str(net),unallocated='0.00',mode=doc.data['source_method']+' → '+doc.data['target_method'],
        recorded_by=doc.posted_by.get_username())
    doc.data.pop('cash_tendered',None);doc.data.pop('change_returned',None)
    s.entry(doc,target,debit=amount);s.entry(doc,source,credit=amount)
    doc.total=amount;doc.outstanding=s.ZERO;doc.state='Transferred'

@transaction.atomic
def reverse(original,user):
    from . import services as s
    original=Document.objects.get(pk=original.pk);s.require(user,original.branch,'can_handle_payments')
    Branch.objects.select_for_update(no_key=True).get(pk=original.branch_id)
    original=Document.objects.select_for_update().get(pk=original.pk)
    if not is_transfer(original) or original.status!='Submitted' or original.source:raise ValidationError('Choose a new submitted internal transfer to reverse.')
    previous=original.linked.filter(kind='Payment Entry',data__direction='Internal Transfer').exclude(status='Cancelled').first()
    if previous:
        if previous.status=='Draft':return previous
        raise ValidationError('This transfer already has a posted reverse movement.')
    mapping=methods(original.branch)
    source=next((name for name,account in mapping.items() if str(account.pk)==original.data.get('target_account')),None)
    target=next((name for name,account in mapping.items() if str(account.pk)==original.data.get('source_account')),None)
    if not source or not target:raise ValidationError('Original accounts are no longer configured for this shop. An owner must review the mapping.')
    shift=s.active_shift(original.branch,user);person=s.staff(user)
    handler=shift.handler if shift else person if person and person.can_handle_payments else None
    doc=s.new_doc('Payment Entry',original.branch,reference=original,handler=handler,total=original.total,notes='Reverse physical movement for '+original.code,
        data=dict(direction='Internal Transfer',source_method=source,target_method=target,source_account=original.data['target_account'],target_account=original.data['source_account'],
            amount=str(original.total),cash_scope=original.data.get('cash_scope','till'),outside_till_confirmed=False,transfer_reference='',reverse_of=str(original.pk)))
    s.audit(user,'Create reverse internal transfer draft',doc,original=str(original.pk));return doc
