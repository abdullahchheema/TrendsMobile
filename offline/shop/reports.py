from collections import defaultdict
from datetime import date
from django.db.models import Sum
from django.utils import timezone
from django.core.exceptions import PermissionDenied,ValidationError
from .models import *
from .access import branches,costs,manager,statements,REPORTS,STATEMENTS
from .services import money,ZERO
from .pricing import line_net,net_amount

def authorize(name,user):
    if name not in REPORTS:raise ValidationError('Unknown report.')
    if name in ('Device Profit','Mobile Device Economics','Purchase Register','Accounts Payable','General Ledger','Branch Comparison','Profit and Loss Statement','Balance Sheet','Trial Balance') and not costs(user):raise PermissionDenied('Purchase costs and financial reports require purchasing or management access.')
    if name in STATEMENTS and not statements(user):raise PermissionDenied('Financial statements require management access.')
    if (name.startswith('Staff ') or name=='Mobile Device Economics') and not manager(user):raise PermissionDenied('This report requires management access.')

def run(name,user,params):
    authorize(name,user)
    shops=branches(user)
    if params.get('branch'):shops=shops.filter(pk=params['branch'])
    try:
        start=date.fromisoformat(params.get('from_date') or '2000-01-01')
        end=date.fromisoformat(params.get('to_date') or str(timezone.localdate()))
    except ValueError:raise ValidationError('Enter valid report dates.')
    if start>end:raise ValidationError('From date must not be after To date.')
    from . import financial_reports
    if name in financial_reports.NAMES:return financial_reports.run(name,shops,start,end)
    from . import report_extra
    if name in report_extra.COLUMNS:return report_extra.run(name,shops,start,end,params,costs(user))
    docs=Document.objects.filter(branch__in=shops,status='Submitted',posting_date__range=[start,end]).select_related('branch','customer','supplier','buyer','seller','handler')
    if params.get('staff'):docs=docs.filter(Q(buyer_id=params['staff'])|Q(seller_id=params['staff'])|Q(handler_id=params['staff']))
    if params.get('party'):docs=docs.filter(Q(customer_id=params['party'])|Q(supplier_id=params['party']))
    devices=Device.objects.filter(branch__in=shops).select_related('branch','product__model','warehouse').prefetch_related('imeis')
    if params.get('warehouse'):devices=devices.filter(warehouse_id=params['warehouse'])
    if params.get('model'):devices=devices.filter(product__model_id=params['model'])
    if params.get('product'):devices=devices.filter(product_id=params['product'])
    if params.get('status'):devices=devices.filter(status=params['status'])
    if params.get('pta_status'):devices=devices.filter(pta_status=params['pta_status'])
    rows=[]
    if name in ('Mobile Inventory','Mobile Stock Ageing','Mobile Device Economics'):
        if name=='Mobile Stock Ageing':devices=devices.filter(status__in=['Available','Reserved'])
        for d in devices:
            row={'Device':d.code,'Shop':f'{d.branch.name} ({d.branch.code})','Model':d.product.model.name if d.product.model_id else d.product.name,'Storage':d.product.storage,
                'Colour':d.product.colour,'PTA status':d.pta_status,'Status':d.status,
                'Warehouse':str(d.warehouse or ''),'Condition':d.condition,'Asking price':d.asking_price,'Days in stock':max(0,(end-d.received_on).days)}
            if costs(user):row['Purchase cost']=d.purchase_cost
            if name=='Mobile Stock Ageing':
                config=ShopSettings.objects.filter(branch=d.warehouse.branch).first() if d.warehouse_id else None
                threshold=config.ageing_days if config else 30
                row['Received']=d.received_on;row['Action']='Set asking price' if not d.asking_price else 'Review price / slow stock' if row['Days in stock']>=threshold else 'Current'
                if params.get('review_only') and row['Action']=='Current':continue
            if name=='Mobile Device Economics':
                expenses=sum((money(l.quantity*l.rate) for l in Line.objects.filter(device=d,document__kind='Purchase Invoice',document__status='Submitted',document__reference__isnull=True)),ZERO)
                net_sales=ZERO;stock_cost=ZERO
                for line in Line.objects.filter(device=d,document__kind__in=['Sales Invoice','POS Invoice'],document__status='Submitted').select_related('document'):
                    if line.document.source.get('is_consolidated'):continue
                    sign=-1 if line.document.is_return else 1
                    net_sales+=line_net(line);stock_cost+=sign*money(line.quantity*line.cost)
                row={'Device':d.code,'Shop':f'{d.branch.name} ({d.branch.code})','Phone':d.product.name,'Status':d.status,'Current / Last Valuation':d.purchase_cost,
                    'Expensed Repairs (Net)':expenses,'Valuation + Repairs':d.purchase_cost+expenses,'Lifetime Net Sales':net_sales,
                    'Net Posted Stock Cost':stock_cost,'Posted Margin After Repairs':net_sales-stock_cost-expenses}
            rows.append(row)
        if name=='Mobile Stock Ageing':rows.sort(key=lambda row:row['Days in stock'],reverse=True)
    elif name=='Device Profit':
        trades=Line.objects.filter(document__in=docs.filter(kind__in=['Sales Invoice','POS Invoice']),device__isnull=False)
        if params.get('warehouse'):trades=trades.filter(Q(warehouse_id=params['warehouse'])|Q(warehouse__isnull=True,document__warehouse_id=params['warehouse']))
        if params.get('model'):trades=trades.filter(product__model_id=params['model'])
        if params.get('product'):trades=trades.filter(product_id=params['product'])
        for line in trades.select_related('device','document','product'):
            if line.document.source.get('is_consolidated'):continue
            sign=-1 if line.document.is_return else 1
            rows.append({'Date':line.document.posting_date,'Shop':f'{line.document.branch.name} ({line.document.branch.code})','Invoice':line.document.code,'Device':line.device.code,
                'Model':line.product.name,'Sale':line_net(line),'Cost':sign*line.quantity*line.cost,'Margin':line_net(line)-sign*line.quantity*line.cost})
    elif name in ('Purchase Register','Sales Register'):
        kinds=['Purchase Invoice'] if name=='Purchase Register' else ['Sales Invoice','POS Invoice']
        for d in docs.filter(kind__in=kinds):
            if d.source.get('is_consolidated'):continue
            row={'Date':d.posting_date,'Invoice':d.code,'Party':str(d.supplier or d.customer or '')}
            if name=='Sales Register':
                captured=d.data.get('pricing',{});row.update({'Item subtotal':money(captured['subtotal']) if captured.get('version')==1 else d.total,'Invoice discount':money(captured.get('discount')),'Net sales before tax':net_amount(d),'Sales tax':money(captured.get('tax_total'))})
            row.update({'Total':d.total,'Outstanding':d.outstanding,'Staff':d.staff_names.get('buyer' if name=='Purchase Register' else 'seller','')});rows.append(row)
    elif name in ('Accounts Receivable','Accounts Payable'):
        key='receivable' if name=='Accounts Receivable' else 'payable'
        account_ids=[s.accounts.get(key) for s in ShopSettings.objects.filter(branch__in=shops) if s.accounts.get(key)]
        entries=LedgerEntry.objects.filter(branch__in=shops,account_id__in=account_ids,posting_date__lte=end).select_related('party','reference','document')
        if params.get('party'):entries=entries.filter(party_id=params['party'])
        grouped={}
        for e in entries:
            if not e.party_id:continue
            reference=e.reference or e.document
            group=(e.party_id,reference.pk)
            if group not in grouped:grouped[group]={'Party':str(e.party),'Document':reference.code,'Date':reference.posting_date,'Outstanding':ZERO,'Age days':max(0,(end-reference.posting_date).days)}
            grouped[group]['Outstanding']+=(e.debit-e.credit) if key=='receivable' else (e.credit-e.debit)
        rows=[r for r in grouped.values() if r['Outstanding']]
    elif name in ('General Ledger','Payment Ledger'):
        ledger=LedgerEntry.objects.filter(branch__in=shops,posting_date__range=[start,end]).select_related('account','document','party','reference','branch').order_by('posting_date','id')
        if name=='Payment Ledger':ledger=ledger.filter(party__isnull=False)
        if params.get('party'):ledger=ledger.filter(party_id=params['party'])
        if params.get('account'):ledger=ledger.filter(account_id=params['account'])
        for e in ledger:rows.append({'Date':e.posting_date,'Shop':f'{e.branch.name} ({e.branch.code})','Document':e.document.code,'Account':e.account.name,'Party':str(e.party or ''),'Reference':str(e.reference or ''),'Debit':e.debit,'Credit':e.credit})
    elif name in ('Stock Balance','Stock Ledger'):
        stock=StockMovement.objects.filter(warehouse__branch__in=shops,posting_date__lte=end).values('product__code','product__name','warehouse_id','warehouse__code','warehouse__name','warehouse__branch__name','warehouse__branch__code').annotate(q=Sum('quantity'),v=Sum('value')).order_by('product__code','warehouse__branch__code','warehouse__name')
        if params.get('warehouse'):stock=stock.filter(warehouse_id=params['warehouse'])
        if params.get('product'):stock=stock.filter(product_id=params['product'])
        for r in stock:
            if r['q'] or r['v']:
                row={'Item':r['product__code'],'Name':r['product__name'],'Shop':f"{r['warehouse__branch__name']} ({r['warehouse__branch__code']})",'Warehouse':r['warehouse__name'],'Warehouse code':r['warehouse__code'],'Quantity':r['q']}
                if costs(user):row['Stock value']=r['v']
                rows.append(row)
        if name=='Stock Ledger':
            moves=StockMovement.objects.filter(warehouse__branch__in=shops,posting_date__range=[start,end]).select_related('document','product','warehouse__branch').order_by('posting_date','id')
            if params.get('product'):moves=moves.filter(product_id=params['product'])
            if params.get('warehouse'):moves=moves.filter(warehouse_id=params['warehouse'])
            rows=[]
            for move in moves:
                row={'Date':move.posting_date,'Document':move.document.code,'Item':move.product.code,'Shop':f'{move.warehouse.branch.name} ({move.warehouse.branch.code})','Warehouse':move.warehouse.name,'Warehouse code':move.warehouse.code,'Quantity change':move.quantity}
                if costs(user):row['Value change']=move.value
                rows.append(row)
    elif name in ('Staff Activity','Staff Summary'):
        summary=defaultdict(lambda:dict(phones_bought=ZERO,phones_sold=ZERO,purchases=ZERO,sales=ZERO,collected=ZERO,paid=ZERO,transfers=ZERO))
        def activity(d,person,name,amount,qty=ZERO,mode=''):
            if params.get('staff') and (not person or str(person.pk)!=params['staff']):return
            field='buyer' if name in ('Purchase','Purchase Return') else 'seller' if name in ('Sale','Sales Return') else 'handler'
            person_name=d.staff_names.get(field) or (person.name if person else 'Unassigned / historical')
            person_code=person.code if person else ''
            totals=summary[(d.branch_id,person.pk if person else None)]
            totals.setdefault('name',person_name if person else 'Unassigned / historical');totals.setdefault('code',person_code);totals.setdefault('shop',d.branch.name)
            column={'Purchase':'purchases','Purchase Return':'purchases','Sale':'sales','Sales Return':'sales','Collection':'collected','Payment / Refund':'paid','Internal transfer':'transfers'}[name]
            totals[column]+=amount
            if name in ('Purchase','Purchase Return'):totals['phones_bought']+=qty
            if name in ('Sale','Sales Return'):totals['phones_sold']+=qty
            rows.append({'Date':d.posting_date,'Staff ID':person_code,'Staff Name':person_name,'Shop':d.branch.name,'Activity':name,'Document Type':d.kind,
                'Document':d.code,'Phones (Net Qty)':qty,'Amount':amount,'Payment Method':mode,
                'Posted / Recorded by Login':d.source.get('covison_posted_by') or (d.posted_by.get_username() if d.posted_by_id else d.source.get('owner',''))})
        for d in docs:
            sign=-1 if d.is_return else 1
            qty=sum((l.quantity for l in d.lines.select_related('product') if l.device_id or (l.product.serialized and d.kind=='Purchase Receipt')),ZERO)*sign
            if d.kind=='Purchase Receipt' and qty:activity(d,d.buyer,'Purchase Return' if d.is_return else 'Purchase',d.total,qty)
            elif d.kind in ('Sales Invoice','POS Invoice') and not d.source.get('is_consolidated'):
                activity(d,d.seller,'Sales Return' if d.is_return else 'Sale',net_amount(d),qty)
                if d.source.get('is_pos') or d.kind=='POS Invoice' and d.source:
                    paid=money(d.source.get('base_paid_amount'))-money(d.source.get('base_change_amount'))
                    if paid:activity(d,d.handler,'Payment / Refund' if d.is_return else 'Collection',abs(paid),mode='POS split payments')
            elif d.kind=='Payment Entry' and d.data.get('direction')!='Internal Transfer':activity(d,d.handler,'Collection' if d.data.get('direction')=='Receive' else 'Payment / Refund',d.total,mode=d.data.get('mode',''))
            elif d.kind=='Payment Entry' and d.data.get('direction')=='Internal Transfer':activity(d,d.handler,'Internal transfer',d.total,mode=d.data.get('mode',''))
        if name=='Staff Summary':rows=[{'Staff ID':v['code'],'Staff Name':v['name'],'Shop':v['shop'],'Phones Bought (Net)':v['phones_bought'],'Phones Sold (Net)':v['phones_sold'],
            'Net Device Purchases':v['purchases'],'Net Sales':v['sales'],'Money Collected':v['collected'],'Money Paid / Refunded':v['paid'],'Internal Transfers Handled':v['transfers']} for v in sorted(summary.values(),key=lambda row:(row['shop'],row['name'],row['code']))]
    return list(rows[0]) if rows else empty_columns(name,costs(user)),rows

