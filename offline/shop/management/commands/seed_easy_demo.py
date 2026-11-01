"""Labelled training transactions, posted through the same retail services."""
from datetime import date
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from shop.models import *
from shop import setup,easy_retail,services as s,customer_names,financial_reports

BATCH='DEMO-EASY-RETAIL-V1'


def seed(user,day):
    if Audit.objects.filter(action='Seed easy retail demo',details__batch=BATCH).exists():return {'already_seeded':True,'batch':BATCH}
    if Branch.objects.filter(code='COV-DEMO').exists():raise CommandError('COV-DEMO already exists without this seed marker; existing records were preserved.')
    branch=setup.create_shop(dict(code='COV-DEMO',name='DEMO Mobile Shop',company='Covison Training Company',warehouse_name='Demo shop stock',till_name='Demo till',payment_methods=['Cash','Bank Transfer','JazzCash','Easypaisa']),user)
    branch.demo_batch=BATCH;branch.save();warehouse=Warehouse.objects.get(branch=branch);warehouse.demo_batch=BATCH;warehouse.save()
    config=ShopSettings.objects.get(branch=branch)
    Account.objects.filter(pk__in=list(config.accounts.values())+list(config.payment_methods.values())).update(demo_batch=BATCH)
    Till.objects.filter(branch=branch).update(demo_batch=BATCH)
    seller=Staff.objects.create(code='DEMO-EASY-SALES',name='DEMO Ali Hassan',branch=branch,default_warehouse=warehouse,can_sell=True,can_handle_payments=True,demo_batch=BATCH)
    buyer=Staff.objects.create(code='DEMO-EASY-BUYER',name='DEMO Usman Khan',branch=branch,default_warehouse=warehouse,can_buy=True,demo_batch=BATCH)
    specs=[('11','64GB','Black','Non-PTA',40000,50000),('11','128GB','White','PTA',55000,65000),('12','128GB','Blue','Non-PTA',60000,75000),('13','128GB','Midnight','PTA',90000,105000),('14','128GB','Purple','PTA',115000,135000),('15','256GB','Black','Non-PTA',140000,160000)]
    bills=[];phones=[]
    for index,(model_name,storage,colour,pta,cost,asking) in enumerate(specs):
        model,_=PhoneModel.objects.get_or_create(code='DEMO-EASY-IP'+model_name,defaults=dict(name='iPhone '+model_name+' (Demo)',brand='Apple',demo_batch=BATCH))
        product=Product.objects.create(code='DEMO-EASY-PHONE-'+str(index+1),name=f'DEMO iPhone {model_name} {storage} {colour}',brand='Apple',model=model,storage=storage,colour=colour,serialized=True,stock_item=True,selling_price=asking,demo_batch=BATCH)
        bill=easy_retail.purchase(dict(branch=branch,party_name='DEMO Bilal Mobiles' if index<3 else 'DEMO Ahmed Wholesale',product=product,quantity=1,price=cost,asking_price=asking,pta_status=pta,device_type='Used',condition='Good',warehouse=warehouse,staff=buyer,handler=seller,posting_date=day,payment_status='unpaid',method='Cash',payment_reference='',notes='Training purchase; no actual money or customer.'),user,BATCH)
        bills.append(bill);phones.append(bill.reference.lines.get().device)
    modes=[('full',0,'Cash'),('part',30000,'Cash'),('unpaid',0,'Cash'),('full',0,'Bank Transfer')]
    names=['DEMO Hassan Ali','DEMO Ayesha Khan','DEMO Bilal Ahmed','DEMO Maryam Shah']
    for index,(mode,paid,method) in enumerate(modes):
        easy_retail.sale(dict(branch=branch,party_name=names[index],phone=phones[index],price=specs[index][5],pta_status=specs[index][3],staff=seller,handler=seller,posting_date=day,payment_status=mode,amount_paid=paid,method=method,payment_reference='DEMO-BANK-SALE-04' if method=='Bank Transfer' else '',notes='Training sale; no actual money or customer.'),user,BATCH)
    for bill,amount,method in [(bills[0],40000,'Cash'),(bills[3],80000,'Bank Transfer')]:
        payment=s.new_doc('Payment Entry',branch,supplier=bill.supplier,reference=bill,handler=seller,posting_date=day,demo_batch=BATCH,data=dict(direction='Pay',amount=str(amount),mode=method,reference='DEMO-BANK-SUPPLIER' if method=='Bank Transfer' else ''))
        s.submit(payment,user)
    supplier=customer_names.resolve('DEMO Shop Services',user,kind='Supplier',demo_batch=BATCH)
    expense=Product.objects.create(code='DEMO-EASY-EXPENSE',name='DEMO shop operating expense',stock_item=False,serialized=False,demo_batch=BATCH)
    bill=s.new_doc('Purchase Invoice',branch,supplier=supplier,buyer=buyer,posting_date=day,demo_batch=BATCH,notes='Sample shop operating expense.')
    Line.objects.create(document=bill,product=expense,quantity=1,rate=10000);bill=s.submit(bill,user)
    s.submit(s.new_doc('Payment Entry',branch,supplier=supplier,reference=bill,handler=seller,posting_date=day,demo_batch=BATCH,data=dict(direction='Pay',amount='10000',mode='Cash')),user)
    totals=verify(branch,day)
    Audit.objects.create(user=user,action='Seed easy retail demo',details={'batch':BATCH,'branch':str(branch.pk),'totals':{key:str(value) for key,value in totals.items()}})
    return {'batch':BATCH,'shop':branch.name,'branch':str(branch.pk),'date':str(day),'totals':{key:str(value) for key,value in totals.items()}}


