"""Independent consistency audit of the whole ERP. Reads only; changes nothing.

Every check recomputes a figure from the raw records and compares it with what another part of the system says, so a
disagreement anywhere (inventory vs ledger, balances vs invoices, reports vs ledger, one shop vs the total) is reported."""
from collections import defaultdict
from decimal import Decimal
from django.db.models import Count,Sum
from .models import Account,Branch,Device,Document,IMEI,LedgerEntry,Line,Party,ShopSettings,StockMovement,Warehouse
from . import dashboard_metrics as dm,financial_reports,money,services as s

ZERO=Decimal('0.00')
CENT=Decimal('0.01')


def _sum(qs,field):return qs.aggregate(x=Sum(field))['x'] or ZERO


def ledger_balanced_per_document():
    out=[]
    for r in LedgerEntry.objects.values('document_id','document__code','document__kind').annotate(d=Sum('debit'),c=Sum('credit')):
        if abs((r['d'] or ZERO)-(r['c'] or ZERO))>=CENT:out.append(f"{r['document__kind']} {r['document__code']}: debit {r['d']} ≠ credit {r['c']}")
    return out


def ledger_balanced_per_shop():
    out=[]
    for r in LedgerEntry.objects.values('branch__name').annotate(d=Sum('debit'),c=Sum('credit')):
        if abs((r['d'] or ZERO)-(r['c'] or ZERO))>=CENT:out.append(f"{r['branch__name']}: debits {r['d']} ≠ credits {r['c']}")
    return out


def trial_balance_balanced():
    """The trial balance report must itself balance, per shop and for all shops together."""
    out=[]
    groups=[(b.name,[b]) for b in Branch.objects.all()]+[('ALL SHOPS',list(Branch.objects.all()))]
    for name,shops in groups:
        _,rows=financial_reports.run('Trial Balance',Branch.objects.filter(pk__in=[b.pk for b in shops]),__import__('datetime').date(2000,1,1),__import__('datetime').date.today())
        d=sum((r['Closing debit'] for r in rows),ZERO);c=sum((r['Closing credit'] for r in rows),ZERO)
        if abs(d-c)>=CENT:out.append(f'{name}: trial balance closing debit {d} ≠ credit {c}')
    return out


def device_stock_vs_movements():
    out=[];sums=defaultdict(lambda:defaultdict(lambda:ZERO))
    for r in StockMovement.objects.filter(device__isnull=False).values('device_id','warehouse_id').annotate(q=Sum('quantity')):sums[r['device_id']][r['warehouse_id']]+=r['q']
    for d in Device.objects.select_related('product'):
        by=sums.get(d.pk,{});total=sum(by.values(),ZERO);expected=1 if d.status in ('Available','Reserved','Holding') else 0
        if total!=expected:out.append(f'{d.code} ({d.status}): stock movements add up to {total}, expected {expected}')
        elif expected==1:
            where=[w for w,q in by.items() if q==1]
            if where!=[d.warehouse_id]:out.append(f'{d.code} ({d.status}): sits in warehouse {d.warehouse_id} but stock says {where}')
        if d.warehouse_id and d.warehouse.branch_id!=d.branch_id:out.append(f'{d.code}: warehouse belongs to another shop than the device')
        if d.status in ('In transit','Sold','Written off') and d.warehouse_id:out.append(f'{d.code}: status {d.status} but still has a warehouse')
    return out


def no_negative_stock():
    out=[]
    for r in StockMovement.objects.values('product__name','warehouse__branch__name','product_id','warehouse_id').annotate(q=Sum('quantity')):
        if r['q']<0:out.append(f"{r['warehouse__branch__name']}: {r['product__name']} has negative stock {r['q']}")
    return out


def empty_shelf_has_no_value():
    """Accessories are valued at their average cost, so a shelf with nothing on it is worth nothing, and stock is never worth less than nothing."""
    out=[]
    for r in StockMovement.objects.filter(product__serialized=False).values('warehouse__branch__name','product__name').annotate(q=Sum('quantity'),v=Sum('value')):
        q=r['q'] or ZERO;v=r['v'] or ZERO
        if q==0 and abs(v)>=CENT:out.append(f"{r['warehouse__branch__name']}: {r['product__name']} has no stock but a stock value of {v}")
        elif q>0 and v<=-CENT:out.append(f"{r['warehouse__branch__name']}: {r['product__name']} has {q:g} in stock with a negative value of {v}")
    return out


