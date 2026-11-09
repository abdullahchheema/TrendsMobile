"""Permission catalogue and role helpers. Roles hold permission codes; Staff flags (manager, can_buy, can_sell,
can_handle_payments) are derived from them on save so legacy checks and new checks always agree."""
CATALOG=[
    # (code, label, group)
    ('sell','Sell phones and accessories (New Sale, POS)','Sales'),
    ('payments','Take payments, run the till, collect from customers','Sales'),
    ('discount_override','Give discounts or prices above a role limit (manager PIN override)','Sales'),
    ('refund','Issue refunds and return credits','Sales'),
    ('buy','Buy stock and record supplier bills','Stock'),
    ('manage_branch','Manage the shop: cancel posted transactions, transfers, staff, settings','Stock'),
    ('adjust_stock','Write off or adjust stock','Stock'),
    ('view_costs','See purchase costs and cost reports','Accounts'),
    ('view_statements','See profit and loss, balance sheet and ledgers','Accounts'),
    ('accounting','Post journals, expenses and period controls','Accounts'),
    ('view_audit','View the audit log','Control'),
    ('manage_roles','Create and edit roles and permissions','Control'),
]
CODES={c for c,_,_ in CATALOG}
FLAG_PERMS={'manager':'manage_branch','can_buy':'buy','can_sell':'sell','can_handle_payments':'payments'}

DEFAULT_ROLES=[
    # code, name, description, permissions, discount %, refund max
    ('owner','Owner','Everything in every shop.',sorted(CODES),100,0),
    ('manager','Branch Manager','Runs one shop: sales, purchases, stock, staff and branch reports.',['sell','payments','discount_override','refund','buy','manage_branch','adjust_stock','view_costs','view_statements','view_audit'],100,0),
    ('sales','Sales Employee','POS, customers and sales history.',['sell'],5,0),
    ('accountant','Accountant','Ledgers, expenses, payments and financial reports; no selling or buying.',['payments','view_costs','view_statements','accounting','view_audit'],0,0),
    ('sales_payments','Sales & collections','Sells and collects payments.',['sell','payments'],5,0),
    ('buyer','Purchases','Buys stock and records supplier bills.',['buy','view_costs'],0,0),
    ('buyer_payments','Purchases & supplier payments','Buys stock and pays suppliers.',['buy','payments','view_costs'],0,0),
    ('cashier','Cashier','Sells at the POS, takes payments and runs the till.',['sell','payments'],0,0),
    ('technician','Technician','Repairs and inspections only.',[],0,0),
]


def flags_to_permissions(manager,can_buy,can_sell,can_handle_payments):
    """What a Staff row with no role is allowed to do, expressed as permissions."""
    perms=set()
    if can_sell:perms.add('sell')
    if can_handle_payments:perms.add('payments')
    if can_buy:perms|={'buy','view_costs'}
    if manager:perms|={'sell','payments','buy','manage_branch','adjust_stock','view_costs','view_statements','view_audit','refund','discount_override'}
    return perms


def staff_permissions(person):
    if person.role_id:return set(person.role.permissions)
    return flags_to_permissions(person.manager,person.can_buy,person.can_sell,person.can_handle_payments)


def can(user,code):
    """True for the owner (superuser) or when the user's role (or legacy flags) grants the permission."""
    from .models import Staff
    if not user.is_authenticated:return False
    if user.is_superuser:return True
    try:person=user.staff
    except Staff.DoesNotExist:return False
    if not person.enabled or not person.branch.enabled:return False
    return code in staff_permissions(person)
