from django.core.exceptions import PermissionDenied
from .models import Branch,Staff
from . import permissions

REPORTS=['Mobile Inventory','Mobile Stock Ageing','Device Profit','Mobile Device Economics','Staff Activity','Staff Summary',
         'Purchase Register','Sales Register','Payment Ledger','Accounts Receivable','Accounts Payable','General Ledger','Stock Balance','Stock Ledger','Profit and Loss Statement','Balance Sheet','Trial Balance','Unadjusted Trial Balance','Adjusted Trial Balance','Cash Flow Statement',
         'Daily Sales','Monthly Sales','Annual Sales','Expense Report','Low Stock','IMEI Report','Exchange Report','Stock Transfer Report','Branch Comparison']
NAV=[('Phones',[('Find by IMEI','IMEI Lookup'),('Phone Inventory','Mobile Device'),('Models','Mobile Model'),('Variants & Accessories','Item')]),
    ('Buy',[('Bulk purchase','Bulk Purchase'),('Buy a Phone','Mobile Intake'),('Purchase Receipts','Purchase Receipt'),('Supplier Bills / Expenses','Purchase Invoice'),('Purchase Orders','Purchase Order')]),
    ('Sell / POS',[('Sales & Returns','Sales Invoice')]),('Exchanges',[('New exchange','New Exchange'),('Phone Exchanges','Mobile Exchange')]),
    ('Stock',[('Stock transfers','Stock Entry'),('Stock adjustments','Stock Adjustment')]),
    ('Customers & Suppliers',[('Customers','Customer'),('Suppliers','Supplier'),('Overdue payments','Overdue')]),
    ('Payments & Expenses',[('Expenses & owner entries','Voucher'),('Payments / Advances','Payment Entry'),('Daily Closing','Mobile Daily Closing'),('Open / Past Shifts','POS Opening Entry'),('Till Closings','POS Closing Entry')]),
    ('Staff',[('Staff Names','Mobile Staff')]),('Warranty',[('Warranty / Returns','Warranty Claim')])]

def staff(user):
    if not user.is_authenticated:raise PermissionDenied
    if user.is_superuser:return None
    try:person=user.staff
    except Staff.DoesNotExist:raise PermissionDenied('Your login needs an active Staff assignment.')
    if not person.enabled or not person.branch.enabled:raise PermissionDenied('Your staff or shop account is inactive.')
    return person

def branches(user):
    person=staff(user)
    return Branch.objects.filter(enabled=True,**({'pk':person.branch_id} if person else {})).order_by('name','code')

def scoped_parties(user):
    """Customers and suppliers the user may see: the owner sees every one, a shop sees only its own shop's."""
    from .models import Party
    return Party.objects.all() if user.is_superuser else Party.objects.filter(branch__in=branches(user))

def visible_accounts(user):
    """Ledger accounts a user may pick: the owner sees all; a shop sees the shared chart and its own shop accounts, never another shop's."""
    from .models import Account
    from django.db.models import Q
    if user.is_superuser:return Account.objects.all()
    query=Q(code__startswith='COA-')
    for code in branches(user).values_list('code',flat=True):query|=Q(code__startswith=code+'-')
    return Account.objects.filter(query)

def parties_for(branch,kind=None):
    """One shop's customers or suppliers, plus not-yet-used legacy records any shop may adopt on first use."""
    from .models import Party
    from django.db.models import Q
    qs=Party.objects.filter(Q(branch=branch)|Q(branch__isnull=True)) if branch else Party.objects.none()
    return qs.filter(kind=kind) if kind else qs

def manager(user):
    person=staff(user)
    return person is None or person.manager

def costs(user):
    person=staff(user)
    return person is None or person.manager or person.can_buy or permissions.can(user,'view_costs')

STATEMENTS=('General Ledger','Profit and Loss Statement','Balance Sheet','Trial Balance','Unadjusted Trial Balance','Adjusted Trial Balance','Cash Flow Statement','Expense Report','Branch Comparison')

