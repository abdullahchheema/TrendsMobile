"""The simple money layer: one transaction history, customer and supplier dues, receipts, payments, expenses and cash/bank.

Everything is read from, and posted to, the existing double-entry ledger, so balances always reconcile. All functions work on
a shop (or the shops the user may see) and never cross shops for a shop login."""
from collections import defaultdict
from datetime import date,timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from .models import Account,Branch,Document,LedgerEntry,Party,ShopSettings
from . import access,fund_transfers as funds,shopdefaults,vouchers,services as s

ZERO=Decimal('0.00')
TYPES=['Sale payment','Customer payment','Customer refund','Supplier payment','Supplier refund','Expense','Owner funds','Owner drawings','Other income','Loan','Transfer','Cash difference','Reversal','Other']
CASH_ISH=('Cash','Bank')


# ------------------------------------------------------------------ accounts and methods
def method_map(branches):
    """{account id: (branch, method name)} for every payment method of the given shops."""
    out={}
    for config in ShopSettings.objects.filter(branch__in=branches).select_related('branch'):
        for name,pk in config.payment_methods.items():out[str(pk)]=(config.branch,name)
    return out


def balances(branches):
    """Current balance of every cash / bank / wallet account of the given shops."""
    mapped=method_map(branches);accounts={str(a.pk):a for a in Account.objects.filter(pk__in=list(mapped))}
    totals={str(r['account_id']):(r['d'] or ZERO)-(r['c'] or ZERO) for r in LedgerEntry.objects.filter(account_id__in=list(mapped),branch__in=branches).values('account_id').annotate(d=Sum('debit'),c=Sum('credit'))}
    rows=[dict(branch=branch,method=name,account=accounts.get(pk),amount=totals.get(pk,ZERO),kind=accounts[pk].account_type if pk in accounts else '') for pk,(branch,name) in mapped.items()]
    return sorted(rows,key=lambda r:(r['branch'].name,r['kind']!='Cash',r['method']))


# ------------------------------------------------------------------ transaction history
def classify(doc):
    d=doc.data;kind=doc.kind
    if kind=='Payment Entry':
        direction=d.get('direction')
        if direction=='Internal Transfer':return 'Transfer'
        if doc.customer_id:
            if direction=='Receive':
                ref=doc.reference
                return 'Sale payment' if ref is not None and ref.kind in s.SELL and ref.posting_date==doc.posting_date and not ref.is_return else 'Customer payment'
            return 'Customer refund'
        if doc.supplier_id:return 'Supplier payment' if direction=='Pay' else 'Supplier refund'
        return 'Other'
    if kind=='Journal Entry' and d.get('online_settlement'):return 'Online Store settlement'
    if kind=='Voucher':
        return {'expense':'Expense','capital':'Owner funds','drawings':'Owner drawings','income':'Other income','loan_in':'Loan','loan_out':'Loan'}.get(d.get('type'),'Other')
    if kind=='Mobile Daily Closing':return 'Cash difference'
    if kind=='Journal Entry' and doc.notes.startswith('Cancellation reversal'):return 'Reversal'
    return 'Other'


def describe(doc):
    d=doc.data
    if doc.kind=='Voucher':
        bits=[d.get('account_name') or d.get('label') or '',d.get('description') or doc.notes]
        return ' · '.join(b for b in bits if b)
    if doc.kind=='Payment Entry' and d.get('direction')=='Internal Transfer':return doc.notes or 'Move between accounts'
    if doc.kind=='Payment Entry':
        extra=f'Against {doc.reference.code}' if doc.reference_id else 'On account'
        return ' · '.join(b for b in (doc.notes,extra,d.get('reference','')) if b)
    return doc.notes or doc.kind