def valuation_vs_ledger():
    out=[]
    for config in ShopSettings.objects.select_related('branch'):
        inv=config.accounts.get('inventory')
        if not inv:continue
        ledger=_sum(LedgerEntry.objects.filter(branch=config.branch,account_id=inv),'debit')-_sum(LedgerEntry.objects.filter(branch=config.branch,account_id=inv),'credit')
        stock=_sum(StockMovement.objects.filter(warehouse__branch=config.branch),'value')
        if abs(ledger-stock)>=CENT:out.append(f'{config.branch.name}: stock value {stock} ≠ inventory ledger {ledger}')
    return out


def party_balances_vs_invoices():
    """For every customer and supplier: what the ledger says they owe equals the open amounts on their invoices less unapplied advances."""
    out=[]
    for config in ShopSettings.objects.select_related('branch'):
        for kind,key,sale_kinds,field in (('Customer','receivable',list(s.SELL),'customer'),('Supplier','payable',['Purchase Invoice'],'supplier')):
            account=config.accounts.get(key)
            if not account:continue
            ledger={r['party_id']:(r['d'] or ZERO)-(r['c'] or ZERO) for r in LedgerEntry.objects.filter(branch=config.branch,account_id=account,party__isnull=False).values('party_id').annotate(d=Sum('debit'),c=Sum('credit'))}
            if kind=='Supplier':ledger={k:-v for k,v in ledger.items()}
            docs=defaultdict(lambda:ZERO)
            for d in Document.objects.filter(branch=config.branch,status='Submitted',kind__in=sale_kinds,**{field+'__isnull':False}).values(field+'_id','outstanding'):docs[d[field+'_id']]+=d['outstanding']
            adv=defaultdict(lambda:ZERO)
            for p in Document.objects.filter(branch=config.branch,status='Submitted',kind='Payment Entry',reference__isnull=True,**{field+'__isnull':False}).values(field+'_id','data'):
                try:amount=Decimal(str((p['data'] or {}).get('unallocated','0')))
                except Exception:amount=ZERO
                prepay='Receive' if kind=='Customer' else 'Pay'          # a prepayment lowers the balance; a refund given back raises it
                adv[p[field+'_id']]+=amount if (p['data'] or {}).get('direction')==prepay else -amount
            for pid in set(ledger)|set(docs)|set(adv):
                expected=docs.get(pid,ZERO)-adv.get(pid,ZERO)
                if abs(ledger.get(pid,ZERO)-expected)>=CENT:
                    name=Party.objects.filter(pk=pid).values_list('name',flat=True).first()
                    out.append(f"{config.branch.name} · {kind} {name}: ledger {ledger.get(pid,ZERO)} vs invoices-less-advances {expected}")
    return out


def document_totals_vs_ledger():
    out=[]
    for d in Document.objects.filter(status__in=('Submitted','Cancelled'),kind__in=list(s.SELL)+['Purchase Invoice']).select_related('branch'):
        config=ShopSettings.objects.filter(branch=d.branch).first()
        if not config:continue
        key='payable' if d.kind=='Purchase Invoice' else 'receivable';account=config.accounts.get(key)
        entries=LedgerEntry.objects.filter(document=d,account_id=account)
        net=(_sum(entries,'credit')-_sum(entries,'debit')) if d.kind=='Purchase Invoice' else (_sum(entries,'debit')-_sum(entries,'credit'))
        if d.status=='Submitted' and abs(net-d.total)>=CENT:out.append(f'{d.kind} {d.code}: total {d.total} but ledger {net}')
        if d.status=='Submitted' and not d.is_return and not (ZERO-CENT<=d.outstanding<=d.total+CENT):out.append(f'{d.code}: outstanding {d.outstanding} outside 0..{d.total}')
        if d.status=='Submitted' and d.is_return and d.outstanding>CENT:out.append(f'{d.code}: return has positive outstanding {d.outstanding}')
    return out