def statements(user):
    """Profit, ledger and balance-sheet figures: managers, the owner and roles granted view_statements (Accountant). Buyers keep cost reports only."""
    return manager(user) or permissions.can(user,'view_statements')

def require(user,branch,capability=None):
    person=staff(user)
    allowed=person is None or not capability or bool(getattr(person,capability,False))
    if person and capability=='can_sell':allowed=person.can_sell or person.can_handle_payments
    if person and (person.branch_id!=branch.pk or (capability and not person.manager and not allowed)):
        raise PermissionDenied('Your role does not allow this action in this shop.')

OWNER_ONLY_REPORTS=('General Ledger','Payment Ledger','Balance Sheet','Trial Balance','Unadjusted Trial Balance','Adjusted Trial Balance','Cash Flow Statement','Branch Comparison')


def menu_for(user,visible,path=''):
    """The sidebar, exactly as the business asked for it. Owner and Shop Accountant get different menus; a Shop Accountant never sees
    Shops, Stock Transfers or owner-only accounting. Each section is (title, icon, first_url, [(label,url)])."""
    from django.urls import reverse as r
    owner=user.is_superuser
    report=lambda name:r('report',args=[name])
    coll=lambda kind:r('collection',args=[kind])
    def has(name):return name in visible
    reports=[(label,report(name)) for label,name in (('Sales Reports','Daily Sales'),('Purchase Reports','Purchase Register'),('Inventory Reports','Mobile Inventory'),('Expense Reports','Expense Report'),
             ('Profit Reports' if owner else 'Profit Summary','Profit and Loss Statement')) if has(name)]
    if owner:reports+=[(label,report(name)) for label,name in (('Financial Summaries','Balance Sheet'),('Branch Performance','Branch Comparison')) if has(name)]
    money=[('Money In',r('money_in')),('Money Out',r('money_out')),('Expenses',r('money_expenses')),('Customer Dues',r('customer_dues')),('Supplier Dues',r('supplier_dues')),('Cash and Bank',r('cash_and_bank')),('Transaction History',r('money_history'))]
    sections=[('Dashboard','home',r('dashboard'),[])]
    if owner:
        sections.append(('Shops','shops',r('setup'),[('All Shops',r('setup')),('Shop Accounts',r('users')),('Branch Information',r('settings'))]))
    sections.append(('POS','sell-pos',r('pos'),[('New Sale',r('pos')),('Sales History',coll('Sales Invoice'))]))
    inventory=[('All Shops Inventory' if owner else 'My Shop Inventory',coll('Mobile Device'))]
    inventory.append(('Product and Model Management',coll('Item')) if owner else ('Add Stock',r('easy_entry',args=['purchase'])))
    inventory+=[('Stock in All Shops',r('stock_everywhere')),('IMEI Management',r('imei_lookup')),('Stock Adjustments',r('stock_adjust'))]
    sections.append(('Inventory','stock',coll('Mobile Device'),inventory))
    sections.append(('Purchases','buy',r('easy_entry',args=['purchase']),[('New Purchase',r('easy_entry',args=['purchase'])),('Bulk Purchase',r('bulk_purchase')),('Purchase History',coll('Purchase Invoice')),('Supplier Records' if owner else 'Suppliers',coll('Supplier'))]))
    if owner:sections.append(('Stock Transfers','exchanges',r('stock_transfer'),[('All Transfers',r('stock_transfer')),('Pending Transfers',r('stock_transfer')+'?state=Open'),('Transfer History',r('stock_transfer')+'?state=Received')]))
    sections.append(('Customers','customers-suppliers',coll('Customer'),[('Customer Directory',coll('Customer')),('Customer Outstanding Balances' if owner else 'Customer Dues',r('customer_dues')),('Customer Payment History',coll('Payment Entry'))]))
    sections.append(('Suppliers','buy',coll('Supplier'),[('Supplier Directory',coll('Supplier')),('Supplier Outstanding Balances' if owner else 'Supplier Dues',r('supplier_dues')),('Supplier Payment History',coll('Payment Entry'))]))
    sections.append(('Accounts & Finance','payments-expenses',r('money_in'),money))
    sections.append(('Reports','reports',r('report_center'),reports+[('All Reports',r('report_center'))]))
    if owner:
        settings=[('Business Settings',r('setup')),('Shop Settings',r('settings')),('Payment Methods',r('setup')),('Product Settings',coll('Item')),('System Preferences',r('password')),
                  ('Chart of Accounts',r('chart')),('Accounting Adjustments',r('journals')),('Lock a Month',r('periods')),('Year-end Close',r('year_end')),('Fixed Assets',r('assets')),('Prepaids & Accruals',r('deferrals')),('Loans',r('loans')),('Activity Log',r('audit_log'))]
    else:
        settings=[('My Shop Information',r('settings')),('Personal Account Settings',r('password')),('PC, Scanner & Printer Check',r('device_check'))]
    sections.append(('Settings','settings',settings[0][1],settings))
    out=[]
    from urllib.parse import parse_qsl
    here,_,query=(path or '').partition('?');asked=set(parse_qsl(query))
    def specificity(x):
        # Links that share a page and differ only by ?filter (All / Pending / History) must not all light up at once.
        base,_,q=x.partition('?')
        if not here or base!=here:return -1
        wanted=set(parse_qsl(q));return len(wanted) if wanted<=asked else -1
    for title,icon,url,items in sections:
        best=max([specificity(x) for _,x in items]+[-1])
        rows=[dict(label=l,url=x,current=best>=0 and specificity(x)==best) for l,x in items]
        out.append(dict(title=title,icon=icon,url=url,items=rows,open=any(row['current'] for row in rows) or (bool(path) and path.partition('?')[0]==url.split('?')[0])))
    return out


