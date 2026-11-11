"""Dashboard figures. Everything here is aggregated from posted documents and the ledger — no placeholder data.

Money figures reuse the same engines as Sales & Accounts (financial_reports / reports) so the dashboard and the
reports can never disagree. Sales trend uses SQL aggregates (one query), not Python loops over documents.
"""
from datetime import date,timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Case,Count,DecimalField,F,Sum,When
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


def recent(branches,limit=6):
    branches=branches if isinstance(branches,(list,tuple)) else [branches]
    docs=list(Document.objects.filter(branch__in=branches,status='Submitted',kind__in=SALE_KINDS+['Purchase Invoice','Payment Entry'])
              .exclude(pk__in=_consolidated()).select_related('customer','supplier').order_by('-posting_date','-created')[:limit])
    for doc in docs:doc.party_name=(doc.customer or doc.supplier).name if (doc.customer or doc.supplier) else '—'
    return docs


def low_stock(branch):
    """Accessories whose stock in this shop is at or below their reorder level (products with a level of 0 are never listed)."""
    from .models import Product,StockMovement
    products=list(Product.objects.filter(enabled=True,stock_item=True,serialized=False,reorder_level__gt=0).order_by('name'))
    if not products:return []
    held=dict(StockMovement.objects.filter(product__in=products,warehouse__branch=branch).values_list('product_id').annotate(q=Sum('quantity')))
    rows=[]
    for p in products:
        qty=held.get(p.pk) or ZERO
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


RANGES=[('today','Today'),('7d','Last 7 days'),('30d','Last 30 days'),('month','This month'),('year','This year'),('custom','Custom dates')]


def period(params,today=None):
    """Resolve the dashboard date range from ?range= (and ?from_date / ?to_date for custom). Falls back to this month."""
    from datetime import date
    today=today or timezone.localdate();key=params.get('range') or 'month'
    if key=='custom':
        try:
            start=date.fromisoformat(params.get('from_date') or '');end=date.fromisoformat(params.get('to_date') or str(today))
            if start<=end:return 'custom',start,end
        except ValueError:pass
        key='month'
    start={'today':today,'7d':today-timedelta(days=6),'30d':today-timedelta(days=29),'year':today.replace(month=1,day=1)}.get(key,today.replace(day=1))
    return key if key in dict(RANGES) else 'month',start,today


def overview(shops,start,end):
    """Company or single-shop figures for [start, end]: one pass of SQL aggregates, shared with the Branch Comparison report."""
    from . import report_extra
    from .models import Device
    shops=list(shops)
    rows=report_extra.branch_comparison(shops,start,end)
    total=lambda key:sum((r[key] for r in rows),ZERO)
    income=total('Net sales');gross=total('Gross profit')
    sales=Document.objects.filter(branch__in=shops,status='Submitted',kind__in=SALE_KINDS,is_return=False,posting_date__range=[start,end]).exclude(pk__in=_consolidated())
    mix=dict(cash=ZERO,credit=ZERO,part=ZERO,cash_n=0,credit_n=0,part_n=0,cash_sales=ZERO,credit_sales=ZERO)
    # Cash or credit is decided when the sale is made: money taken on the sale date is a cash sale, the rest is credit.
    # Paying a credit invoice later never turns it into a cash sale.
    sale_rows=list(sales.values('pk','total'))
    paid=dict(Document.objects.filter(kind='Payment Entry',status='Submitted',reference__in=[r['pk'] for r in sale_rows],posting_date=F('reference__posting_date')).values_list('reference_id').annotate(t=Sum('total')))
    for row in sale_rows:
        at_sale=min(paid.get(row['pk'],ZERO),row['total']);kind='cash' if at_sale>=row['total'] else 'credit' if at_sale<=0 else 'part'
        mix[kind]+=row['total'];mix[kind+'_n']+=1;mix['cash_sales']+=at_sale;mix['credit_sales']+=row['total']-at_sale
    sold=Device.objects.filter(branch__in=shops,status='Sold',sold_on__range=[start,end])
    def top(field,limit=5):
        return list(sold.values(field).annotate(n=Count('pk'),revenue=Sum('sold_price'),cost=Sum('purchase_cost')).order_by('-revenue')[:limit])
    models=[dict(name=r['product__model__name'] or r['product__name'],n=r['n'],revenue=r['revenue'] or ZERO,profit=(r['revenue'] or ZERO)-(r['cost'] or ZERO)) for r in top('product__model__name')]
    brands=[dict(name=r['product__model__brand'] or 'Other',n=r['n'],revenue=r['revenue'] or ZERO) for r in top('product__model__brand')]
    profitable=[dict(device=d,profit=d.sold_price-d.purchase_cost) for d in sorted(sold.select_related('product'),key=lambda d:d.purchase_cost-d.sold_price)[:5]]
    compare=[dict(shop=r['Shop'],sales=r['Net sales'],purchases=r['Purchases'],gross=r['Gross profit'],margin=r['Gross margin %'],expenses=r['Expenses'],net=r['Net profit'],cash=r['Cash and bank'],owed=r['Customers owe'],payable=r['You owe suppliers'],stock=r['Stock value']) for r in rows]
    return dict(rows=rows,compare=compare,shops=shops,start=start,end=end,income=income,purchases=total('Purchases'),gross=gross,margin=(gross*100/income).quantize(Decimal('0.1')) if income else ZERO,expenses=total('Expenses'),net=total('Net profit'),
                stock=total('Stock value'),cash=total('Cash and bank'),receivable=total('Customers owe'),payable=total('You owe suppliers'),phones=sum(r['Phones sold'] for r in rows),mix=mix,models=models,brands=brands,profitable=profitable)


