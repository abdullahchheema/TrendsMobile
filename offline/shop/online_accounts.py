"""The Online Store's own accounting.

The Online Store is a set of books like any shop: it has its own cash, bank and wallet accounts, customers, sales, refunds and expenses.
It holds no stock. When it sells a phone that sits in a shop, the stock leaves that shop at cost and the Online Store records the cost of
goods sold and a debt to the shop (inter-shop clearing). The shop's books show the stock leaving and a claim on the Online Store, so:

    Online Store:  Dr Cash / Receivable   Cr Sales                (the customer's price)
                   Dr Cost of goods sold  Cr Inter-shop clearing  (what the shop is owed, at cost)
    The shop:      Dr Inter-shop clearing Cr Inventory            (the same cost)

Both sides always cancel out in the owner's consolidated figures. The Online Store settles what it owes by paying the shop from its own
bank or wallet (Pay a shop), which moves real money between the two sets of books. Nothing here is visible to a shop beyond the stock
leaving and that claim."""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count,Sum
from django.utils import timezone
from .models import Account,Branch,Document,LedgerEntry,OnlineOrder,ShopSettings
from . import money,services as s,setup

ZERO=Decimal('0.00')


def _online():return Branch.objects.filter(is_online=True).first()


def _clearing(branch):
    config=ShopSettings.objects.filter(branch=branch).first()
    pk=config.accounts.get('inter_shop') if config else None
    return Account.objects.filter(pk=pk).first() if pk else None


def owed_to_shops(online=None):
    """[(shop, amount the Online Store still owes it)] and the Online Store's own figure (they must agree)."""
    online=online or _online();rows=[]
    if not online:return rows,ZERO
    mine=_clearing(online)
    own=sum((e.credit-e.debit for e in LedgerEntry.objects.filter(branch=online,account=mine)),ZERO) if mine else ZERO
    for shop in Branch.objects.filter(enabled=True,is_online=False).order_by('name'):
        acct=_clearing(shop)
        if not acct:continue
        entries=LedgerEntry.objects.filter(branch=shop,account=acct,document__branch=online)
        amount=sum((e.debit-e.credit for e in entries),ZERO)
        if amount or entries.exists():rows.append(dict(shop=shop,owed=amount))
    return rows,own


def summary(start=None,end=None):
    online=_online()
    empty=dict(goods_sales=ZERO,goods_revenue=ZERO,product_profit=ZERO,delivery_kept=ZERO,sales=ZERO,returned=ZERO,net_sales=ZERO,revenue=ZERO,cogs=ZERO,gross_profit=ZERO,expenses=ZERO,net_profit=ZERO,received=ZERO,refunded=ZERO,net_received=ZERO,pending=ZERO,credit_owed=ZERO,
               methods=[],balances=[],owed=[],owed_total=ZERO,books_agree=True,shops=[],orders={},order_count=0,invoices=0,branch=None)
    if not online:return empty
    docs=Document.objects.filter(branch=online,status='Submitted')
    if start:docs=docs.filter(posting_date__gte=start)
    if end:docs=docs.filter(posting_date__lte=end)
    invoices=docs.filter(kind__in=list(s.SELL),is_return=False);returns=docs.filter(kind__in=list(s.SELL),is_return=True)
    payments=docs.filter(kind='Payment Entry');vouchers=docs.filter(kind='Voucher')
    written=docs.filter(kind='Journal Entry',data__has_key='charge_cancelled')      # delivery charges of refused parcels, taken back out of income (online_orders.cancel_uncollected_charges)
    sales=invoices.aggregate(t=Sum('total'))['t'] or ZERO;returned=abs(returns.aggregate(t=Sum('total'))['t'] or ZERO)+(written.aggregate(t=Sum('total'))['t'] or ZERO)
    ids=list(invoices.values_list('pk',flat=True))+list(returns.values_list('pk',flat=True))+list(written.values_list('pk',flat=True))
    ledger=LedgerEntry.objects.filter(document__in=ids,branch=online)
    revenue=sum((e.credit-e.debit for e in ledger.filter(account__account_type='Income Account')),ZERO)
    cogs=sum((e.debit-e.credit for e in ledger.filter(account__account_type='Cost of Goods Sold')),ZERO)
    # Delivery charges are not sales of goods. What the customers paid for delivery (and that was not given back) is shown on its own, so the
    # sales and the profit on the phones are only about the phones. The books themselves are unchanged: the charge is income, just not a sale.
    from . import online_orders as oo
    charges=[oo.delivery_charge_of(i) for i in invoices.prefetch_related('lines__product')]
    delivery_net_in=sum((n for n,_ in charges),ZERO);delivery_gross_in=sum((n+t for n,t in charges),ZERO)
    gave_back=LedgerEntry.objects.filter(document__in=list(written.values_list('pk',flat=True)),branch=online,account__account_type='Income Account')
    delivery_net_back=sum((e.debit-e.credit for e in gave_back),ZERO);delivery_gross_back=written.aggregate(t=Sum('total'))['t'] or ZERO
    delivery_kept=delivery_net_in-delivery_net_back;goods_revenue=revenue-delivery_kept
    goods_sales=(sales-delivery_gross_in)-(abs(returns.aggregate(t=Sum('total'))['t'] or ZERO))
    expenses=sum((Decimal(str(d.data.get('amount') or 0)) for d in vouchers.filter(data__type='expense')),ZERO)
    methods=defaultdict(lambda:dict(received=ZERO,refunded=ZERO))
    for p in payments:
        m=methods[p.data.get('mode','')]
        if p.data.get('direction')=='Receive':m['received']+=p.total
        else:m['refunded']+=p.total
    received=sum((m['received'] for m in methods.values()),ZERO);refunded=sum((m['refunded'] for m in methods.values()),ZERO)
    live=Document.objects.filter(branch=online,status='Submitted',kind__in=list(s.SELL))
    pending=live.filter(is_return=False,outstanding__gt=0).aggregate(t=Sum('outstanding'))['t'] or ZERO
    credit_owed=-(live.filter(is_return=True,outstanding__lt=0).aggregate(t=Sum('outstanding'))['t'] or ZERO)
    owed,own=owed_to_shops(online);owed_total=sum((r['owed'] for r in owed),ZERO)
    status={r['status']:r['n'] for r in OnlineOrder.objects.values('status').annotate(n=Count('id'))}
    return dict(branch=online,goods_sales=goods_sales,goods_revenue=goods_revenue,product_profit=goods_revenue-cogs,delivery_kept=delivery_kept,sales=sales,returned=returned,net_sales=sales-returned,revenue=revenue,cogs=cogs,gross_profit=revenue-cogs,expenses=expenses,net_profit=revenue-cogs-expenses,
                received=received,refunded=refunded,net_received=received-refunded,pending=pending,credit_owed=credit_owed,methods=sorted(({'method':k,**v} for k,v in methods.items()),key=lambda r:r['method']),
                balances=money.balances([online]),owed=owed,owed_total=owed_total,books_agree=owed_total==own,shops=[],orders=status,order_count=sum(status.values()),invoices=invoices.count())


