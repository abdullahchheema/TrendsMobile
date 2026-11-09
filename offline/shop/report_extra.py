"""Additional business reports: sales by day/month/year, expenses, low stock, IMEI list, exchanges, stock transfers and
branch comparison. Every report is shop-scoped by the caller and returns plain rows so it exports to CSV like the others."""
from collections import defaultdict
from decimal import Decimal
from django.db.models import Sum
from .models import Device,Document,Line,LedgerEntry,ShopSettings,StockMovement
from . import financial_reports,services as s

ZERO=Decimal('0.00')
SALES=('Daily Sales','Monthly Sales','Annual Sales')

COLUMNS={
 'Daily Sales':['Date','Shop','Invoices','Phones sold','Sales total','Returns','Net sales','Collected','Still unpaid'],
 'Monthly Sales':['Month','Shop','Invoices','Phones sold','Sales total','Returns','Net sales','Collected','Still unpaid'],
 'Annual Sales':['Year','Shop','Invoices','Phones sold','Sales total','Returns','Net sales','Collected','Still unpaid'],
 'Expense Report':['Account code','Account','Shop','Amount'],
 'Low Stock':['Item','Shop','Quantity','Reorder level','Short by'],
 'IMEI Report':['IMEI 1','IMEI 2','Device','Model','Shop','Status','Condition','Grade','Received','Sold','Customer'],
 'Exchange Report':['Date','Shop','Exchange','Customer','New phone price','Trade-in value','Cash difference','Old phones sold','Old phone margin'],
 'Stock Transfer Report':['Date','Document','From shop','To shop','Value'],
 'Branch Comparison':['Shop','Net sales','Purchases','Gross profit','Gross margin %','Expenses','Net profit','Stock value','Cash and bank','Customers owe','You owe suppliers','Phones sold'],
}
MONEY={
 'Daily Sales':{'Sales total','Returns','Net sales','Collected','Still unpaid'},'Monthly Sales':{'Sales total','Returns','Net sales','Collected','Still unpaid'},'Annual Sales':{'Sales total','Returns','Net sales','Collected','Still unpaid'},
 'Expense Report':{'Amount'},'Exchange Report':{'New phone price','Trade-in value','Cash difference','Old phone margin'},'Stock Transfer Report':{'Value'},
 'Branch Comparison':{'Net sales','Purchases','Gross profit','Expenses','Net profit','Stock value','Cash and bank','Customers owe','You owe suppliers'},
}
DESCRIPTIONS={
 'Daily Sales':'Invoices, phones sold, sales, returns and how much was collected, for each day.',
 'Monthly Sales':'The same figures grouped by month.','Annual Sales':'The same figures grouped by year.',
 'Expense Report':'Operating expenses by category and shop for the period (cost of goods sold is excluded).',
 'Low Stock':'Accessories at or below their reorder level in each shop.',
 'IMEI Report':'Every phone with its IMEIs, status, condition and sale. Filter by status.',
 'Exchange Report':'Each exchange: new phone price, trade-in value, cash difference and the profit or loss when the old phones were resold.',
 'Stock Transfer Report':'Phones moved between shops, with the value moved.',
 'Branch Comparison':'Shops side by side for the period: sales, profit, stock, cash and what customers owe. Compare and rank branches.',
}


def cogs_accounts():
    return {str(c.accounts.get('cogs')) for c in ShopSettings.objects.all() if c.accounts.get('cogs')}


def is_cogs(account,cogs):
    return account.account_type=='Cost of Goods Sold' or str(account.pk) in cogs


def _label(day,name):
    return {'Daily Sales':day,'Monthly Sales':f'{day:%Y-%m}','Annual Sales':str(day.year)}[name]