def empty_columns(name,with_costs):
    from . import financial_reports
    if name in financial_reports.NAMES:return financial_reports.columns(name)
    from . import report_extra
    if name in report_extra.COLUMNS:return report_extra.COLUMNS[name]
    inventory=['Device','Shop','Model','Storage','Colour','PTA status','Status','Warehouse','Condition','Asking price','Days in stock']+(['Purchase cost'] if with_costs else [])
    return {
        'Mobile Inventory':inventory,'Mobile Stock Ageing':inventory+['Received','Action'],
        'Mobile Device Economics':['Device','Shop','Phone','Status','Current / Last Valuation','Expensed Repairs (Net)','Valuation + Repairs','Lifetime Net Sales','Net Posted Stock Cost','Posted Margin After Repairs'],
        'Device Profit':['Date','Shop','Invoice','Device','Model','Sale','Cost','Margin'],
        'Purchase Register':['Date','Invoice','Party','Total','Outstanding','Staff'],'Sales Register':['Date','Invoice','Party','Item subtotal','Invoice discount','Net sales before tax','Sales tax','Total','Outstanding','Staff'],
        'Accounts Receivable':['Party','Document','Date','Outstanding','Age days'],'Accounts Payable':['Party','Document','Date','Outstanding','Age days'],
        'General Ledger':['Date','Shop','Document','Account','Party','Reference','Debit','Credit'],'Payment Ledger':['Date','Shop','Document','Account','Party','Reference','Debit','Credit'],
        'Stock Balance':['Item','Name','Shop','Warehouse','Warehouse code','Quantity']+(['Stock value'] if with_costs else []),
        'Stock Ledger':['Date','Document','Item','Shop','Warehouse','Warehouse code','Quantity change']+(['Value change'] if with_costs else []),
        'Staff Activity':['Date','Staff ID','Staff Name','Shop','Activity','Document Type','Document','Phones (Net Qty)','Amount','Payment Method','Posted / Recorded by Login'],
        'Staff Summary':['Staff ID','Staff Name','Shop','Phones Bought (Net)','Phones Sold (Net)','Net Device Purchases','Net Sales','Money Collected','Money Paid / Refunded','Internal Transfers Handled'],
    }.get(name,[])
