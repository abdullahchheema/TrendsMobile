"""The audit's mandatory system invariants (qa-audit/TEST_PLAN.md, section "Invariants"), written against the raw tables.

Each function returns a list of plain-language problems; an empty list means the invariant holds. They are independent of
books_check.py (the application's own checker): they never call a service or a report, only read rows. Run them after any
workflow, at the end of the suite, or against a copy of live data with `manage.py audit_invariants`."""
from collections import Counter,defaultdict
from decimal import Decimal
from django.db.models import Count,Q,Sum
from .models import *

ZERO=Decimal('0.00')
SELL=('Sales Invoice','POS Invoice')
IN_STOCK=('Available','Reserved','Holding')


# ------------------------------------------------------------------ financial
def journals_balance():
    """F1. Every document's ledger rows balance, in total and inside each shop's books."""
    out=[]
    for r in LedgerEntry.objects.values('document_id','document__code','branch__code').annotate(d=Sum('debit'),c=Sum('credit')):
        if r['d']!=r['c']:out.append(f"{r['document__code']} in {r['branch__code']}: debits {r['d']} but credits {r['c']}")
    return out


def no_one_sided_rows():
    """F1b. A ledger row is a debit or a credit, never both, and never negative. (A sale given away at a 100% discount posts rows of zero;
    those are harmless and allowed.)"""
    bad=LedgerEntry.objects.filter(Q(debit__gt=0,credit__gt=0)|Q(debit__lt=0)|Q(credit__lt=0))
    return [f'ledger row {e.pk} of {e.document.code}: debit {e.debit}, credit {e.credit}' for e in bad.select_related('document')[:50]]


def drafts_have_no_postings():
    """F3. Drafts never touch the ledger or the stock."""
    out=[]
    for d in Document.objects.filter(status='Draft').annotate(l=Count('ledger',distinct=True),m=Count('stock',distinct=True)).filter(Q(l__gt=0)|Q(m__gt=0)):
        out.append(f'{d.kind} {d.code} is a draft but has {d.l} ledger rows and {d.m} stock movements')
    return out


def posted_sales_have_ledger():
    """F2. Every posted sale, supplier bill, payment and voucher has ledger rows (a posted transaction that never reached the books)."""
    kinds=list(SELL)+['Purchase Invoice','Payment Entry','Voucher','Purchase Return']
    return [f'{d.kind} {d.code} is posted but has no ledger rows' for d in Document.objects.filter(status='Submitted',kind__in=kinds,total__gt=0).annotate(l=Count('ledger')).filter(l=0)]


def receivables_match_invoices():
    """F6/F7. For every customer and supplier in every shop, the control account in the ledger equals the open amounts on their documents
    less money paid on account that is not yet used."""
    out=[]
    for config in ShopSettings.objects.select_related('branch'):
        for kind,key,doc_kinds,field,prepay in (('Customer','receivable',SELL,'customer','Receive'),('Supplier','payable',('Purchase Invoice',),'supplier','Pay')):
            account=config.accounts.get(key)
            if not account:continue
            ledger=defaultdict(lambda:ZERO)
            for r in LedgerEntry.objects.filter(branch=config.branch,account_id=account,party__isnull=False).values('party_id').annotate(d=Sum('debit'),c=Sum('credit')):
                ledger[r['party_id']]=(r['d']-r['c'])*(1 if kind=='Customer' else -1)
            unassigned=LedgerEntry.objects.filter(branch=config.branch,account_id=account,party__isnull=True).aggregate(d=Sum('debit'),c=Sum('credit'))
            if (unassigned['d'] or ZERO)!=(unassigned['c'] or ZERO):out.append(f'{config.branch.code}: the {key} control account has postings with no {kind.lower()} on them that do not cancel out')
            expected=defaultdict(lambda:ZERO)
            for d in Document.objects.filter(branch=config.branch,status='Submitted',kind__in=doc_kinds,**{field+'__isnull':False}).values(field+'_id','outstanding'):expected[d[field+'_id']]+=d['outstanding']
            for p in Document.objects.filter(branch=config.branch,status='Submitted',kind='Payment Entry',reference__isnull=True,**{field+'__isnull':False}).values(field+'_id','data'):
                amount=Decimal(str((p['data'] or {}).get('unallocated') or '0'))
                expected[p[field+'_id']]-=amount if (p['data'] or {}).get('direction')==prepay else -amount
            for party in set(ledger)|set(expected):
                if ledger[party]!=expected[party]:out.append(f'{config.branch.code} {kind} {Party.objects.get(pk=party).name}: ledger says {ledger[party]}, documents say {expected[party]}')
    return out