def sales_summary(name,shops,start,end):
    docs=Document.objects.filter(branch__in=shops,status='Submitted',kind__in=list(s.SELL),posting_date__range=[start,end]).select_related('branch')
    groups=defaultdict(lambda:dict(invoices=0,phones=0,total=ZERO,returns=ZERO,unpaid=ZERO))
    phones=dict(Line.objects.filter(document__in=docs,device__isnull=False,document__is_return=False).values_list('document_id').annotate(n=Sum('quantity')))
    for d in docs:
        if d.source.get('is_consolidated'):continue
        g=groups[(_label(d.posting_date,name),d.branch.name)]
        if d.is_return:g['returns']+=-d.total
        else:g['invoices']+=1;g['phones']+=int(phones.get(d.pk,0));g['total']+=d.total;g['unpaid']+=max(d.outstanding,ZERO)
    rows=[]
    for (label,shop),g in sorted(groups.items(),key=lambda kv:(str(kv[0][0]),kv[0][1]),reverse=(name=='Daily Sales')):
        net=g['total']-g['returns']
        rows.append({COLUMNS[name][0]:label,'Shop':shop,'Invoices':g['invoices'],'Phones sold':g['phones'],'Sales total':g['total'],'Returns':g['returns'],'Net sales':net,'Collected':net-g['unpaid'],'Still unpaid':g['unpaid']})
    return rows


def expense_report(shops,start,end):
    classes=financial_reports.classifications();cogs=cogs_accounts();totals=defaultdict(lambda:ZERO)
    for r in LedgerEntry.objects.filter(branch__in=shops,posting_date__range=[start,end]).values('account_id','branch__name').annotate(d=Sum('debit'),c=Sum('credit')):
        account,root=classes[r['account_id']]
        if root=='Expense' and not is_cogs(account,cogs):totals[(account.code,account.name,r['branch__name'])]+=(r['d'] or ZERO)-(r['c'] or ZERO)
    return [{'Account code':c,'Account':n,'Shop':sh,'Amount':a} for (c,n,sh),a in sorted(totals.items()) if a]


def low_stock(shops):
    from . import dashboard_metrics as dm
    rows=[]
    for shop in shops:
        for r in dm.low_stock(shop):rows.append({'Item':r['product'].name,'Shop':shop.name,'Quantity':r['quantity'],'Reorder level':r['level'],'Short by':max(r['level']-r['quantity'],ZERO)})
    return rows


def imei_report(shops,params,with_costs):
    devices=Device.objects.filter(branch__in=shops).select_related('branch','product','customer').prefetch_related('imeis')
    if params.get('status'):devices=devices.filter(status=params['status'])
    rows=[]
    for d in devices.order_by('-created'):
        numbers=[i.number for i in sorted(d.imeis.all(),key=lambda i:i.slot)]
        rows.append({'IMEI 1':numbers[0] if numbers else '','IMEI 2':numbers[1] if len(numbers)>1 else '','Device':d.code,'Model':d.product.name,'Shop':d.branch.name,'Status':d.status,'Condition':d.condition,'Grade':d.grade or '','Received':d.received_on,'Sold':d.sold_on or '','Customer':d.customer.name if d.customer_id else ''})
    return rows


def exchange_report(shops,start,end):
    rows=[]
    for ex in Document.objects.filter(branch__in=shops,kind='Mobile Exchange',status='Submitted',posting_date__range=[start,end]).select_related('branch','customer').order_by('-posting_date'):
        bill=Document.objects.filter(pk=ex.data.get('purchase_invoice')).first();sale=Document.objects.filter(pk=ex.data.get('sales_invoice')).first()
        old=Device.objects.filter(pk__in=Line.objects.filter(document=bill.reference).values('device')) if bill and bill.reference_id else Device.objects.none()
        sold=old.filter(status='Sold');margin=sum((d.sold_price-d.purchase_cost for d in sold),ZERO)
        rows.append({'Date':ex.posting_date,'Shop':ex.branch.name,'Exchange':ex.code,'Customer':ex.customer.name if ex.customer_id else '','New phone price':sale.total if sale else ZERO,'Trade-in value':bill.total if bill else ZERO,
                     'Cash difference':(sale.total-bill.total) if sale and bill else ZERO,'Old phones sold':f'{sold.count()} of {old.count()}','Old phone margin':margin})
    return rows