def navigation(request):
    if not request.user.is_authenticated:return {}
    try:
        person=staff(request.user);is_manager=manager(request.user);can_costs=costs(request.user);can_statements=statements(request.user)
        can_buy=is_manager or person.can_buy
        can_sell=is_manager or person.can_sell or person.can_handle_payments
        sections=[(section,links) for section,links in NAV if (section!='Buy' or can_buy) and (section not in ('Exchanges','Stock') or is_manager) and (section!='Sell / POS' or can_sell)]
        visible=[name for name in REPORTS if (request.user.is_superuser or name not in OWNER_ONLY_REPORTS) and (name not in ('Device Profit','Mobile Device Economics','Purchase Register','Accounts Payable') or can_costs) and (name not in STATEMENTS or can_statements) and (name not in ('Staff Activity','Staff Summary','Mobile Device Economics') or is_manager)]
        from .report_catalog import label
        from .report_catalog import label as _label
        quick=[dict(name=n,label=_label(n)) for n in ('Daily Sales','Profit and Loss Statement','Expense Report','Mobile Inventory','Accounts Receivable','Accounts Payable','Branch Comparison') if n in visible]
        company=branches(request.user).exclude(company='').values_list('company',flat=True).first() or ''
        return dict(menu=menu_for(request.user,visible,request.get_full_path()),owner_company=company,navigation=sections,reports=visible,report_navigation=[dict(name=name,label=label(name)) for name in visible],quick_reports=quick,staff_report_navigation=[dict(name=name,label=label(name)) for name in visible if name.startswith('Staff ')],can_costs=can_costs,can_buy=can_buy,can_statements=can_statements,can_audit=permissions.can(request.user,'view_audit'),can_accounting=permissions.can(request.user,'accounting'),can_roles=request.user.is_superuser or permissions.can(request.user,'manage_roles'),can_sell=can_sell,is_manager=is_manager,can_fund_transfers=is_manager or person.can_handle_payments,shops=branches(request.user))
    except PermissionDenied:return {}