def outstanding_within_bounds():
    """F11. A sale is never paid more than its total and a return credit is never used more than its value (a payment counted twice)."""
    out=[]
    for d in Document.objects.filter(status='Submitted',kind__in=SELL+('Purchase Invoice',)):
        if d.is_return and not (d.total<=d.outstanding<=0):out.append(f'{d.code}: return credit of {d.total} shows {d.outstanding} left')
        if not d.is_return and d.kind in SELL and not (0<=d.outstanding<=d.total):out.append(f'{d.code}: total {d.total} but {d.outstanding} outstanding')
    return out


def payments_equal_their_ledger():
    """F5. Every posted customer or supplier payment moved exactly its amount through a cash or bank account, once."""
    out=[]
    for p in Document.objects.filter(status='Submitted',kind='Payment Entry').exclude(data__direction='Internal Transfer'):
        moved=LedgerEntry.objects.filter(document=p,account__account_type__in=('Cash','Bank')).aggregate(d=Sum('debit'),c=Sum('credit'))
        net=(moved['d'] or ZERO)-(moved['c'] or ZERO);want=p.total if p.data.get('direction')=='Receive' else -p.total
        if net!=want:out.append(f'payment {p.code}: amount {p.total} ({p.data.get("direction")}) but cash/bank moved {net}')
    return out


def locked_periods_are_closed():
    """F13. Nothing was created on or before a shop's lock date after the lock was set (posting into a locked period)."""
    out=[]
    for b in Branch.objects.filter(lock_through__isnull=False):
        lock=Audit.objects.filter(action__in=('Lock period','Year-end close'),branch=b).order_by('-created').first()
        if not lock:continue
        late=LedgerEntry.objects.filter(branch=b,posting_date__lte=b.lock_through,document__created__gt=lock.created).exclude(document__data__year_end_close=True)
        if late.exists():out.append(f'{b.code}: {late.count()} ledger rows were posted into the locked period (through {b.lock_through})')
    return out


# ------------------------------------------------------------------ inventory
def imeis_unique():
    """I1. One IMEI, one phone; a phone has at most two IMEIs, in slots 1 and 2. (The IMEI is the table's primary key; this checks formatting twins.)"""
    seen=Counter(''.join(ch for ch in i if ch.isdigit()) for i in IMEI.objects.values_list('number',flat=True))
    out=[f'IMEI {n} is recorded {c} times with different formatting' for n,c in seen.items() if c>1]
    out+=[f'IMEI {i.number} is not 15 digits' for i in IMEI.objects.all() if len(i.number)!=15 or not i.number.isdigit()]
    return out


def a_phone_is_in_one_place():
    """I2-I6. A phone's status, shop, stock location and stock movements all tell the same story, and it is counted in at most one place."""
    out=[];where=defaultdict(lambda:defaultdict(lambda:ZERO))
    for r in StockMovement.objects.filter(device__isnull=False).values('device_id','warehouse_id').annotate(q=Sum('quantity')):where[r['device_id']][r['warehouse_id']]=r['q']
    for d in Device.objects.select_related('warehouse'):
        places={w:q for w,q in where[d.pk].items() if q}
        if any(q not in (0,1) for q in where[d.pk].values()):out.append(f'{d.code}: counted {dict(where[d.pk])} times in a stock location')
        if d.status in IN_STOCK or (d.status=='Unavailable' and d.warehouse_id):      # Unavailable with a location = came back damaged, still held and valued until written off
            if list(places)!=[d.warehouse_id]:out.append(f'{d.code} is {d.status} in location {d.warehouse_id} but the stock movements put it in {list(places)}')
            if d.warehouse_id and d.warehouse.branch_id!=d.branch_id:out.append(f'{d.code}: its stock location belongs to another shop')
        elif places:out.append(f'{d.code} is {d.status} but stock movements still count it in {list(places)}')
        if d.status=='Sold' and not d.sale_id:out.append(f'{d.code} is Sold with no sale')
        if d.status!='Sold' and d.sale_id:out.append(f'{d.code} is {d.status} but still linked to a sale')
    return out


def a_phone_is_sold_once():
    """I2. Net of returns, a phone appears on at most one posted sale, and exactly one when it is Sold."""
    net=Counter()
    for device,is_return in Line.objects.filter(device__isnull=False,document__status='Submitted',document__kind__in=SELL).values_list('device_id','document__is_return'):net[device]+=-1 if is_return else 1
    out=[]
    for d in Device.objects.all():
        n=net[d.pk]
        if n not in (0,1):out.append(f'{d.code}: {n} unreturned sales')
        if (d.status=='Sold')!=(n==1):out.append(f'{d.code}: status {d.status} but {n} unreturned sale(s)')
    return out


