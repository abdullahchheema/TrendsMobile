"""Read-only report search, sorting, totals, links and date shortcuts."""
from datetime import date,datetime,timedelta
from decimal import Decimal
from urllib.parse import urlencode
from django.urls import reverse
from django.db.models import Q
from django.utils import timezone
from . import access,report_catalog

MONEY={
 'Profit and Loss Statement':{'Amount'},'Balance Sheet':{'Amount'},
 'Trial Balance':{'Opening debit','Opening credit','Period debit','Period credit','Closing debit','Closing credit'},
 'Mobile Inventory':{'Asking price','Purchase cost'},'Mobile Stock Ageing':{'Asking price','Purchase cost'},
 'Mobile Device Economics':{'Current / Last Valuation','Expensed Repairs (Net)','Valuation + Repairs','Lifetime Net Sales','Net Posted Stock Cost','Posted Margin After Repairs'},
 'Device Profit':{'Sale','Cost','Margin'},'Purchase Register':{'Total','Outstanding'},
 'Sales Register':{'Item subtotal','Invoice discount','Net sales before tax','Sales tax','Total','Outstanding'},
 'Accounts Receivable':{'Outstanding'},'Accounts Payable':{'Outstanding'},
 'General Ledger':{'Debit','Credit'},'Payment Ledger':{'Debit','Credit'},'Stock Balance':{'Stock value'},'Stock Ledger':{'Value change'},
 'Staff Summary':{'Net Device Purchases','Net Sales','Money Collected','Money Paid / Refunded','Internal Transfers Handled'},'Staff Activity':{'Amount'}}
QUANTITIES={'Staff Summary':{'Phones Bought (Net)','Phones Sold (Net)'},'Stock Balance':{'Quantity'},'Stock Ledger':{'Quantity change'}}


def select_rows(columns,rows,params):
    query=str(params.get('search','')).strip().casefold()
    selected=[row for row in rows if not query or any(query in str(row.get(column,'')).casefold() for column in columns)]
    column=params.get('sort_by')
    if column in columns:
        def key(row):
            value=row.get(column)
            if isinstance(value,(int,float,Decimal)):return (0,Decimal(str(value)))
            if isinstance(value,(date,datetime)):return (1,value.isoformat())
            return (2,str(value or '').casefold())
        filled=[row for row in selected if row.get(column) is not None];empty=[row for row in selected if row.get(column) is None]
        selected=sorted(filled,key=key,reverse=params.get('sort_dir')=='desc')+empty
    return selected


def display(value,column,name):
    if value is None:return '—'
    if isinstance(value,(date,datetime)):return value.isoformat()
    if column in MONEY.get(name,set()) and isinstance(value,(int,float,Decimal)):return format(value,',.2f')
    if isinstance(value,Decimal):return format(value,'f').rstrip('0').rstrip('.') if '.' in format(value,'f') else str(value)
    return str(value)


def context(name,columns,rows,params,documents,devices,user):
    totals={}
    if name not in ('Staff Activity','Profit and Loss Statement','Balance Sheet'):
        for column in columns:
            if column in MONEY.get(name,set())|QUANTITIES.get(name,set()):totals[column]=sum((Decimal(str(row[column])) for row in rows if isinstance(row.get(column),(int,float,Decimal))),Decimal(0))
    # Resolve only identities the caller may actually open; never link forbidden records.
    names={str(row.get(column,'')) for row in rows for column in ('Invoice','Document','Reference')}
    docs=documents.filter(code__in=names)
    if not access.costs(user):
        from .services import BUY
        docs=docs.exclude(kind__in=BUY)
    if not access.manager(user):docs=docs.exclude(kind='Stock Entry')
    if not user.is_superuser:docs=docs.filter(Q(data__manual_journal__isnull=True)|~Q(data__manual_journal__version=1))
    person=access.staff(user)
    if person and not person.manager and not person.can_handle_payments:docs=docs.filter(Q(data__direction__isnull=True)|~Q(data__direction='Internal Transfer'))
    doc_urls={doc.code:reverse('detail',args=[doc.pk]) for doc in docs}
    phone_urls={phone.code:reverse('device',args=[phone.pk]) for phone in devices.filter(code__in={row.get('Device','') for row in rows})}
    account_urls={}
    if name in ('Profit and Loss Statement','Balance Sheet','Trial Balance'):
        from .models import Account
        selected={row.get('Account code') for row in rows if row.get('Account code')}
        dates={key:params[key] for key in ('branch','from_date','to_date') if params.get(key)}
        if name=='Balance Sheet':dates['from_date']='0001-01-01'
        account_urls={account.name+'|'+account.code:reverse('report',args=['General Ledger'])+'?'+urlencode({**dates,'account':str(account.pk)}) for account in Account.objects.filter(code__in=selected)}
    displayed=[]
    for row in rows:
        cells=[]
        for column in columns:
            value=row.get(column);url=phone_urls.get(str(value)) if column=='Device' else doc_urls.get(str(value)) if column in ('Invoice','Document','Reference') else None
            if column=='Account' and account_urls:url=account_urls.get(str(value)+'|'+row.get('Account code',''))
            cells.append(dict(value=display(value,column,name),url=url,numeric=column in MONEY.get(name,set())|QUANTITIES.get(name,set())))
        displayed.append(cells)
    headers=[]
    for column in columns:
        direction='desc' if params.get('sort_by')==column and params.get('sort_dir','asc')=='asc' else 'asc'
        headers.append(dict(label=column,url='?'+urlencode({**params,'sort_by':column,'sort_dir':direction}),active=params.get('sort_by')==column,direction=params.get('sort_dir','asc')))
    footer=[dict(value='Filtered total' if index==0 else display(totals[column],column,name) if column in totals else '',numeric=column in totals) for index,column in enumerate(columns)] if totals and rows else []
    today=timezone.localdate();month=today.replace(day=1);previous=month-timedelta(days=1)
    presets=[]
    from .report_filters import FIELDS
    if 'from_date' in FIELDS.get(name,[]):
        for title,start,end in [('This month',month,today),('Last month',previous.replace(day=1),previous),('All history',date(2000,1,1),today)]:
            presets.append(dict(label=title,url='?'+urlencode({**params,'from_date':start.isoformat(),'to_date':end.isoformat()})))
    elif 'to_date' in FIELDS.get(name,[]):
        for title,end in [('As of today',today),('End of last month',previous)]:presets.append(dict(label=title,url='?'+urlencode({**params,'to_date':end.isoformat()})))
    if name=='Mobile Inventory':presets.append(dict(label='All stock statuses',url='?'+urlencode({**params,'status':''})))
    if name=='Mobile Stock Ageing':presets.append(dict(label='All available / reserved phones',url='?'+urlencode({**params,'review_only':'0'})))
    return dict(display_rows=displayed,report_headers=headers,report_totals=footer,date_presets=presets,report_label=report_catalog.label(name),report_description=report_catalog.DESCRIPTIONS.get(name,''),report_name=name,
        all_history_url=next((item['url'] for item in presets if item['label']=='All history'),None))
