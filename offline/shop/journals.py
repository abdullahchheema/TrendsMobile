"""Owner-reviewed accounting reclassifications with immutable linked corrections."""
from copy import deepcopy
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from .models import Account,Branch,Document,ShopSettings,SalesTaxRule
from .setup import owner

TYPES=['Income Account','Expense Account','Equity','Current Asset','Current Liability']


def is_manual(doc):
    return doc.kind=='Journal Entry' and isinstance(doc.data.get('manual_journal'),dict) and doc.data['manual_journal'].get('version')==1


def accounts(branch):
    """Control accounts stay under the transactions that reconcile their subledgers."""
    blocked=set();income_expense=[]
    for config in ShopSettings.objects.all():
        blocked.update(str(value) for key,value in config.accounts.items() if key not in ('sales','expense'))
        blocked.update(str(value) for value in config.payment_methods.values())
        if config.branch_id==branch.pk:income_expense=[str(config.accounts[key]) for key in ('sales','expense') if config.accounts.get(key)]
        else:blocked.update(str(config.accounts[key]) for key in ('sales','expense') if config.accounts.get(key))
    blocked.update(str(pk) for pk in SalesTaxRule.objects.values_list('account_id',flat=True))
    return Account.objects.filter(Q(account_type__in=TYPES)|Q(pk__in=income_expense),enabled=True,is_group=False).exclude(pk__in=blocked).exclude(account_type__in=['Cash','Bank','Receivable','Payable','Stock','Stock Received But Not Billed','Cost of Goods Sold','Tax']).order_by('name','code')


def normalize(branch,rows):
    from . import services as s
    if not isinstance(rows,list) or not 2<=len(rows)<=30:raise ValidationError('Enter between 2 and 30 journal rows.')
    choices={str(account.pk):account for account in accounts(branch)}
    result=[];seen=set();debit=credit=s.ZERO
    for row in rows:
        if not isinstance(row,dict):raise ValidationError('Invalid journal row.')
        key=str(row.get('account',''));account=choices.get(key)
        if not account:raise ValidationError('Select an active adjustment account. Cash, bank, stock, party, clearing and output-tax accounts need their dedicated workflows.')
        if key in seen:raise ValidationError('Use each account only once; combine repeated account rows.')
        seen.add(key)
        dr=s.payment_money(row.get('debit','0'));cr=s.payment_money(row.get('credit','0'))
        if dr<0 or cr<0 or (dr>0)==(cr>0):raise ValidationError('Each journal row needs a positive debit or a positive credit, never both.')
        memo=row.get('memo','')
        if not isinstance(memo,str) or len(memo)>180:raise ValidationError('Journal row notes must be text up to 180 characters.')
        result.append(dict(account=key,code=account.code,name=account.name,account_type=account.account_type,debit=str(dr),credit=str(cr),memo=memo.strip()))
        debit+=dr;credit+=cr
    if debit!=credit or debit<=0:raise ValidationError('Journal debits and credits must balance to the same positive total.')
    s.money(debit)
    return result,debit


def validate(branch,rows,reference,reason):
    if not branch.enabled:raise ValidationError('Select an active shop.')
    if not isinstance(reference,str) or not reference.strip() or len(reference)>180:raise ValidationError('Record a supporting reference up to 180 characters.')
    if not isinstance(reason,str) or not reason.strip():raise ValidationError('Explain the accounting adjustment or correction.')
    return normalize(branch,rows)


def signature(rows):return sorted((row['account'],row['debit'],row['credit']) for row in rows)


def posted_original(doc):
    if not doc.reference_id:raise ValidationError('The original journal reference is missing.')
    original=Document.objects.select_for_update().get(pk=doc.reference_id)
    if not is_manual(original) or original.source or original.status!='Submitted' or original.branch_id!=doc.branch_id or original.data['manual_journal'].get('reversal_of'):
        raise ValidationError('Choose an original submitted owner adjustment in this shop.')
    actual=sorted((str(row.account_id),str(row.debit),str(row.credit)) for row in original.ledger.all())
    if signature(original.data['manual_journal'].get('lines',[]))!=actual:raise ValidationError('The original journal evidence does not match its ledger. Review it before correction.')
    return original


