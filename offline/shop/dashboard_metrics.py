"""Dashboard figures. Everything here is aggregated from posted documents and the ledger — no placeholder data.

Money figures reuse the same engines as Sales & Accounts (financial_reports / reports) so the dashboard and the
reports can never disagree. Sales trend uses SQL aggregates (one query), not Python loops over documents.
"""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Case,DecimalField,F,Sum,When
from django.db.models.functions import Abs
from django.utils import timezone
from . import financial_reports,reports as reporting
from .models import Branch,Document,LedgerEntry,ShopSettings

ZERO=Decimal('0.00')
SALE_KINDS=['Sales Invoice','POS Invoice']
DAYS=14


def _consolidated():
    # Positive JSON filter in a subquery: negating a JSON key lookup directly would also drop rows that lack the key.
    return Document.objects.filter(source__is_consolidated=True).values('pk')


def sales_trend(branch,days=DAYS):
    """Net sales (sales minus returns) per day for the last `days` days, oldest first."""
    today=timezone.localdate();start=today-timedelta(days=days-1)
    signed=Case(When(is_return=True,then=-Abs('total')),default=F('total'),output_field=DecimalField(max_digits=18,decimal_places=2))
    rows=(Document.objects.filter(branch=branch,kind__in=SALE_KINDS,status='Submitted',posting_date__gte=start,posting_date__lte=today)
          .exclude(pk__in=_consolidated()).values('posting_date').annotate(net=Sum(signed)).order_by('posting_date'))
    by_day={row['posting_date']:row['net'] or ZERO for row in rows}
    return [dict(day=start+timedelta(days=i),net=by_day.get(start+timedelta(days=i),ZERO)) for i in range(days)]


def chart_geometry(points,width=835,height=230,top=24,bottom=40):
    """Turn the trend into SVG paths for the self-drawing wave chart. Returns None when there is nothing to plot."""
    values=[max(p['net'],ZERO) for p in points]
    if len(values)<2 or not any(values):return None
    top_value=max(values);usable=height-top-bottom;step=width/(len(values)-1)
    xy=[(round(i*step,1),round(height-bottom-float(v/top_value)*usable,1)) for i,v in enumerate(values)]
    path=f'M{xy[0][0]},{xy[0][1]}'
    for (x0,y0),(x1,y1) in zip(xy,xy[1:]):
        mid=round((x0+x1)/2,1);path+=f' C{mid},{y0} {mid},{y1} {x1},{y1}'
    return dict(line=path,area=f'{path} L{width},{height} L0,{height} Z',width=width,height=height,
                points=[dict(x=x,y=y,day=p['day'],net=p['net']) for (x,y),p in zip(xy,points)],peak=top_value)


def balances(branch):
    """Cash / bank / wallet balances from the ledger accounts mapped to the shop's payment methods."""
    config=ShopSettings.objects.filter(branch=branch).first()
    if not config:return []
    methods={str(pk):name for name,pk in config.payment_methods.items() if pk}
    if not methods:return []
    totals={str(row['account_id']):(row['d'] or ZERO)-(row['c'] or ZERO) for row in
            LedgerEntry.objects.filter(branch=branch,account_id__in=list(methods)).values('account_id').annotate(d=Sum('debit'),c=Sum('credit'))}
    merged={}
    for account_id,name in methods.items():merged[name]=merged.get(name,ZERO)+totals.get(account_id,ZERO)
    return [dict(name=name,amount=amount) for name,amount in merged.items()]


def recent(branch,limit=6):
    docs=list(Document.objects.filter(branch=branch,status='Submitted',kind__in=SALE_KINDS+['Purchase Invoice','Payment Entry'])
              .exclude(pk__in=_consolidated()).select_related('customer','supplier').order_by('-posting_date','-created')[:limit])
    for doc in docs:doc.party_name=(doc.customer or doc.supplier).name if (doc.customer or doc.supplier) else '—'
    return docs


def low_stock(branch):
    """Accessories whose stock in this shop is at or below their reorder level (products with a level of 0 are never listed)."""
    from .models import Product,StockMovement
    rows=[]
    for p in Product.objects.filter(enabled=True,stock_item=True,serialized=False,reorder_level__gt=0).order_by('name'):
        qty=StockMovement.objects.filter(product=p,warehouse__branch=branch).aggregate(q=Sum('quantity'))['q'] or ZERO
        if qty<=p.reorder_level:rows.append(dict(product=p,quantity=qty,level=p.reorder_level))
    return rows


def finance(user,branch):
    """Month-to-date P&L plus receivables/payables. Returns None if the figures cannot be produced (never raises into the page)."""
    if not branch:return None
    today=timezone.localdate();month=today.replace(day=1)
    try:
        _,rows=financial_reports.run('Profit and Loss Statement',Branch.objects.filter(pk=branch.pk),month,today)
        pnl=dict(financial_reports.summary('Profit and Loss Statement',rows))
        params={'branch':str(branch.pk),'to_date':str(today)}
        _,receivables=reporting.run('Accounts Receivable',user,params)
        _,payables=reporting.run('Accounts Payable',user,params)
    except (ValidationError,KeyError,PermissionError):
        return None
    positive=lambda rows:sum((max(row['Outstanding'],0) for row in rows),ZERO)
    return dict(month=month,income=pnl.get('Income',ZERO),expenses=pnl.get('Expenses',ZERO),profit=pnl.get('Net profit / loss',ZERO),
                receivable=positive(receivables),payable=positive(payables))
