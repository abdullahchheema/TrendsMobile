"""Stable report identities with the original shop-facing names and locations."""
from . import access

LABELS={'Mobile Inventory':'Phone Stock','Mobile Stock Ageing':'Stock Age & Prices','Mobile Device Economics':'Device Costs & Margin',
    'Accounts Receivable':'Customer Balances','Accounts Payable':'Supplier Balances'}
GROUPS=[('Stock & devices',['Mobile Inventory','Mobile Stock Ageing','Device Profit','Mobile Device Economics','Stock Balance','Stock Ledger']),
    ('Sales & purchases',['Sales Register','Purchase Register']),('Staff',['Staff Activity','Staff Summary']),
    ('Financial statements',['Profit and Loss Statement','Balance Sheet','Cash Flow Statement','Trial Balance','Unadjusted Trial Balance','Adjusted Trial Balance']),
    ('Accounts & payments',['Accounts Receivable','Accounts Payable','Payment Ledger','General Ledger'])]
DESCRIPTIONS={
    'Cash Flow Statement':'Where cash came from and went: operating, investing and financing activities, proved against the actual change in cash and bank balances.',
    'Unadjusted Trial Balance':'Trial balance before the owner or accountant adjusting journals and before year-end closing entries.',
    'Adjusted Trial Balance':'Trial balance including adjusting journals, before year-end closing entries.',
    'Profit and Loss Statement':'Ledger income and expenses for the selected period, including returns, repair expenses and posted adjustments. Accrual basis; not cash collected.',
    'Balance Sheet':'Ledger assets, liabilities and equity through the selected date, including unclosed profit/loss. Uses posted ledger valuation, not current asking prices.',
    'Trial Balance':'Account opening balances, period debits/credits and closing balances. Includes reversal entries and unclassified accounts; does not hide an imbalance.',
    'Mobile Inventory':'Individual phones, PTA / Non-PTA status, condition, warehouse and asking price. Purchase costs follow your role.',
    'Mobile Stock Ageing':'Available/reserved phones, time in stock and prices needing review.',
    'Mobile Device Economics':'Lifetime posted sales less stock cost and device repair expenses. Includes unsold devices with repair costs; excludes unallocated overheads.',
    'Device Profit':'Posted device sales and returns, net revenue, stock cost and margin before overheads.',
    'Staff Activity':'Posted buying, selling and payment activity by responsible staff. Sales and collections are separate activities and must not be added together.',
    'Staff Summary':'Net purchases and sales by staff, with actual collections, payouts and internal transfer handling shown separately.',
    'Sales Register':'Submitted sales and return credits, discounts, tax, invoice total and outstanding amounts.',
    'Purchase Register':'Submitted supplier invoices and their outstanding balances.',
    'Accounts Receivable':'Customer balances from ledger entries through the selected date, including advances and credits.',
    'Accounts Payable':'Supplier balances from ledger entries through the selected date, including advances and credits.',
    'General Ledger':'Account debits and credits for the selected dates. Open a linked source document where your role permits.',
    'Payment Ledger':'Party-linked ledger entries for customer/supplier collections, payments and allocations.',
    'Stock Balance':'Item quantities and valuation by warehouse through the selected date.',
    'Stock Ledger':'Dated stock movements by item, warehouse and source document; valuation follows your role.'}


def label(name):return LABELS.get(name,name)


def visible(user):
    from .reports import authorize
    from django.core.exceptions import PermissionDenied
    result=[]
    for name in access.REPORTS:
        try:authorize(name,user)
        except PermissionDenied:continue
        result.append(dict(name=name,label=label(name),description=DESCRIPTIONS.get(name,'')))
    return result


def groups(user,search=''):
    query=search.casefold().strip()
    reports={item['name']:item for item in visible(user) if not query or query in (' '.join([item['name'],item['label'],item['description']])).casefold()}
    return [dict(title=title,reports=[reports[name] for name in names if name in reports]) for title,names in GROUPS if any(name in reports for name in names)]