def correction(doc,rows):
    spec=doc.data['manual_journal'];reverse=spec.get('reversal_of');replace=spec.get('replacement_for')
    if reverse and replace:raise ValidationError('A journal cannot be both a reversal and a replacement.')
    if not reverse and not replace:
        if doc.reference_id:raise ValidationError('Use the dedicated correction action to link another journal.')
        return
    original=posted_original(doc)
    if str(original.pk)!=(reverse or replace):raise ValidationError('The correction does not match its original journal.')
    existing=original.linked.filter(kind='Journal Entry',status='Submitted').exclude(pk=doc.pk)
    if reverse:
        expected=[{**row,'debit':row['credit'],'credit':row['debit']} for row in original.data['manual_journal']['lines']]
        if signature(rows)!=signature(expected):raise ValidationError('A reversal must use the exact original accounts and amounts in reverse.')
        if existing.filter(data__manual_journal__reversal_of=reverse).exists():raise ValidationError('This journal already has a posted reversal.')
    else:
        if not existing.filter(data__manual_journal__reversal_of=replace).exists():raise ValidationError('Submit the original journal’s reversal before its replacement.')
        if existing.filter(data__manual_journal__replacement_for=replace).exists():raise ValidationError('This journal already has a posted replacement.')


@transaction.atomic
def save(values,user,doc=None):
    from . import services as s
    owner(user)
    branch=Branch.objects.select_for_update(no_key=True).get(pk=values['branch'].pk)
    if doc:
        doc=Document.objects.select_for_update(no_key=True).get(pk=doc.pk)
        if not is_manual(doc) or doc.source or doc.status!='Draft' or doc.branch_id!=branch.pk:raise ValidationError('Edit a new draft journal in its original shop.')
    rows,total=validate(branch,values['rows'],values['supporting_reference'],values['notes'])
    if not doc:doc=s.new_doc('Journal Entry',branch,data={'manual_journal':{'version':1}})
    doc.data['manual_journal'].update(lines=rows,supporting_reference=values['supporting_reference'].strip())
    correction(doc,rows)
    doc.posting_date=values['posting_date'];doc.notes=values['notes'].strip();doc.total=total;doc.demo_batch=doc.demo_batch or branch.demo_batch
    doc.save();s.audit(user,'Save owner journal draft',doc);return doc


def post(doc):
    from . import services as s
    owner(doc.posted_by)
    if not is_manual(doc) or doc.source:raise ValidationError('Use a new owner journal draft. Imported and system journals cannot be submitted here.')
    if doc.customer_id or doc.supplier_id or doc.buyer_id or doc.seller_id or doc.handler_id or doc.till_id or doc.pos_shift_id or doc.is_return or doc.lines.exists() or doc.ledger.exists() or doc.stock.exists():raise ValidationError('Owner journals cannot include parties, invoice items, stock or till movements.')
    spec=doc.data['manual_journal'];rows,total=validate(doc.branch,spec.get('lines'),spec.get('supporting_reference'),doc.notes)
    correction(doc,rows)
    # Serialize account setup changes with posting and recheck the permitted roles.
    list(Account.objects.select_for_update().filter(pk__in=[row['account'] for row in rows]).order_by('pk'))
    rows,total=normalize(doc.branch,rows)
    if any(before.get('code')!=after['code'] or before.get('account_type')!=after['account_type'] for before,after in zip(spec['lines'],rows)):
        raise ValidationError('An account code or type changed. Edit and save this journal to review the accounts again.')
    for row in rows:s.entry(doc,Account.objects.get(pk=row['account']),debit=row['debit'],credit=row['credit'])
    spec.update(lines=rows,recorded_by=doc.posted_by.get_username(),posted_at=timezone.now().isoformat())
    doc.total=total;doc.outstanding=s.ZERO;doc.state='Reversal' if spec.get('reversal_of') else 'Replacement' if spec.get('replacement_for') else 'Posted'


@transaction.atomic
def prepare(original,user,replacement=False):
    from . import services as s
    owner(user)
    original=Document.objects.get(pk=original.pk)
    Branch.objects.select_for_update(no_key=True).get(pk=original.branch_id)
    original=Document.objects.select_for_update(no_key=True).get(pk=original.pk)
    probe=Document(branch=original.branch,reference=original);posted_original(probe)
    mode='replacement_for' if replacement else 'reversal_of'
    linked=original.linked.filter(kind='Journal Entry')
    existing=linked.filter(**{'data__manual_journal__'+mode:str(original.pk)}).exclude(status='Cancelled').first()
    if existing:
        if existing.status=='Draft':return existing
        raise ValidationError('This journal already has a posted '+('replacement.' if replacement else 'reversal.'))
    if replacement and not linked.filter(status='Submitted',data__manual_journal__reversal_of=str(original.pk)).exists():raise ValidationError('Post the reversal before preparing a replacement.')
    rows=deepcopy(original.data['manual_journal']['lines'])
    if not replacement:rows=[{**row,'debit':row['credit'],'credit':row['debit']} for row in rows]
    doc=s.new_doc('Journal Entry',original.branch,reference=original,total=original.total,demo_batch=original.demo_batch,
        notes=('Replacement for ' if replacement else 'Reversal of ')+original.code,
        data={'manual_journal':dict(version=1,lines=rows,supporting_reference='',**{mode:str(original.pk)})})
    s.audit(user,'Prepare journal correction',doc,original=str(original.pk),mode=mode);return doc
