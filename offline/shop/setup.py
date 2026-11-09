import uuid
"""Owner setup operations. Creating a shop never posts opening money or stock."""
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.conf import settings
from .models import Branch,Warehouse,Till,Account,ShopSettings,Staff,Document,LedgerEntry,Audit,SupabaseIdentity,SalesTaxRule

POSTING={
 'receivable':('Customer receivables','Receivable'), 'payable':('Supplier payables','Payable'),
 'inventory':('Stock valuation','Stock'), 'stock_received':('Stock received, not billed','Stock Received But Not Billed'),
 'sales':('Sales income','Income Account'), 'cogs':('Cost of goods sold','Cost of Goods Sold'), 'expense':('Shop / repair expenses','Expense Account'),
}
METHODS=['Cash','Bank Transfer','Card','JazzCash','Easypaisa']
SECTION={'Receivable':'Asset','Payable':'Liability','Stock':'Asset','Stock Received But Not Billed':'Liability','Income Account':'Income','Cost of Goods Sold':'Expense','Expense Account':'Expense'}

def owner(user):
    if not user.is_superuser:raise PermissionDenied('Only the owner can configure shops and posting accounts.')

def validate_mapping(accounts,methods):
    if SalesTaxRule.objects.filter(account_id__in=list(accounts.values())+list(methods.values())).exists():raise ValidationError('An output-tax account cannot be used as a shop posting or payment account.')
    if not set(POSTING).issubset(accounts) or set(accounts)-set(POSTING)-{'inter_shop'}:raise ValidationError('Configure all seven posting accounts; inter-shop clearing is optional.')
    found={str(a.pk):a for a in Account.objects.filter(pk__in=list(accounts.values())+list(methods.values()),enabled=True,is_group=False)}
    if not all(str(value) in found for value in list(accounts.values())+list(methods.values())):raise ValidationError('Select active posting accounts, not account groups.')
    for key,account_id in accounts.items():
        kind=found[str(account_id)].account_type
        if key in ('receivable','payable') and kind!=POSTING[key][1]:raise ValidationError('Customer/supplier control accounts must have the corresponding Receivable/Payable type.')
        if key not in ('receivable','payable') and kind in ('Receivable','Payable','Cash','Bank'):raise ValidationError('Stock, sales and expense postings cannot use cash, bank or party control accounts.')
    if accounts.get('inter_shop'):
        if accounts['inter_shop'] in [v for k,v in accounts.items() if k!='inter_shop'] or accounts['inter_shop'] in methods.values():raise ValidationError('Inter-shop clearing needs a separate account.')
        if found[accounts['inter_shop']].account_type!='Current Asset':raise ValidationError('Inter-shop clearing must use a Current Asset account.')
    if 'Cash' not in methods:raise ValidationError('Configure the Cash payment method.')
    for name,account_id in methods.items():
        if found[str(account_id)].account_type!=('Cash' if name=='Cash' else 'Bank'):raise ValidationError('Cash must use a Cash account; digital/card/bank methods must use Bank accounts.')

@transaction.atomic
def configure(branch,accounts,methods,user):
    owner(user);Branch.objects.select_for_update().get(pk=branch.pk)
    settings,_=ShopSettings.objects.select_for_update().get_or_create(branch=branch)
    list(Account.objects.select_for_update().filter(pk__in=list(accounts.values())+list(methods.values())).order_by('pk'))
    validate_mapping(accounts,methods)
    changed=any(settings.accounts.get(key)!=accounts.get(key) for key in POSTING)
    previous_clearing=settings.accounts.get('inter_shop')
    if previous_clearing and previous_clearing!=accounts.get('inter_shop') and LedgerEntry.objects.filter(branch=branch,account_id=previous_clearing).exists():raise ValidationError('The inter-shop clearing account is locked after its first posting.')
    if changed and LedgerEntry.objects.filter(branch=branch).exists():raise ValidationError('Posting accounts are locked after trading begins. Changing them requires an accounting migration.')
    if (changed or settings.payment_methods!=methods) and Document.objects.filter(branch=branch,kind='POS Opening Entry',state='Open',status='Submitted').exists():raise ValidationError('Close this shop’s POS shifts before changing payment or posting accounts.')
    settings.accounts.pop('inter_shop',None)
    settings.accounts={**settings.accounts,**accounts};settings.payment_methods=methods;settings.save()
    Audit.objects.create(user=user,action='Configure shop accounts',branch=branch,details={'branch':str(branch.pk),'accounts':accounts,'payment_methods':methods})
    return settings