def transfer_report(shops,start,end):
    rows=[]
    for d in Document.objects.filter(kind='Stock Entry',status='Submitted',data__has_key='transfer',posting_date__range=[start,end],branch__in=shops).select_related('branch').order_by('-posting_date'):
        t=d.data.get('transfer') or {}
        rows.append({'Date':d.posting_date,'Document':d.code,'From shop':t.get('source_shop',d.branch.name),'To shop':t.get('target_shop',''),'Value':s.money(t.get('value'))})
    return rows


def branch_comparison(shops,start,end):
    classes=financial_reports.classifications();cogs=cogs_accounts()
    per=defaultdict(lambda:defaultdict(lambda:ZERO))
    for r in LedgerEntry.objects.filter(branch__in=shops,posting_date__range=[start,end]).values('branch_id','account_id').annotate(d=Sum('debit'),c=Sum('credit')):
        account,root=classes[r['account_id']];net=(r['d'] or ZERO)-(r['c'] or ZERO)
        if root=='Income':per[r['branch_id']]['income']-=net
        elif root=='Expense':per[r['branch_id']]['cogs' if is_cogs(account,cogs) else 'expense']+=net
    balances=defaultdict(lambda:defaultdict(lambda:ZERO))
    configs={c.branch_id:c for c in ShopSettings.objects.filter(branch__in=shops)}
    for r in LedgerEntry.objects.filter(branch__in=shops,posting_date__lte=end).values('branch_id','account_id').annotate(d=Sum('debit'),c=Sum('credit')):
        account,root=classes[r['account_id']];net=(r['d'] or ZERO)-(r['c'] or ZERO);conf=configs.get(r['branch_id'])
        if conf and str(r['account_id']) in {str(v) for v in conf.payment_methods.values() if v}:balances[r['branch_id']]['cash']+=net
        if conf and str(r['account_id'])==str(conf.accounts.get('receivable')):balances[r['branch_id']]['receivable']+=net
        if conf and str(r['account_id'])==str(conf.accounts.get('payable')):balances[r['branch_id']]['payable']-=net
    stock={r['warehouse__branch_id']:r['v'] or ZERO for r in StockMovement.objects.filter(warehouse__branch__in=shops,posting_date__lte=end).values('warehouse__branch_id').annotate(v=Sum('value'))}
    purchases={r['branch_id']:r['t'] or ZERO for r in Document.objects.filter(branch__in=shops,status='Submitted',kind='Purchase Invoice',posting_date__range=[start,end]).values('branch_id').annotate(t=Sum('total'))}
    sold={shop.pk:Device.objects.filter(branch=shop,status='Sold',sold_on__range=[start,end]).count() for shop in shops}
    rows=[]
    for shop in shops:
        p=per[shop.pk];b=balances[shop.pk];income=p['income'];gross=income-p['cogs'];net=gross-p['expense']
        rows.append({'Shop':shop.name,'Net sales':income,'Purchases':purchases.get(shop.pk,ZERO),'Gross profit':gross,'Gross margin %':(gross*100/income).quantize(Decimal('0.1')) if income else ZERO,'Expenses':p['expense'],'Net profit':net,
                     'Stock value':stock.get(shop.pk,ZERO),'Cash and bank':b['cash'],'Customers owe':b['receivable'],'You owe suppliers':b['payable'],'Phones sold':sold[shop.pk]})
    return sorted(rows,key=lambda r:-r['Net profit'])


def run(name,shops,start,end,params,with_costs):
    shops=list(shops)
    if name in SALES:rows=sales_summary(name,shops,start,end)
    elif name=='Expense Report':rows=expense_report(shops,start,end)
    elif name=='Low Stock':rows=low_stock(shops)
    elif name=='IMEI Report':rows=imei_report(shops,params,with_costs)
    elif name=='Exchange Report':rows=exchange_report(shops,start,end)
    elif name=='Stock Transfer Report':rows=transfer_report(shops,start,end)
    elif name=='Branch Comparison':rows=branch_comparison(shops,start,end)
    else:raise KeyError(name)
    return COLUMNS[name],rows