def history(branches,start=None,end=None,kind='',method='',search=''):
    """Every movement on the shop's cash, bank and wallet accounts, newest first, with received/paid columns and totals.
    Cancelled records stay visible together with their reversal, so the running money always matches the accounts;
    moves between a shop's own accounts show once and are not counted as money in or out."""
    mapped=method_map(branches)
    entries=LedgerEntry.objects.filter(account_id__in=list(mapped),branch__in=branches).select_related('document','document__customer','document__supplier','document__reference','branch')
    if start:entries=entries.filter(posting_date__gte=start)
    if end:entries=entries.filter(posting_date__lte=end)
    rows=[]
    for e in entries.order_by('-posting_date','-document__created','-pk'):
        doc=e.document;_,name=mapped[str(e.account_id)];party=doc.customer or doc.supplier
        label=classify(doc)
        if label=='Transfer':
            if e.credit>0:continue         # one row per transfer: show it from the receiving leg
            row_method=f"{doc.data.get('source_method','')} → {doc.data.get('target_method',name)}";received=paid=ZERO
        else:row_method=name;received=e.debit;paid=e.credit
        rows.append(dict(date=e.posting_date,code=doc.code,doc=doc,type=label,description=describe(doc),party=party.name if party else '',received=received,paid=paid,moved=e.debit if label=='Transfer' else ZERO,
                         method=row_method,status='Cancelled' if doc.status=='Cancelled' else 'Posted',shop=e.branch.name,linkable=doc.branch_id==e.branch_id))      # a payment the Online Store made to this shop is a record in the Online Store's books: shown here, but the shop cannot open it
    if kind:rows=[r for r in rows if r['type']==kind]
    if method:rows=[r for r in rows if method in r['method'].split(' → ')]
    if search:
        q=search.casefold();rows=[r for r in rows if q in ' '.join((r['code'],r['description'],r['party'],r['type'])).casefold()]
    totals=dict(received=sum((r['received'] for r in rows),ZERO),paid=sum((r['paid'] for r in rows),ZERO))
    return rows,totals


# ------------------------------------------------------------------ dues
def _party_rows(branches,kind):
    key='receivable' if kind=='Customer' else 'payable'
    rows=[]
    for config in ShopSettings.objects.filter(branch__in=branches).select_related('branch'):
        account=config.accounts.get(key)
        if not account:continue
        owed={r['party_id']:(r['d'] or ZERO)-(r['c'] or ZERO) for r in LedgerEntry.objects.filter(branch=config.branch,account_id=account,party__isnull=False).values('party_id').annotate(d=Sum('debit'),c=Sum('credit'))}
        if kind=='Supplier':owed={k:-v for k,v in owed.items()}
        parties={p.pk:p for p in Party.objects.filter(pk__in=list(owed),kind=kind)}
        docs=Document.objects.filter(branch=config.branch,status='Submitted',is_return=False,kind__in=list(s.SELL) if kind=='Customer' else ['Purchase Invoice'])
        docs=docs.filter(customer__in=parties) if kind=='Customer' else docs.filter(supplier__in=parties)
        billed=defaultdict(lambda:ZERO);due=defaultdict(lambda:None)
        for doc in docs.values('customer_id','supplier_id','total','outstanding','due_date'):
            pid=doc['customer_id'] if kind=='Customer' else doc['supplier_id']
            billed[pid]+=doc['total']
            if doc['outstanding']>0 and doc['due_date'] and (due[pid] is None or doc['due_date']<due[pid]):due[pid]=doc['due_date']
        today=timezone.localdate()
        for pid,remaining in owed.items():
            party=parties.get(pid)
            if party is None or remaining==0:continue
            # remaining is "what the customer owes the shop" / "what the shop owes the supplier"; a negative number is the other way round
            # (a customer who paid in advance or returned goods; a supplier who was paid too much or took goods back).
            theirs=remaining>0 if kind=='Customer' else remaining<0
            rows.append(dict(party=party,branch=config.branch,billed=billed[pid],received=billed[pid]-remaining if remaining>0 else billed[pid],remaining=remaining,due=due[pid],overdue=bool(due[pid] and due[pid]<today and remaining>0),
                             owes_you=abs(remaining) if theirs else ZERO,you_owe=ZERO if theirs else abs(remaining)))
    return sorted(rows,key=lambda r:(-abs(r['remaining']),r['party'].name))


def customer_dues(branches):return [r for r in _party_rows(branches,'Customer') if r['remaining']>0]
def supplier_dues(branches):return [r for r in _party_rows(branches,'Supplier') if r['remaining']>0]
def customer_balances(branches):return _party_rows(branches,'Customer')     # who owes the shop, and whom the shop owes (advance, returned goods)
def supplier_balances(branches):return _party_rows(branches,'Supplier')     # whom the shop owes, and who owes the shop (overpaid, returned goods)