def line_sums_vs_totals():
    out=[]
    for d in Document.objects.filter(status='Submitted',kind__in=list(s.SELL)+['Purchase Invoice','Purchase Receipt']).prefetch_related('lines'):
        net=sum((s.money(l.quantity*l.rate) for l in d.lines.all()),ZERO)
        if d.kind in s.SELL and d.data.get('pricing',{}).get('version')==1:continue          # discount/tax shape the total; checked through the ledger instead
        if d.kind=='Purchase Receipt':continue
        if not d.is_return and abs(net-d.total)>=CENT:out.append(f'{d.kind} {d.code}: lines add up to {net}, total says {d.total}')
    return out


def sold_and_available_devices():
    out=[]
    sold_lines=defaultdict(int)
    for l in Line.objects.filter(device__isnull=False,document__status='Submitted',document__kind__in=list(s.SELL)).select_related('document'):
        sold_lines[l.device_id]+=-1 if l.document.is_return else 1
    for d in Device.objects.all():
        net=sold_lines.get(d.pk,0)
        if d.status=='Sold' and net!=1:out.append(f'{d.code}: marked Sold but net sale lines = {net}')
        if d.status in ('Available','Reserved','In transit') and net>0:out.append(f'{d.code}: {d.status} but has an unreturned sale')
        if d.status=='Sold' and (not d.sale_id or not d.customer_id):out.append(f'{d.code}: Sold without a sale or customer link')
        if d.status!='Sold' and d.sale_id and d.status in ('Available',):out.append(f'{d.code}: Available but still linked to sale {d.sale_id}')
    return out


def imei_integrity():
    out=[]
    for i in IMEI.objects.all():
        if not s.valid_imei(i.number):out.append(f'IMEI {i.number}: fails the check digit')
    for r in IMEI.objects.values('device_id').annotate(n=Count('number')):
        if r['n']>2:out.append(f"device {r['device_id']}: {r['n']} IMEIs")
    return out


def shop_ownership():
    out=[]
    for d in Document.objects.filter(customer__isnull=False,customer__branch__isnull=False).select_related('customer','branch'):
        if d.customer.branch_id!=d.branch_id:out.append(f'{d.code}: customer {d.customer.name} belongs to another shop')
    for d in Document.objects.filter(supplier__isnull=False,supplier__branch__isnull=False).select_related('supplier','branch'):
        if d.supplier.branch_id!=d.branch_id:out.append(f'{d.code}: supplier {d.supplier.name} belongs to another shop')
    for l in LedgerEntry.objects.filter(party__isnull=False,party__branch__isnull=False).select_related('party')[:100000]:
        if l.party.branch_id!=l.branch_id:out.append(f'ledger row of {l.document_id}: party {l.party.name} in another shop');break
    return out


def unclassified_accounts():
    classes=financial_reports.classifications();out=[]
    for r in LedgerEntry.objects.values('account_id').annotate(d=Sum('debit'),c=Sum('credit')):
        account,root=classes[r['account_id']]
        if root=='Unclassified' and (r['d'] or ZERO)!=(r['c'] or ZERO):out.append(f'{account.code} {account.name}: has a balance but no section')
    return out


def cancelled_documents_net_zero():
    out=[]
    for d in Document.objects.filter(status='Cancelled'):
        nets=defaultdict(lambda:ZERO)
        for e in LedgerEntry.objects.filter(document=d):nets[e.account_id]+=e.debit-e.credit
        for rev in Document.objects.filter(reference=d,notes__startswith='Cancellation reversal'):
            for e in LedgerEntry.objects.filter(document=rev):nets[e.account_id]+=e.debit-e.credit
        bad={a:v for a,v in nets.items() if abs(v)>=CENT}
        if bad:out.append(f'{d.kind} {d.code}: cancelled but its ledger does not net to zero ({len(bad)} accounts)')
    return out