def month_start(day):return day.replace(day=1)


def _month_spans(months,today):
    import calendar
    year,month=today.year,today.month;spans=[]
    for _ in range(months):
        spans.append((year,month));month-=1
        if month==0:year,month=year-1,12
    return [(date(y,m,1),min(date(y,m,calendar.monthrange(y,m)[1]),today)) for y,m in reversed(spans)]


def trend(shops,months=6,today=None):
    """Sales, purchases and profit per month for the last `months` months (oldest first), with bar widths for a simple chart."""
    from . import report_extra
    today=today or timezone.localdate();spans=_month_spans(months,today)
    totals=report_extra.span_totals(list(shops),spans)
    rows=[dict(label=start.strftime('%b %Y'),sales=t['sales'],purchases=t['purchases'],profit=t['profit']) for (start,_),t in zip(spans,totals)]
    peak=max([abs(v) for r in rows for v in (r['sales'],r['purchases'],r['profit'])] or [ZERO]) or Decimal('1')
    for r in rows:
        for key in ('sales','purchases','profit'):r[key+'_w']=int(abs(r[key])*100/peak)
    return rows


def stock_counts(shops):
    """Phones available and accessory units on hand in the given shops."""
    from .models import Device,StockMovement
    phones=Device.objects.filter(branch__in=shops,status='Available').count()
    units=StockMovement.objects.filter(warehouse__branch__in=shops,product__serialized=False).aggregate(q=Sum('quantity'))['q'] or ZERO
    return phones,units


def pending_transfers(shops):
    """Transfers still on their way: (incoming, outgoing) counts for the given shops."""
    ids=[str(s.pk) for s in shops];states=['Pending','Dispatched']
    docs=Document.objects.filter(kind='Stock Entry',state__in=states).exclude(status='Cancelled')
    return docs.filter(data__target_branch__in=ids).count(),docs.filter(branch__in=shops).count()


def snapshot(shops,start,end,today=None):
    """Everything the dashboards show, for the given shops and period. Owner passes all shops; a shop passes only itself."""
    from . import money
    from . import report_extra
    today=today or timezone.localdate();shops=list(shops)
    data=overview(shops,start,end)
    now,month=report_extra.span_totals(shops,[(today,today),(month_start(today),today)])
    data['today_sales']=now['sales'];data['month_sales']=month['sales']
    data['today_expenses']=now['expenses'];data['month_expenses']=month['expenses']
    balances=money.balances(shops)
    data['cash_in_hand']=sum((b['amount'] for b in balances if b['kind']=='Cash'),ZERO);data['bank_total']=sum((b['amount'] for b in balances if b['kind']!='Cash'),ZERO)
    data['accounts']=balances
    data['stock_phones'],data['stock_units']=stock_counts(shops)
    data['pending_in'],data['pending_out']=pending_transfers(shops)
    data['trend']=trend(shops,6,today)
    return data