def payment_history(branches,kind,search='',party=None):
    """Every payment with customers (or suppliers), newest first, said in plain words: paid with the sale, clearing an older due,
    paid in advance / on account, refund. Cancelled payments stay visible."""
    side='customer' if kind=='Customer' else 'supplier'
    docs=Document.objects.filter(branch__in=branches,kind='Payment Entry',**{side+'__isnull':False}).exclude(data__direction='Internal Transfer').select_related(side,'reference','branch')
    if party:docs=docs.filter(**{side:party})
    rows=[]
    for d in docs.order_by('-posting_date','-created'):
        direction=d.data.get('direction');ref=d.reference
        if kind=='Customer':
            if direction=='Receive':
                if ref is None:label='Advance / on account'
                elif ref.is_return:label='Against a return credit'
                elif ref.posting_date==d.posting_date:label='Paid with the sale'
                else:label='Clearing an older due'
            else:label='Refund to customer'
        else:
            if direction=='Pay':label='Paid against a bill' if ref else 'Advance / on account'
            else:label='Refund from supplier'
        received=direction=='Receive'
        rows.append(dict(doc=d,date=d.posting_date,code=d.code,party=getattr(d,side),shop=d.branch.name,label=label,method=d.data.get('mode',''),against=ref.code if ref else '',amount=d.total,
                         money_in=received,status='Cancelled' if d.status=='Cancelled' else 'Posted',note=d.notes))
    if search:
        q=search.casefold();rows=[r for r in rows if q in ' '.join((r['code'],r['party'].name,r['label'],r['against'],r['method'],r['note'])).casefold()]
    return rows


def party_detail(party):
    """Invoices and payment history for one customer or supplier, in their own shop."""
    customer=party.kind=='Customer'
    docs=Document.objects.filter(branch=party.branch,status='Submitted',kind__in=list(s.SELL) if customer else ['Purchase Invoice'],**({'customer':party} if customer else {'supplier':party})).order_by('-posting_date','-created')
    payments=Document.objects.filter(branch=party.branch,kind='Payment Entry',status='Submitted',**({'customer':party} if customer else {'supplier':party})).exclude(data__direction='Internal Transfer').select_related('reference').order_by('-posting_date','-created')
    key='receivable' if customer else 'payable';config=ShopSettings.objects.filter(branch=party.branch).first()
    net=LedgerEntry.objects.filter(branch=party.branch,account_id=(config.accounts.get(key) if config else None),party=party).aggregate(d=Sum('debit'),c=Sum('credit'))
    remaining=(net['d'] or ZERO)-(net['c'] or ZERO)
    if not customer:remaining=-remaining
    invoices=list(docs[:100])
    for d in invoices:d.paid=d.total-d.outstanding
    return dict(invoices=invoices,payments=list(payments[:100]),remaining=remaining,owes_you=max(remaining,ZERO) if customer else max(-remaining,ZERO),you_owe=max(-remaining,ZERO) if customer else max(remaining,ZERO),billed=sum((d.total for d in docs if not d.is_return),ZERO))


# ------------------------------------------------------------------ posting
def _method(branch,method):
    config=ShopSettings.objects.filter(branch=branch).first()
    if not config or method not in config.payment_methods:raise ValidationError('Choose a payment method set up for this shop.')


def _enough(branch,method,amount):
    """Money cannot leave an account that does not hold it."""
    config=ShopSettings.objects.filter(branch=branch).first();account=Account.objects.get(pk=config.payment_methods[method])
    available=s.account_balance(branch,account)
    if available<amount:raise ValidationError(f'{method} has only PKR {available:,.2f} recorded for this shop, so PKR {amount:,.2f} cannot be paid from it.')


def _lock_shop(user,branch):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=branch.pk);access.require(user,branch,'can_handle_payments');return branch


@transaction.atomic
def record_party_payment(user,party,amount,method,when,reference='',notes=''):
    """Take money from a customer, or pay a supplier, against their oldest unpaid bills first; anything left over stays on account.
    The remaining balance updates by itself because the ledger is the source."""
    if not party.branch_id:raise ValidationError('This record belongs to no shop yet.')
    branch=_lock_shop(user,party.branch);_method(branch,method)
    amount=s.payment_money(amount)
    if amount<=0:raise ValidationError('Enter an amount greater than zero.')
    customer=party.kind=='Customer';direction='Receive' if customer else 'Pay'
    if not customer:_enough(branch,method,amount)
    me=shopdefaults.person(user,branch);left=amount;made=[]
    bills=Document.objects.select_for_update().filter(branch=branch,status='Submitted',is_return=False,outstanding__gt=0,kind__in=list(s.SELL) if customer else ['Purchase Invoice'],**({'customer':party} if customer else {'supplier':party})).order_by('posting_date','created')
    def pay(part,bill=None):
        doc=s.new_doc('Payment Entry',branch,posting_date=when,handler=me,reference=bill,notes=notes,**({'customer':party} if customer else {'supplier':party}),
                      data=dict(direction=direction,mode=method,amount=str(part),reference=reference))
        made.append(s.submit(doc,user))
    for bill in list(bills):
        if left<=0:break
        part=min(left,bill.outstanding);pay(part,bill);left-=part
    if left>0:pay(left)
    s.audit(user,'Customer payment received' if customer else 'Supplier payment made',made[-1],branch=branch,party=party.name,amount=str(amount))
    return made[-1]


