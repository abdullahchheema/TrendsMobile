"""Read-only financial statements derived from the same posted ledger as GL.

No balancing entries are created. Unclassified accounts block statements instead
of silently being omitted; Trial Balance remains available for investigation.
"""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Sum
from .models import Account,LedgerEntry,ShopSettings,SalesTaxRule

NAMES=('Profit and Loss Statement','Balance Sheet','Trial Balance')
ROOTS=('Asset','Liability','Equity','Income','Expense')
TYPES={
    'Cash':'Asset','Bank':'Asset','Receivable':'Asset','Stock':'Asset','Current Asset':'Asset','Fixed Asset':'Asset',
    'Payable':'Liability','Stock Received But Not Billed':'Liability','Current Liability':'Liability',
    'Equity':'Equity','Income Account':'Income','Expense Account':'Expense','Cost of Goods Sold':'Expense',
    'Depreciation':'Expense','Accumulated Depreciation':'Asset',
}
CONFIG={'receivable':'Asset','inventory':'Asset','inter_shop':'Asset','payable':'Liability',
    'stock_received':'Liability','sales':'Income','cogs':'Expense','expense':'Expense'}
ZERO=Decimal('0.00')


def classifications():
    mapped=defaultdict(set)
    for config in ShopSettings.objects.all():
        for key,value in config.accounts.items():
            if value and key in CONFIG:mapped[str(value)].add(CONFIG[key])
        for value in config.payment_methods.values():mapped[str(value)].add('Asset')
    for account_id in SalesTaxRule.objects.values_list('account_id',flat=True):mapped[str(account_id)].add('Liability')
    result={}
    for account in Account.objects.all():
        root=account.root_type or account.source.get('root_type')
        if root not in ROOTS:root=TYPES.get(account.account_type)
        candidates=mapped[str(account.pk)]
        if not root and len(candidates)==1:root=next(iter(candidates))
        result[account.pk]=(account,root or 'Unclassified')
    return result


def run(name,shops,start,end):
    entries=LedgerEntry.objects.filter(branch__in=shops,posting_date__lte=end)
    accounts=classifications();rows=[]
    if name=='Trial Balance':
        opening={r['account_id']:r for r in entries.filter(posting_date__lt=start).values('account_id').annotate(dr=Sum('debit'),cr=Sum('credit'))}
        period={r['account_id']:r for r in entries.filter(posting_date__gte=start).values('account_id').annotate(dr=Sum('debit'),cr=Sum('credit'))}
        for key in opening.keys()|period.keys():
            account,root=accounts[key];before=opening.get(key,{});current=period.get(key,{})
            initial=before.get('dr',ZERO)-before.get('cr',ZERO);dr=current.get('dr',ZERO);cr=current.get('cr',ZERO);closing=initial+dr-cr
            rows.append({'Account code':account.code,'Account':account.name,'Section':root,
                'Opening debit':max(initial,ZERO),'Opening credit':max(-initial,ZERO),'Period debit':dr,'Period credit':cr,
                'Closing debit':max(closing,ZERO),'Closing credit':max(-closing,ZERO)})
    else:
        if name=='Profit and Loss Statement':entries=entries.filter(posting_date__gte=start)
        totals=list(entries.values('account_id').annotate(dr=Sum('debit'),cr=Sum('credit')))
        unknown=[accounts[r['account_id']][0].code for r in totals if accounts[r['account_id']][1]=='Unclassified' and (r['dr'] or r['cr'])]
        if unknown:raise ValidationError('Financial statement needs account classifications. Review these account types and shop posting mappings: '+', '.join(sorted(unknown))+'. Trial Balance and General Ledger remain available; no accounts have been omitted.')
        earnings=ZERO
        for total in totals:
            account,root=accounts[total['account_id']];net=total['dr']-total['cr']
            if root=='Income':earnings-=net
            elif root=='Expense':earnings-=net
            if name=='Profit and Loss Statement' and root not in ('Income','Expense'):continue
            if name=='Balance Sheet' and root not in ('Asset','Liability','Equity'):continue
            amount=net if root in ('Asset','Expense') else -net
            rows.append({'Section':root,'Account code':account.code,'Account':account.name,'Amount':amount})
        if name=='Balance Sheet' and earnings:
            rows.append({'Section':'Equity','Account code':'','Account':'Unclosed profit / loss','Amount':earnings})
    order={root:index for index,root in enumerate(ROOTS)}
    rows.sort(key=lambda row:(order.get(row['Section'],99),row['Account code'],row['Account']))
    return columns(name),rows


def columns(name):
    if name=='Trial Balance':return ['Account code','Account','Section','Opening debit','Opening credit','Period debit','Period credit','Closing debit','Closing credit']
    return ['Section','Account code','Account','Amount']


def summary(name,rows):
    if name not in NAMES:return []
    if name=='Trial Balance':
        dr=sum((r['Closing debit'] for r in rows),ZERO);cr=sum((r['Closing credit'] for r in rows),ZERO)
        return [('Closing debit',dr),('Closing credit',cr),('Debit less credit',dr-cr)]
    totals={root:sum((r['Amount'] for r in rows if r['Section']==root),ZERO) for root in ROOTS}
    if name=='Profit and Loss Statement':return [('Income',totals['Income']),('Expenses',totals['Expense']),('Net profit / loss',totals['Income']-totals['Expense'])]
    return [('Assets',totals['Asset']),('Liabilities',totals['Liability']),('Equity incl. unclosed earnings',totals['Equity']),('Assets less liabilities and equity',totals['Asset']-totals['Liability']-totals['Equity'])]
