"""Read-only financial statements derived from the same posted ledger as GL.

No balancing entries are created. Unclassified accounts block statements instead
of silently being omitted; Trial Balance remains available for investigation.
"""
from collections import defaultdict
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Sum
from .models import Account,Document,LedgerEntry,ShopSettings,SalesTaxRule

TB_NAMES=('Trial Balance','Unadjusted Trial Balance','Adjusted Trial Balance')
NAMES=('Profit and Loss Statement','Balance Sheet')+TB_NAMES+('Cash Flow Statement',)
PRE_CLOSING=('Profit and Loss Statement','Unadjusted Trial Balance','Adjusted Trial Balance','Cash Flow Statement')
ROOTS=('Asset','Liability','Equity','Income','Expense')
TYPES={
    'Cash':'Asset','Bank':'Asset','Receivable':'Asset','Stock':'Asset','Current Asset':'Asset','Fixed Asset':'Asset',
    'Payable':'Liability','Stock Received But Not Billed':'Liability','Current Liability':'Liability',
    'Equity':'Equity','Income Account':'Income','Expense Account':'Expense','Cost of Goods Sold':'Expense',
    'Depreciation':'Expense','Accumulated Depreciation':'Asset','Loan':'Liability',
}
CONFIG={'receivable':'Asset','inventory':'Asset','inter_shop':'Asset','payable':'Liability',
    'stock_received':'Liability','sales':'Income','cogs':'Expense','expense':'Expense'}
ZERO=Decimal('0.00')


def classifications():
    from . import reqcache
    return reqcache.remember('classifications',_classifications)


def _classifications():
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


def pre_closing(entries):
    """Year-end closing entries move profit into retained earnings; statements about a period's activity must not see them."""
    return entries.exclude(document_id__in=Document.objects.filter(data__year_end_close=True).values('pk'))


def cash_flow(entries,start,end,accounts):
    """Indirect cash flow statement from the posted ledger (pre-closing). The result is proved against the actual change in
    cash and bank balances; any difference is shown, never hidden."""
    period=entries.filter(posting_date__gte=start,posting_date__lte=end)
    nets={r['account_id']:(r['dr'] or ZERO)-(r['cr'] or ZERO) for r in period.values('account_id').annotate(dr=Sum('debit'),cr=Sum('credit'))}
    unknown=[accounts[k][0].code for k in nets if accounts[k][1]=='Unclassified' and nets[k]]
    if unknown:raise ValidationError('Cash flow needs account classifications. Review these accounts: '+', '.join(sorted(unknown))+'.')
    opening=sum(((r['dr'] or ZERO)-(r['cr'] or ZERO) for r in entries.filter(posting_date__lt=start,account__account_type__in=('Cash','Bank')).values('account_id').annotate(dr=Sum('debit'),cr=Sum('credit'))),ZERO)
    profit=ZERO;depreciation=ZERO;working={};investing=ZERO;financing_loans=ZERO;financing_equity=ZERO;actual=ZERO
    labels={'Receivable':'Customer receivables','Stock':'Stock','Stock Received But Not Billed':'Supplier payables and stock received','Payable':'Supplier payables and stock received'}
    for key,net in nets.items():
        account,root=accounts[key];kind=account.account_type
        if kind in ('Cash','Bank'):actual+=net;continue
        if root in ('Income','Expense'):
            profit-=net
            if kind=='Depreciation':depreciation+=net
            continue
        if kind=='Accumulated Depreciation':continue                   # the non-cash side of depreciation expense
        if kind=='Fixed Asset':investing-=net;continue
        if kind=='Loan':financing_loans-=net;continue
        if root=='Equity':financing_equity-=net;continue
        label=labels.get(kind,'Other current assets' if root=='Asset' else 'Other current liabilities and advances')
        working[label]=working.get(label,ZERO)-net
    order=['Customer receivables','Stock','Other current assets','Supplier payables and stock received','Other current liabilities and advances']
    rows=[('Operating activities','Net profit / loss for the period',profit),('Operating activities','Add back depreciation (non-cash)',depreciation)]
    rows+=[('Operating activities','Change in '+label.lower(),working.get(label,ZERO)) for label in order if working.get(label)]
    operating=profit+depreciation+sum(working.values(),ZERO)
    rows.append(('Operating activities','Net cash from operating activities',operating))
    rows+=[('Investing activities','Fixed assets bought / sold',investing),('Investing activities','Net cash from investing activities',investing)]
    financing=financing_loans+financing_equity
    rows+=[('Financing activities','Owner capital introduced less drawings',financing_equity),('Financing activities','Loans received less repaid',financing_loans),('Financing activities','Net cash from financing activities',financing)]
    change=operating+investing+financing
    rows+=[('Cash position','Net change in cash and bank',change),('Cash position','Cash and bank at start',opening),('Cash position','Cash and bank at end (calculated)',opening+change),
           ('Cash position','Cash and bank at end (ledger)',opening+actual),('Cash position','Unexplained difference (should be zero)',change-actual)]
    return [{'Section':s,'Line':l,'Amount':a} for s,l,a in rows]


def run(name,shops,start,end):
    entries=LedgerEntry.objects.filter(branch__in=shops,posting_date__lte=end)
    accounts=classifications();rows=[]
    if name in PRE_CLOSING:entries=pre_closing(entries)
    if name=='Unadjusted Trial Balance':entries=entries.exclude(document_id__in=Document.objects.filter(data__manual_journal__version=1).values('pk'))
    if name=='Cash Flow Statement':return columns(name),cash_flow(entries,start,end,accounts)
    if name in TB_NAMES:
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
    if name=='Cash Flow Statement':return ['Section','Line','Amount']
    if name in TB_NAMES:return ['Account code','Account','Section','Opening debit','Opening credit','Period debit','Period credit','Closing debit','Closing credit']
    return ['Section','Account code','Account','Amount']


def summary(name,rows):
    if name not in NAMES:return []
    if name=='Cash Flow Statement':
        get=lambda label:next((r['Amount'] for r in rows if r['Line']==label),ZERO)
        return [('Operating',get('Net cash from operating activities')),('Investing',get('Net cash from investing activities')),('Financing',get('Net cash from financing activities')),('Net change in cash',get('Net change in cash and bank'))]
    if name in TB_NAMES:
        dr=sum((r['Closing debit'] for r in rows),ZERO);cr=sum((r['Closing credit'] for r in rows),ZERO)
        return [('Closing debit',dr),('Closing credit',cr),('Debit less credit',dr-cr)]
    totals={root:sum((r['Amount'] for r in rows if r['Section']==root),ZERO) for root in ROOTS}
    if name=='Profit and Loss Statement':return [('Income',totals['Income']),('Expenses',totals['Expense']),('Net profit / loss',totals['Income']-totals['Expense'])]
    return [('Assets',totals['Asset']),('Liabilities',totals['Liability']),('Equity incl. unclosed earnings',totals['Equity']),('Assets less liabilities and equity',totals['Asset']-totals['Liability']-totals['Equity'])]