@transaction.atomic
def record_party_cash_back(user,party,amount,method,when,reference='',notes=''):
    """The opposite direction: refund a customer, or receive a refund from a supplier.

    The refund settles the party's open return credit notes (oldest first), so a credit can never be refunded twice or
    refunded after it was applied to an invoice. It can never be more than the shop really owes the customer (or the
    supplier owes the shop)."""
    if not party.branch_id:raise ValidationError('This record belongs to no shop yet.')
    branch=_lock_shop(user,party.branch);_method(branch,method)
    amount=s.payment_money(amount)
    if amount<=0:raise ValidationError('Enter an amount greater than zero.')
    customer=party.kind=='Customer'
    if customer:_enough(branch,method,amount)
    kinds=list(s.SELL) if customer else ['Purchase Invoice'];side={('customer' if customer else 'supplier'):party}
    notes_open=list(Document.objects.select_for_update().filter(branch=branch,status='Submitted',kind__in=kinds,is_return=True,outstanding__lt=0,**side).order_by('posting_date','created'))
    usable=[n for n in notes_open if not (customer and n.reference_id and Document.objects.get(pk=n.reference_id).outstanding>0)]   # credit is refundable once the original invoice is paid
    advances=[a for a in Document.objects.select_for_update().filter(branch=branch,status='Submitted',kind='Payment Entry',reference__isnull=True,**side).order_by('posting_date','created')
              if a.data.get('direction')==('Receive' if customer else 'Pay') and s.money(a.data.get('unallocated'))>0]
    prepaid=sum((s.money(a.data.get('unallocated')) for a in advances),ZERO)
    refundable=sum((-n.outstanding for n in usable),ZERO)+prepaid
    if amount>refundable:
        raise ValidationError(('This is more than the customer can be refunded' if customer else 'This is more than the supplier owes you')+f' ({refundable:,.2f}). Apply any return credit to the unpaid invoice first.')
    left=amount;done=None;me=shopdefaults.person(user,branch)
    def post(ref,part):
        doc=s.new_doc('Payment Entry',branch,posting_date=when,handler=me,notes=notes,reference=ref,**({'customer':party} if customer else {'supplier':party}),
                      data=dict(direction='Pay' if customer else 'Receive',mode=method,amount=str(part),reference=reference))
        return s.submit(doc,user)
    for note in usable:
        if left<=0:break
        part=min(left,-note.outstanding);done=post(note,part);left-=part
    if left>0:                                                  # an advance being given back: use it up so it cannot be refunded twice
        done=post(None,left);done.data['unallocated']='0.00';done.save(update_fields=['data','modified']);take=left
        for adv in advances:
            if take<=0:break
            use=min(take,s.money(adv.data.get('unallocated')));adv.data['unallocated']=str(s.money(adv.data.get('unallocated'))-use);adv.save(update_fields=['data','modified']);take-=use
    s.audit(user,'Customer refund paid' if customer else 'Supplier refund received',done,branch=branch,party=party.name,amount=str(amount));return done


@transaction.atomic
def record_voucher(user,branch,kind,account,amount,method,when,description,reference='',channel='Shop'):
    branch=Branch.objects.select_for_update(no_key=True).get(pk=branch.pk);access.require(user,branch)
    return vouchers.post(dict(branch=branch,type=kind,posting_date=when,amount=amount,account=str(account.pk),mode=method,reference=reference,description=description,channel=channel),user)


@transaction.atomic
def move_money(user,branch,source,target,amount,when,reference='',notes=''):
    """Move money between the shop's own accounts (cash, bank, Easypaisa, JazzCash). Never counted as income or expense."""
    branch=_lock_shop(user,branch);me=shopdefaults.person(user,branch)
    choices=funds.methods(branch)
    involves_cash=any(choices[name].account_type=='Cash' for name in (source,target) if name in choices)
    shift=s.active_shift(branch,user) if involves_cash else None
    values=dict(branch=branch,source_method=source,target_method=target,amount=amount,posting_date=when,handler=me,transfer_reference=reference or 'Internal move',notes=notes or f'Moved from {source} to {target}',
                cash_scope=('till' if shift else 'shop') if involves_cash else 'bank',outside_till_confirmed=True if involves_cash and not shift else False)
    doc=funds.save(values,user);return s.submit(doc,user)
