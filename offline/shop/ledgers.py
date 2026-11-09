"""Ledgers with opening and running balances: any account, cash book, bank book, and customer / supplier statements."""
import csv
from datetime import date,timedelta
from decimal import Decimal
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Sum
from django.http import HttpResponse,Http404
from django.shortcuts import get_object_or_404,render
from django.utils import timezone
from .models import Account,Document,LedgerEntry,Party,ShopSettings
from . import access,permissions,services as s

ZERO=Decimal('0.00')
CREDIT_NATURE=('Liability','Equity','Income')
BUCKETS=(('Current (0-30 days)',0,30),('31-60 days',31,60),('61-90 days',61,90),('Over 90 days',91,10**6))


def _csv_cell(value):
    return ("'"+value) if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) else value


def _dates(request):
    try:start=date.fromisoformat(request.GET.get('from_date') or '2000-01-01');end=date.fromisoformat(request.GET.get('to_date') or str(timezone.localdate()))
    except ValueError:raise Http404('Enter valid dates.')
    return start,end


def build(entries,start,end,credit_nature):
    """Opening balance, period rows with a running balance, and totals. Balance is shown on the account's natural side."""
    sign=-1 if credit_nature else 1
    opening=sum((e['debit']-e['credit'] for e in entries.filter(posting_date__lt=start).values('debit','credit')),ZERO)*sign
    running=opening;rows=[];debit=credit=ZERO
    for e in entries.filter(posting_date__gte=start,posting_date__lte=end).select_related('document','account','party','branch').order_by('posting_date','pk'):
        running+=(e.debit-e.credit)*sign;debit+=e.debit;credit+=e.credit
        rows.append(dict(date=e.posting_date,document=e.document,code=e.document.code,description=e.document.notes or e.document.kind,account=e.account.name,party=e.party.name if e.party_id else '',shop=e.branch.name,debit=e.debit,credit=e.credit,balance=running))
    return dict(opening=opening,rows=rows,debit=debit,credit=credit,closing=running)


def _respond_csv(name,header,rows):
    response=HttpResponse(content_type='text/csv');response['Content-Disposition']=f'attachment; filename="{name}.csv"'
    writer=csv.writer(response);writer.writerow(header)
    for r in rows:writer.writerow([_csv_cell(v) for v in r])
    return response


@login_required
def ledger(request):
    """/accounting/ledger/?account=<id>  or  ?book=cash|bank   (+ branch, from_date, to_date, format=csv)"""
    if not access.statements(request.user):raise PermissionDenied('Ledgers need access to financial statements.')
    start,end=_dates(request);places=access.branches(request.user)
    branch=places.filter(pk=request.GET['branch']).first() if request.GET.get('branch') else None
    book=request.GET.get('book','');account=None
    entries=LedgerEntry.objects.filter(branch__in=([branch] if branch else places))
    if book in ('cash','bank'):
        entries=entries.filter(account__account_type='Cash' if book=='cash' else 'Bank');title='Cash book' if book=='cash' else 'Bank and wallet book';credit=False
    elif request.GET.get('account'):
        account=get_object_or_404(Account,pk=request.GET['account']);entries=entries.filter(account=account);title=f'Ledger: {account.name}';credit=account.root_type in CREDIT_NATURE
    else:
        title='Ledger';credit=False;entries=entries.none()
    data=build(entries,start,end,credit)
    if request.GET.get('format')=='csv':
        return _respond_csv('ledger',['Date','Reference','Description','Account','Party','Shop','Debit','Credit','Balance'],[[r['date'],r['code'],r['description'],r['account'],r['party'],r['shop'],r['debit'],r['credit'],r['balance']] for r in data['rows']])
    return render(request,'ledger.html',dict(title=title,data=data,account=account,book=book,branch=branch,shops=places,start=start,end=end,
        accounts=access.visible_accounts(request.user).filter(is_group=False,enabled=True).order_by('code'),nature='credit' if credit else 'debit'))


def party_statement_data(party,start,end,places,branch=None):
    credit_nature=party.kind=='Supplier'                 # a supplier's balance is what we owe (credit); a customer's is what they owe (debit)
    key='payable' if credit_nature else 'receivable'
    control=[c.accounts[key] for c in ShopSettings.objects.all() if c.accounts.get(key)]      # each shop maps its own receivable / payable account
    entries=LedgerEntry.objects.filter(party=party,account_id__in=control,branch__in=[branch] if branch else places)
    data=build(entries,start,end,credit_nature)
    kinds=['Purchase Invoice'] if credit_nature else list(s.SELL)
    open_docs=Document.objects.filter(status='Submitted',kind__in=kinds,**({'supplier':party} if credit_nature else {'customer':party})).exclude(outstanding=0)
    open_docs=open_docs.filter(branch__in=[branch] if branch else places)
    today=timezone.localdate();ageing=[dict(label=l,amount=ZERO) for l,_,_ in BUCKETS];credits=ZERO;lines=[]
    for d in open_docs.order_by('posting_date'):
        age=(today-d.posting_date).days
        if d.outstanding<0:credits+=-d.outstanding;continue
        for bucket,(label,low,high) in zip(ageing,BUCKETS):
            if low<=age<=high:bucket['amount']+=d.outstanding;break
        lines.append(dict(document=d,age=age,outstanding=d.outstanding))
    data.update(ageing=ageing,credits=credits,open_documents=lines,due=sum((b['amount'] for b in ageing),ZERO))
    return data


@login_required
def party_statement(request,pk):
    party=get_object_or_404(access.scoped_parties(request.user),pk=pk)
    if not (access.statements(request.user) or permissions.can(request.user,'payments') or access.costs(request.user)):raise PermissionDenied('You do not have access to statements.')
    start,end=_dates(request);places=access.branches(request.user)
    branch=places.filter(pk=request.GET['branch']).first() if request.GET.get('branch') else None
    data=party_statement_data(party,start,end,places,branch)
    if request.GET.get('format')=='csv':
        return _respond_csv('statement-'+party.code,['Date','Reference','Description','Debit','Credit','Balance'],[[r['date'],r['code'],r['description'],r['debit'],r['credit'],r['balance']] for r in data['rows']])
    return render(request,'statement.html',dict(title=f'Statement: {party.name}',party=party,data=data,start=start,end=end,shops=places,branch=branch,today=timezone.localdate(),
        meaning='Amount you owe this supplier' if party.kind=='Supplier' else 'Amount this customer owes you'))
