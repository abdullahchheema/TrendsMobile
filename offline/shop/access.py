from django.core.exceptions import PermissionDenied
from .models import Branch,Staff

REPORTS=['Mobile Inventory','Mobile Stock Ageing','Device Profit','Mobile Device Economics','Staff Activity','Staff Summary',
         'Purchase Register','Sales Register','Payment Ledger','Accounts Receivable','Accounts Payable','General Ledger','Stock Balance','Stock Ledger','Profit and Loss Statement','Balance Sheet','Trial Balance']
NAV=[('Phones',[('Phone Inventory','Mobile Device'),('Models','Mobile Model'),('Variants & Accessories','Item')]),
    ('Buy',[('Buy a Phone','Mobile Intake'),('Purchase Receipts','Purchase Receipt'),('Supplier Bills / Expenses','Purchase Invoice'),('Purchase Orders','Purchase Order')]),
    ('Sell / POS',[('Sales & Returns','Sales Invoice')]),('Exchanges',[('Phone Exchanges','Mobile Exchange')]),
    ('Stock',[('Stock transfers','Stock Entry'),('Stock adjustments','Stock Adjustment')]),
    ('Customers & Suppliers',[('Customers','Customer'),('Suppliers','Supplier')]),
    ('Payments & Expenses',[('Payments / Advances','Payment Entry'),('Daily Closing','Mobile Daily Closing'),('Open / Past Shifts','POS Opening Entry'),('Till Closings','POS Closing Entry')]),
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

def manager(user):
    person=staff(user)
    return person is None or person.manager

def costs(user):
    person=staff(user)
    return person is None or person.manager or person.can_buy

STATEMENTS=('General Ledger','Profit and Loss Statement','Balance Sheet','Trial Balance')

def statements(user):
    """Profit, ledger and balance-sheet figures are for managers/owner only; buyers keep cost reports."""
    return manager(user)

def require(user,branch,capability=None):
    person=staff(user)
    allowed=person is None or not capability or bool(getattr(person,capability,False))
    if person and capability=='can_sell':allowed=person.can_sell or person.can_handle_payments
    if person and (person.branch_id!=branch.pk or (capability and not person.manager and not allowed)):
        raise PermissionDenied('Your role does not allow this action in this shop.')

def navigation(request):
    if not request.user.is_authenticated:return {}
    try:
        person=staff(request.user);is_manager=manager(request.user);can_costs=costs(request.user)
        can_sell=is_manager or person.can_sell or person.can_handle_payments
        sections=[(section,links) for section,links in NAV if (section!='Buy' or can_costs) and (section not in ('Exchanges','Stock') or is_manager) and (section!='Sell / POS' or can_sell)]
        visible=[name for name in REPORTS if (name not in ('Device Profit','Mobile Device Economics','Purchase Register','Accounts Payable') or can_costs) and (name not in STATEMENTS or is_manager) and (name not in ('Staff Activity','Staff Summary','Mobile Device Economics') or is_manager)]
        from .report_catalog import label
        company=branches(request.user).exclude(company='').values_list('company',flat=True).first() or ''
        return dict(owner_company=company,navigation=sections,reports=visible,report_navigation=[dict(name=name,label=label(name)) for name in visible],staff_report_navigation=[dict(name=name,label=label(name)) for name in visible if name.startswith('Staff ')],can_costs=can_costs,can_sell=can_sell,is_manager=is_manager,can_fund_transfers=is_manager or person.can_handle_payments,shops=branches(request.user))
    except PermissionDenied:return {}