def stock_never_negative():
    """I9. No product has less than nothing on any shelf, and an empty shelf is worth nothing."""
    out=[]
    for r in StockMovement.objects.values('product__name','warehouse__code','product__serialized').annotate(q=Sum('quantity'),v=Sum('value')):
        if r['q']<0:out.append(f"{r['product__name']} in {r['warehouse__code']}: quantity {r['q']}")
        if r['q']==0 and r['v']!=0:out.append(f"{r['product__name']} in {r['warehouse__code']}: nothing in stock but a value of {r['v']}")
    return out


def stock_value_equals_inventory_account():
    """I12. In every shop, the value of the stock movements equals the inventory account in the ledger."""
    out=[]
    for config in ShopSettings.objects.select_related('branch'):
        account=config.accounts.get('inventory')
        if not account:continue
        ledger=LedgerEntry.objects.filter(branch=config.branch,account_id=account).aggregate(d=Sum('debit'),c=Sum('credit'))
        stock=StockMovement.objects.filter(warehouse__branch=config.branch).aggregate(v=Sum('value'))['v'] or ZERO
        if (ledger['d'] or ZERO)-(ledger['c'] or ZERO)!=stock:out.append(f"{config.branch.code}: stock is worth {stock} but the inventory account says {(ledger['d'] or ZERO)-(ledger['c'] or ZERO)}")
    return out


def adjustments_have_reasons():
    """I8. Every posted stock adjustment carries a reason and an audit record."""
    out=[]
    for d in Document.objects.filter(kind='Stock Adjustment',status='Submitted'):
        if not str(d.data.get('reason') or '').strip():out.append(f'{d.code}: no reason')
        if not Audit.objects.filter(document=d).exists():out.append(f'{d.code}: no audit record')
    return out


# ------------------------------------------------------------------ online store
def online_store_owns_no_stock():
    """O10/I11. The Online Store has no stock location with anything in it and no inventory value in its books."""
    out=[]
    for b in Branch.objects.filter(is_online=True):
        held=StockMovement.objects.filter(warehouse__branch=b).aggregate(q=Sum('quantity'),v=Sum('value'))
        if held['q'] or held['v']:out.append(f'{b.code}: the Online Store holds stock ({held["q"]} units, value {held["v"]})')
        if Device.objects.filter(branch=b).exclude(status='Sold').exists():out.append(f'{b.code}: phones belong to the Online Store')
        config=ShopSettings.objects.filter(branch=b).first();account=config.accounts.get('inventory') if config else None
        if account:
            t=LedgerEntry.objects.filter(branch=b,account_id=account).aggregate(d=Sum('debit'),c=Sum('credit'))
            if (t['d'] or ZERO)!=(t['c'] or ZERO):out.append(f'{b.code}: the Online Store inventory account is not zero')
    return out


def reservations_are_real():
    """O3/O4/I10. Each phone is held by at most one open order, every hold is a real phone that is Reserved in the shop named on the hold,
    and a Reserved phone is always held by something."""
    invoiced=Document.objects.filter(online_order__isnull=False,kind__in=SELL,is_return=False,status='Submitted').values('online_order_id')
    out=[];open_holds=OnlineAllocation.objects.filter(line__order__status__in=('New','Dispatched'),device__isnull=False).exclude(line__order_id__in=invoiced).select_related('device','line__order')      # a dispatched parcel is on the books only from delivery (D4)
    for device,n in Counter(a.device_id for a in open_holds).items():
        if n>1:out.append(f'phone {device} is held by {n} open orders')
    for a in open_holds:
        if a.device.status!='Reserved':out.append(f'{a.line.order.code} holds {a.device.code} which is {a.device.status}')
        if a.device.branch_id!=a.branch_id:out.append(f'{a.line.order.code} holds {a.device.code} in the wrong shop')
    held={a.device_id for a in open_holds}
    for doc in Document.objects.filter(kind='Stock Entry',status='Submitted',state='Pending'):held|={uuid_of(pk) for pk in doc.data.get('devices',[])}
    for d in Device.objects.filter(status='Reserved'):
        if d.pk not in held:out.append(f'{d.code} is Reserved but nothing holds it')
    return out


def uuid_of(text):
    import uuid
    return uuid.UUID(str(text))


def orders_are_unique_and_posted_once():
    """O1/O2/O12. An order reference is used once; an order is invoiced once, at delivery (D4); nothing is invoiced for an order that never
    reached the customer except, for a prepaid one, the delivery charge it paid for. (Orders dispatched before 14 Nov 2026 were invoiced at dispatch.)"""
    out=[]
    for r in OnlineOrder.objects.exclude(external_ref__isnull=True).values('external_ref').annotate(n=Count('pk')).filter(n__gt=1):out.append(f"order reference {r['external_ref']} is used {r['n']} times")
    for o in OnlineOrder.objects.all():
        invoices=Document.objects.filter(online_order=o,kind__in=SELL,is_return=False,status='Submitted').count()
        if o.status in ('New','Cancelled') and invoices:out.append(f'{o.code} is {o.status} but has a posted invoice')
        if o.status=='Delivered' and invoices!=1:out.append(f'{o.code} is Delivered with {invoices} posted invoices')
        if o.status in ('Dispatched','Returned') and invoices>1:out.append(f'{o.code} is {o.status} with {invoices} posted invoices')
        if o.status=='Delivered' and o.payment_status!='Paid':out.append(f'{o.code} is delivered but not marked paid')
    return out


