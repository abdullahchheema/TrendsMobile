"""Chart of accounts: the standard Pakistani mobile-retail chart, the tree view and balances.

The chart is business-wide. Each shop keeps its own system posting accounts (created when the shop is set up); this
chart adds the accounts an owner or accountant needs for expenses, capital, drawings, loans, fixed assets and so on."""
from collections import defaultdict
from decimal import Decimal
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Sum
from django.shortcuts import redirect,render
from .models import Account,LedgerEntry
from . import permissions,services

# code, name, account_type, root_type, parent code, is_group
STANDARD=[
 ('1000','Assets','','Asset',None,True),
 ('1100','Current assets','','Asset','1000',True),
 ('1110','Cash in hand','Cash','Asset','1100',False),
 ('1120','Bank accounts','Bank','Asset','1100',False),
 ('1130','JazzCash wallet','Bank','Asset','1100',False),
 ('1140','Easypaisa wallet','Bank','Asset','1100',False),
 ('1200','Mobile phone inventory','Stock','Asset','1100',False),
 ('1210','Accessories inventory','Stock','Asset','1100',False),
 ('1300','Customer receivables','Receivable','Asset','1100',False),
 ('1310','Advances to suppliers','Current Asset','Asset','1100',False),
 ('1320','Prepaid expenses','Current Asset','Asset','1100',False),
 ('1500','Fixed assets','','Asset','1000',True),
 ('1510','Shop equipment','Fixed Asset','Asset','1500',False),
 ('1520','Furniture and fixtures','Fixed Asset','Asset','1500',False),
 ('1590','Accumulated depreciation','Accumulated Depreciation','Asset','1500',False),
 ('2000','Liabilities','','Liability',None,True),
 ('2100','Supplier payables','Payable','Liability','2000',False),
 ('2200','Customer advances','Current Liability','Liability','2000',False),
 ('2300','Accrued expenses','Current Liability','Liability','2000',False),
 ('2400','Tax payable','Current Liability','Liability','2000',False),
 ('2500','Business loans','Loan','Liability','2000',False),
 ('3000','Owner\'s equity','','Equity',None,True),
 ('3100','Owner\'s capital','Equity','Equity','3000',False),
 ('3200','Additional capital','Equity','Equity','3000',False),
 ('3300','Owner\'s drawings','Equity','Equity','3000',False),
 ('3900','Retained earnings','Equity','Equity','3000',False),
 ('4000','Revenue','','Income',None,True),
 ('4100','Mobile phone sales','Income Account','Income','4000',False),
 ('4200','Accessories sales','Income Account','Income','4000',False),
 ('4300','Repair and service income','Income Account','Income','4000',False),
 ('4400','Exchange / trade-in gains','Income Account','Income','4000',False),
 ('4900','Other income','Income Account','Income','4000',False),
 ('5000','Cost of sales','','Expense',None,True),
 ('5100','Cost of goods sold','Cost of Goods Sold','Expense','5000',False),
 ('5200','Stock losses and write-offs','Expense Account','Expense','5000',False),
 ('6000','Operating expenses','','Expense',None,True),
 ('6100','Shop rent','Expense Account','Expense','6000',False),
 ('6200','Employee salaries','Expense Account','Expense','6000',False),
 ('6300','Electricity','Expense Account','Expense','6000',False),
 ('6400','Internet and phone','Expense Account','Expense','6000',False),
 ('6500','Transportation','Expense Account','Expense','6000',False),
 ('6600','Marketing','Expense Account','Expense','6000',False),
 ('6700','Maintenance and repairs','Expense Account','Expense','6000',False),
 ('6800','Depreciation','Depreciation','Expense','6000',False),
 ('6850','Bank and wallet charges','Expense Account','Expense','6000',False),
 ('6900','Other operating expenses','Expense Account','Expense','6000',False),
 ('6950','Cash short and over','Expense Account','Expense','6000',False),
]
PREFIX='COA-'


@transaction.atomic
def install_standard(user):
    """Create any missing accounts of the standard chart (existing ones are left exactly as they are). Returns the count created."""
    by_code={a.code:a for a in Account.objects.filter(code__startswith=PREFIX)}
    created=0
    for code,name,kind,root,parent,group in STANDARD:
        full=PREFIX+code
        if full in by_code:continue
        account=Account(code=full,name=name,account_type=kind,root_type=root,is_group=group,parent=by_code.get(PREFIX+parent) if parent else None)
        account.full_clean();account.save();by_code[full]=account;created+=1
    services.audit(user,'Install standard chart of accounts',None,created=created)
    return created


def tree():
    """Accounts as an ordered list of dicts with depth and rolled-up balance (debit minus credit, shown in the account's natural side)."""
    accounts=list(Account.objects.select_related('parent').order_by('code'))
    totals={row['account_id']:(row['d'] or Decimal(0))-(row['c'] or Decimal(0)) for row in LedgerEntry.objects.values('account_id').annotate(d=Sum('debit'),c=Sum('credit'))}
    children=defaultdict(list)
    for a in accounts:children[a.parent_id].append(a)
    def own(a):return totals.get(a.pk,Decimal(0))
    def rolled(a):return own(a)+sum((rolled(c) for c in children.get(a.pk,[])),Decimal(0))
    rows=[]
    def walk(a,depth):
        raw=rolled(a);natural=-raw if a.root_type in ('Liability','Equity','Income') else raw
        rows.append(dict(account=a,depth=depth,balance=natural))
        for child in children.get(a.pk,[]):walk(child,depth+1)
    roots=[a for a in accounts if a.parent_id is None]
    for a in roots:walk(a,0)
    return rows


@login_required
def chart(request):
    if not permissions.can(request.user,'accounting'):raise PermissionDenied('You do not have access to this page.')
    if request.method=='POST' and request.POST.get('action')=='install':
        created=install_standard(request.user)
        messages.success(request,f'Standard chart installed: {created} account(s) added.' if created else 'The standard chart is already installed.')
        return redirect('chart')
    rows=tree()
    return render(request,'chart.html',dict(title='Chart of accounts',rows=rows,empty=not Account.objects.filter(code__startswith=PREFIX).exists()))
