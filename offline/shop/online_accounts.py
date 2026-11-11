"""Accounting for the online channel, read straight from the books.

Every online invoice, return and payment is an ordinary document in the shop that supplied the stock, tagged channel = Online. So these
figures always agree with each shop's own books; they are simply the online part of them. Online expenses are shop expenses ticked
"online-store expense"."""
from collections import defaultdict
from decimal import Decimal
from django.db.models import Count,Sum
from .models import Branch,Document,LedgerEntry,OnlineOrder
from . import services as s

ZERO=Decimal('0.00')


def summary(start=None,end=None):
    docs=Document.objects.filter(channel='Online',status='Submitted')
    if start:docs=docs.filter(posting_date__gte=start)
    if end:docs=docs.filter(posting_date__lte=end)
    invoices=docs.filter(kind__in=list(s.SELL),is_return=False);returns=docs.filter(kind__in=list(s.SELL),is_return=True)
    payments=docs.filter(kind='Payment Entry');expenses=docs.filter(kind='Voucher')
    sales=invoices.aggregate(t=Sum('total'))['t'] or ZERO;returned=-(returns.aggregate(t=Sum('total'))['t'] or ZERO)
    returned=abs(returned)
    ledger=LedgerEntry.objects.filter(document__in=list(invoices.values_list('pk',flat=True))+list(returns.values_list('pk',flat=True)))
    revenue=sum((e.credit-e.debit for e in ledger.filter(account__account_type='Income Account')),ZERO)
    cogs=sum((e.debit-e.credit for e in ledger.filter(account__account_type='Cost of Goods Sold')),ZERO)
    online_expense=sum((Decimal(str(d.data.get('amount') or 0)) for d in expenses.filter(data__type='expense')),ZERO)
    methods=defaultdict(lambda:dict(received=ZERO,refunded=ZERO))
    for p in payments:
        m=methods[p.data.get('mode','')]
        if p.data.get('direction')=='Receive':m['received']+=p.total
        else:m['refunded']+=p.total
    received=sum((m['received'] for m in methods.values()),ZERO);refunded=sum((m['refunded'] for m in methods.values()),ZERO)
    pending=Document.objects.filter(channel='Online',status='Submitted',kind__in=list(s.SELL),is_return=False,outstanding__gt=0).aggregate(t=Sum('outstanding'))['t'] or ZERO
    credit_owed=-(Document.objects.filter(channel='Online',status='Submitted',kind__in=list(s.SELL),is_return=True,outstanding__lt=0).aggregate(t=Sum('outstanding'))['t'] or ZERO)
    shops=[]
    for b in Branch.objects.filter(enabled=True).order_by('name'):
        mine=invoices.filter(branch=b)
        n=mine.count()
        if n:shops.append(dict(shop=b.name,invoices=n,sales=mine.aggregate(t=Sum('total'))['t'] or ZERO,pending=mine.filter(outstanding__gt=0).aggregate(t=Sum('outstanding'))['t'] or ZERO))
    status={r['status']:r['n'] for r in OnlineOrder.objects.values('status').annotate(n=Count('id'))}
    return dict(sales=sales,returned=returned,net_sales=sales-returned,revenue=revenue,cogs=cogs,gross_profit=revenue-cogs,expenses=online_expense,net_profit=revenue-cogs-online_expense,
                received=received,refunded=refunded,net_received=received-refunded,pending=pending,credit_owed=credit_owed,methods=sorted(({'method':k,**v} for k,v in methods.items()),key=lambda r:r['method']),
                shops=shops,orders=status,order_count=sum(status.values()),invoices=invoices.count())