@transaction.atomic
def pay_shop(user,shop,amount,online_method,shop_method,when=None,note=''):
    """The Online Store pays a shop what it owes for stock supplied (at cost). Real money moves from an Online Store account to a shop account;
    both sets of books record it and the inter-shop clearing falls on both sides."""
    from . import online_access
    if not (user.is_superuser or online_access.is_online(user)):raise ValidationError('Only the owner and the online store login settle accounts between the Online Store and a shop.')
    online=_online()
    if not online:raise ValidationError('The Online Store has no books yet.')
    if shop.is_online or not shop.enabled:raise ValidationError('Choose a shop.')
    amount=s.payment_money(amount);when=when or timezone.localdate()
    if amount<=0:raise ValidationError('Enter an amount greater than zero.')
    online=Branch.objects.select_for_update(no_key=True).get(pk=online.pk);shop=Branch.objects.select_for_update(no_key=True).get(pk=shop.pk)
    owed=next((r['owed'] for r in owed_to_shops(online)[0] if r['shop'].pk==shop.pk),ZERO)
    if amount>owed:raise ValidationError(f'The Online Store owes {shop.name} only PKR {owed:,.2f}, so it cannot pay PKR {amount:,.2f}.')
    oc=ShopSettings.objects.get(branch=online);sc=ShopSettings.objects.get(branch=shop)
    if online_method not in oc.payment_methods:raise ValidationError('Choose an Online Store account to pay from.')
    if shop_method not in sc.payment_methods:raise ValidationError(f'Choose the {shop.name} account that receives the money.')
    from_account=Account.objects.get(pk=oc.payment_methods[online_method]);to_account=Account.objects.get(pk=sc.payment_methods[shop_method])
    available=s.account_balance(online,from_account)
    if available<amount:raise ValidationError(f'The Online Store {online_method} account holds only PKR {available:,.2f}.')
    s.open_date(Document(branch=online,posting_date=when));s.open_date(Document(branch=shop,posting_date=when))
    _,online_clearing=setup.transfer_accounts(online);_,shop_clearing=setup.transfer_accounts(shop)
    doc=s.new_doc('Journal Entry',online,posting_date=when,status='Submitted',posted_by=user,total=amount,notes=note or f'Online Store pays {shop.name} for stock supplied',
                  data=dict(online_settlement=str(shop.pk),amount=str(amount),mode=online_method,shop_mode=shop_method))
    for branch,account,debit,credit in ((online,online_clearing,amount,0),(online,from_account,0,amount),(shop,to_account,amount,0),(shop,shop_clearing,0,amount)):
        LedgerEntry.objects.create(document=doc,branch=branch,posting_date=when,account=account,debit=debit,credit=credit)
    s.audit(user,'Online Store paid a shop',doc,branch=online,shop=shop.name,amount=str(amount));return doc