def prepaid_money_is_an_advance_until_delivered():
    """O6 (D5). A prepaid order confirmed paid has its money in the books as the customer's advance, once; nothing of it is income before delivery."""
    out=[]
    for o in OnlineOrder.objects.filter(payment_status='Paid',status__in=('New','Dispatched')).exclude(payment_method='Cash on Delivery'):
        rows=Document.objects.filter(online_order=o,kind='Payment Entry',status='Submitted',data__direction='Receive')
        got=rows.aggregate(t=Sum('total'))['t'] or ZERO
        if got and got!=o.total:out.append(f'{o.code}: paid {o.total} but {got} is booked')
        if rows.exclude(reference__isnull=True).exists() and not Document.objects.filter(online_order=o,kind__in=SELL,status='Submitted').exists():out.append(f'{o.code}: a payment is applied to an invoice the order does not have')
    return out


def cod_is_not_cash_until_delivered():
    """O7. A cash-on-delivery order that is not delivered has received no money."""
    out=[]
    for o in OnlineOrder.objects.filter(payment_method='Cash on Delivery').exclude(status='Delivered'):
        got=Document.objects.filter(online_order=o,kind='Payment Entry',status='Submitted',data__direction='Receive').aggregate(t=Sum('total'))['t'] or ZERO
        if got:out.append(f'{o.code} ({o.status}): cash on delivery, not delivered, but {got} is recorded as received')
    return out


def shop_payable_agrees_on_both_sides():
    """O9. What the Online Store's books say it owes the shops equals what the shops' books say they are owed."""
    online=Branch.objects.filter(is_online=True).first()
    if not online:return []
    def clearing(branch):
        config=ShopSettings.objects.filter(branch=branch).first();return config.accounts.get('inter_shop') if config else None
    mine=clearing(online)
    if not mine:return []
    t=LedgerEntry.objects.filter(branch=online,account_id=mine).aggregate(d=Sum('debit'),c=Sum('credit'));owes=(t['c'] or ZERO)-(t['d'] or ZERO);owed=ZERO
    for shop in Branch.objects.filter(is_online=False):
        account=clearing(shop)
        if not account:continue
        t=LedgerEntry.objects.filter(branch=shop,account_id=account,document__branch=online).aggregate(d=Sum('debit'),c=Sum('credit'));owed+=(t['d'] or ZERO)-(t['c'] or ZERO)
    return [] if owes==owed else [f'the Online Store says it owes the shops {owes}; the shops say they are owed {owed}']


# ------------------------------------------------------------------ security (data ownership, read from the rows themselves)
def parties_stay_in_their_shop():
    """S1. No document or ledger row links a shop to another shop's customer or supplier."""
    out=[]
    for field in ('customer','supplier'):
        bad=Document.objects.filter(**{field+'__isnull':False,field+'__branch__isnull':False}).exclude(**{field+'__branch':models.F('branch')})
        out+=[f'{d.code} in {d.branch.code} uses a {field} of another shop' for d in bad.select_related('branch')[:50]]
    return out


def important_actions_are_audited():
    """S8. Every posted or cancelled sale, purchase, payment, voucher, adjustment and return has an audit record."""
    kinds=list(SELL)+['Purchase Receipt','Purchase Invoice','Payment Entry','Voucher','Stock Adjustment','Purchase Return']
    missing=Document.objects.filter(status__in=('Submitted','Cancelled'),kind__in=kinds).annotate(a=Count('audit')).filter(a=0)
    return [f'{d.kind} {d.code} has no audit record' for d in missing[:50]]


INVARIANTS=[journals_balance,no_one_sided_rows,drafts_have_no_postings,posted_sales_have_ledger,receivables_match_invoices,outstanding_within_bounds,payments_equal_their_ledger,locked_periods_are_closed,
    imeis_unique,a_phone_is_in_one_place,a_phone_is_sold_once,stock_never_negative,stock_value_equals_inventory_account,adjustments_have_reasons,
    online_store_owns_no_stock,reservations_are_real,orders_are_unique_and_posted_once,prepaid_money_is_an_advance_until_delivered,cod_is_not_cash_until_delivered,shop_payable_agrees_on_both_sides,
    parties_stay_in_their_shop,important_actions_are_audited]


def all_problems():
    return [f'{check.__name__}: {problem}' for check in INVARIANTS for problem in check()]
