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
    return Branch.objects.filter(enabled=True,**({'pk':person.branch_id} if person else {}))

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

def navigation(request):
    if not request.user.is_authenticated:return {}
    try:
        person=staff(request.user);is_manager=manager(request.user);can_costs=costs(request.user);can_statements=statements(request.user)
        can_buy=is_manager or person.can_buy
        can_sell=is_manager or person.can_sell or person.can_handle_payments
        sections=[(section,links) for section,links in NAV if (section!='Buy' or can_buy) and (section not in ('Exchanges','Stock') or is_manager) and (section!='Sell / POS' or can_sell)]
        visible=[name for name in REPORTS if (name not in ('Device Profit','Mobile Device Economics','Purchase Register','Accounts Payable') or can_costs) and (name not in STATEMENTS or can_statements) and (name not in ('Staff Activity','Staff Summary','Mobile Device Economics') or is_manager)]
        from .report_catalog import label
        from .report_catalog import label as _label
        quick=[dict(name=n,label=_label(n)) for n in ('Daily Sales','Profit and Loss Statement','Expense Report','Mobile Inventory','Accounts Receivable','Accounts Payable','Branch Comparison') if n in visible]
        company=branches(request.user).exclude(company='').values_list('company',flat=True).first() or ''
        return dict(owner_company=company,navigation=sections,reports=visible,report_navigation=[dict(name=name,label=label(name)) for name in visible],quick_reports=quick,staff_report_navigation=[dict(name=name,label=label(name)) for name in visible if name.startswith('Staff ')],can_costs=can_costs,can_buy=can_buy,can_statements=can_statements,can_audit=permissions.can(request.user,'view_audit'),can_accounting=permissions.can(request.user,'accounting'),can_roles=request.user.is_superuser or permissions.can(request.user,'manage_roles'),can_sell=can_sell,is_manager=is_manager,can_fund_transfers=is_manager or person.can_handle_payments,shops=branches(request.user))
    except PermissionDenied:return {}