def transfer_accounts(branch):
    config=ShopSettings.objects.filter(branch=branch).first()
    if not config:raise ValidationError('This shop has no accounts yet. Ask the super admin to finish setting it up.')
    if not config.accounts.get('inter_shop'):       # created on first use so shops can send stock to each other without any setup
        code=branch.code+'-CLEARING'
        if Account.objects.filter(code=code).exists():code+='-'+uuid.uuid4().hex[:4].upper()
        account=Account.objects.create(code=code,name=(branch.name+' · Inter-shop clearing')[:180],account_type='Current Asset',root_type='Asset')
        config.accounts={**config.accounts,'inter_shop':str(account.pk)};config.save()
    ids=[config.accounts.get('inventory'),config.accounts.get('inter_shop')]
    accounts={str(a.pk):a for a in Account.objects.filter(pk__in=[value for value in ids if value],enabled=True,is_group=False)}
    if len(set(ids))!=2 or any(value not in accounts for value in ids):raise ValidationError('Select separate active inventory and inter-shop clearing accounts.')
    inventory,clearing=[accounts[value] for value in ids]
    if inventory.account_type!='Stock' or clearing.account_type!='Current Asset':raise ValidationError('Use a Stock inventory account and a Current Asset inter-shop clearing account.')
    if any(value==ids[1] for key,value in config.accounts.items() if key!='inter_shop') or ids[1] in config.payment_methods.values():raise ValidationError('Inter-shop clearing needs a separate account.')
    return inventory,clearing

@transaction.atomic
def create_shop(values,user):
    owner(user)
    if not set(values['payment_methods']).issubset(METHODS) or 'Cash' not in values['payment_methods']:raise ValidationError('Select supported payment methods, including Cash.')
    branch=Branch(code=values['code'],name=values['name'],company=values['company']);branch.full_clean();branch.save()
    Warehouse.objects.create(code=branch.code+'-STOCK',name=values.get('warehouse_name') or 'Shop stock',branch=branch)
    Till.objects.create(code=branch.code+'-TILL',name=values.get('till_name') or 'Shop till',branch=branch)
    accounts={}
    for key,(label,kind) in POSTING.items():
        account=Account.objects.create(code=branch.code+'-'+key.upper(),name=(branch.name+' · '+label)[:180],account_type=kind,root_type=SECTION.get(kind,''));accounts[key]=str(account.pk)
    methods={}
    for index,name in enumerate(values['payment_methods'],1):
        account=Account.objects.create(code=f'{branch.code}-PAY-{index}',name=(branch.name+' · '+name)[:180],account_type='Cash' if name=='Cash' else 'Bank',root_type='Asset');methods[name]=str(account.pk)
    validate_mapping(accounts,methods)
    ShopSettings.objects.create(branch=branch,shop_phone=values.get('shop_phone',''),shop_address=values.get('shop_address',''),accounts=accounts,payment_methods=methods)
    Audit.objects.create(user=user,action='Create shop setup',branch=branch,details={'branch':str(branch.pk),'code':branch.code,'no_opening_entries':True})
    return branch

def readiness(branch):
    config=ShopSettings.objects.filter(branch=branch).first();issues=[]
    from . import shopdefaults
    shopdefaults.ensure(branch)           # every shop always has its one stock record and till; nothing to set up
    if not config:issues.append('Configure posting accounts and payment methods')
    else:
        try:validate_mapping(config.accounts,config.payment_methods)
        except ValidationError:issues.append('Review posting accounts and payment methods')
    people=Staff.objects.filter(branch=branch,enabled=True)
    logins=people.filter(user__is_active=True)
    if settings.SUPABASE_AUTH_ENABLED:logins=logins.filter(user_id__in=SupabaseIdentity.objects.values('user_id'))
    if not logins.exists():issues.append('Create the shop login (Shop accounts)')
    return {'branch':branch,'issues':issues,'ready':not issues,'warehouses':Warehouse.objects.filter(branch=branch,enabled=True).count(),'tills':Till.objects.filter(branch=branch,enabled=True).count(),'staff_count':people.count(),'linked_logins':logins.count(),'methods':list(config.payment_methods) if config else []}