def verify(branch,day):
    ledger=LedgerEntry.objects.filter(branch=branch,document__demo_batch=BATCH)
    gl=ledger.aggregate(debit=Sum('debit'),credit=Sum('credit'))
    if gl['debit']!=gl['credit']:raise CommandError('Demo ledger does not balance; seed rolled back.')
    _,profit=financial_reports.run('Profit and Loss Statement',Branch.objects.filter(pk=branch.pk),day,day)
    result=dict(sales=Document.objects.filter(branch=branch,kind='Sales Invoice',status='Submitted',demo_batch=BATCH).aggregate(total=Sum('total'))['total'],
        net_profit=dict(financial_reports.summary('Profit and Loss Statement',profit))['Net profit / loss'],
        customer_credit=Document.objects.filter(branch=branch,kind='Sales Invoice',demo_batch=BATCH).aggregate(total=Sum('outstanding'))['total'],
        supplier_credit=Document.objects.filter(branch=branch,kind='Purchase Invoice',demo_batch=BATCH).aggregate(total=Sum('outstanding'))['total'],
        stock_value=StockMovement.objects.filter(warehouse__branch=branch,document__demo_batch=BATCH).aggregate(total=Sum('value'))['total'],
        gl_debit=gl['debit'],gl_credit=gl['credit'])
    expected=dict(sales=295000,net_profit=40000,customer_credit=110000,supplier_credit=380000,stock_value=255000,gl_debit=1865000,gl_credit=1865000)
    if result!=expected:raise CommandError('Demo control totals failed; seed rolled back: '+str(result))
    _,balance=financial_reports.run('Balance Sheet',Branch.objects.filter(pk=branch.pk),day,day)
    if dict(financial_reports.summary('Balance Sheet',balance))['Assets less liabilities and equity']!=0:raise CommandError('Demo balance sheet does not reconcile.')
    return result


class Command(BaseCommand):
    help='Post clearly labelled demo sales/purchases in a separate training shop; repeat runs do not duplicate records.'
    def add_arguments(self,parser):
        parser.add_argument('--actor',required=True);parser.add_argument('--date',type=date.fromisoformat,default=None);parser.add_argument('--dry-run',action='store_true')
    def handle(self,*args,**options):
        user=get_user_model().objects.get(username=options['actor'],is_superuser=True,is_active=True)
        with transaction.atomic():
            # Prevent two seed requests from creating the same training dataset.
            from django.db import connection
            with connection.cursor() as cursor:cursor.execute('SELECT pg_advisory_xact_lock(%s)',[1876042261])
            result=seed(user,options['date'] or timezone.localdate())
            if options['dry_run']:transaction.set_rollback(True)
        import json
        self.stdout.write(json.dumps({'dry_run':options['dry_run'],**result},indent=2))