def reports_vs_ledger():
    """Dashboard, branch comparison and P&L must all agree with the raw ledger; the total must equal the sum of the shops."""
    import datetime
    out=[];start=datetime.date(2000,1,1);end=datetime.date.today();classes=financial_reports.classifications()
    per={}
    for b in Branch.objects.all():
        inc=exp=ZERO
        for r in financial_reports.pre_closing(LedgerEntry.objects.filter(branch=b,posting_date__range=[start,end])).values('account_id').annotate(d=Sum('debit'),c=Sum('credit')):      # year-end closing entries move profit into equity; the statements are about the period's activity
            account,root=classes[r['account_id']];net=(r['d'] or ZERO)-(r['c'] or ZERO)
            if root=='Income':inc-=net
            elif root=='Expense':exp+=net
        snap=dm.overview([b],start,end);per[b.pk]=(snap['income'],snap['net'])
        if abs(snap['income']-inc)>=CENT:out.append(f'{b.name}: dashboard sales {snap["income"]} ≠ ledger income {inc}')
        if abs(snap['net']-(inc-exp))>=CENT:out.append(f'{b.name}: dashboard net profit {snap["net"]} ≠ ledger {inc-exp}')
        _,rows=financial_reports.run('Profit and Loss Statement',Branch.objects.filter(pk=b.pk),start,end);summ=dict(financial_reports.summary('Profit and Loss Statement',rows))
        if abs(summ.get('Net profit / loss',ZERO)-(inc-exp))>=CENT:out.append(f'{b.name}: P&L net {summ.get("Net profit / loss")} ≠ ledger {inc-exp}')
    total=dm.overview(list(Branch.objects.all()),start,end)
    if abs(total['income']-sum(v[0] for v in per.values()))>=CENT:out.append(f'all shops: total sales {total["income"]} ≠ sum of shops {sum(v[0] for v in per.values())}')
    if abs(total['net']-sum(v[1] for v in per.values()))>=CENT:out.append(f'all shops: total net profit {total["net"]} ≠ sum of shops {sum(v[1] for v in per.values())}')
    return out


def cash_and_bank_vs_history():
    out=[];shops=list(Branch.objects.all())
    for scope in [[b] for b in shops]+[shops]:
        position=sum((b['amount'] for b in money.balances(scope)),ZERO);_,totals=money.history(scope)
        if abs(position-(totals['received']-totals['paid']))>=CENT:out.append(f"{', '.join(b.name for b in scope)}: balances {position} ≠ history received−paid {totals['received']-totals['paid']}")
    return out


def dues_vs_balances():
    out=[]
    for b in Branch.objects.all():
        for label,rows,key in (('customers',money.customer_dues([b]),'receivable'),('suppliers',money.supplier_dues([b]),'payable')):
            total=sum((r['remaining'] for r in rows),ZERO)
            config=ShopSettings.objects.get(branch=b);acct=config.accounts[key]
            led=LedgerEntry.objects.filter(branch=b,account_id=acct).aggregate(d=Sum('debit'),c=Sum('credit'))
            net=(led['d'] or ZERO)-(led['c'] or ZERO)
            if key=='payable':net=-net
            positive=sum((v for v in _party_ledger(b,acct,key).values() if v>0),ZERO)
            if abs(total-positive)>=CENT:out.append(f'{b.name}: {label} dues list {total} ≠ ledger {positive}')
    return out


def _party_ledger(branch,account,key):
    out={r['party_id']:(r['d'] or ZERO)-(r['c'] or ZERO) for r in LedgerEntry.objects.filter(branch=branch,account_id=account,party__isnull=False).values('party_id').annotate(d=Sum('debit'),c=Sum('credit'))}
    return {k:(-v if key=='payable' else v) for k,v in out.items()}


CHECKS=[('Ledger balances for every document',ledger_balanced_per_document),('Ledger balances for every shop',ledger_balanced_per_shop),('Trial balance balances',trial_balance_balanced),
    ('Every phone: status vs stock movements vs location',device_stock_vs_movements),('No negative stock',no_negative_stock),('An empty shelf carries no value',empty_shelf_has_no_value),('Stock value equals inventory ledger',valuation_vs_ledger),
    ('Customer/supplier balances equal their invoices',party_balances_vs_invoices),('Invoice totals equal the ledger',document_totals_vs_ledger),('Invoice lines add up to totals',line_sums_vs_totals),
    ('Sold / available phones agree with sales',sold_and_available_devices),('IMEIs are valid',imei_integrity),('Customers and suppliers stay in their own shop',shop_ownership),
    ('No unclassified accounts with balances',unclassified_accounts),('Cancelled documents net to zero',cancelled_documents_net_zero),('Dashboard/P&L equal the ledger; total equals shops',reports_vs_ledger),
    ('Cash and bank equal the transaction history',cash_and_bank_vs_history),('Dues lists equal the ledger',dues_vs_balances)]


def run_all():
    return [(name,fn()) for name,fn in CHECKS]
