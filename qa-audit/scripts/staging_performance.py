"""Controlled performance test on an ISOLATED staging database. Never point this at production.

It refuses to run unless the database host is 127.0.0.1 / localhost. It (1) builds a staging business (owner, two shops, logins,
the Online Store) if the database is empty, (2) grows it with the application's own 90-day business generator, (3) measures every
important page as the owner and as a shop login (server time, number of SQL statements, payload size), and (4) runs mixed
concurrent work (online orders, counter sales, IMEI searches, payments) at rising concurrency, then checks the books.

    set PYTHONPATH=<folder holding pg_settings.py>;<repo>/offline
    python staging_performance.py --grow 2 --rounds 7 --out ../test-results/staging-performance.json

Times are the server's own work (application + database on one machine, no network). They show which pages are expensive and how
the work grows with data; they are not what a customer in Pakistan sees from Vercel (that is in live-erp-probe.json)."""
import argparse
import json
import os
import statistics
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

os.environ.setdefault('DJANGO_SETTINGS_MODULE','pg_settings')
import django
django.setup()
from decimal import Decimal
from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import close_old_connections,connection,connections,reset_queries
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from shop.models import *
from shop import books_check,coa,easy_retail,online_check,online_orders as oo,online_store,pricing,setup,shop_accounts,shopdefaults,stress_data,services as s
from shop.tests_audit_base import imei
from shop import tests_audit_invariants as invariants

PASSWORD='Staging-only-password-9931!'
KEY='staging-key-not-a-secret'


def guard():
    host=settings.DATABASES['default']['HOST']
    if host not in ('127.0.0.1','localhost','::1'):sys.exit(f'Refusing to run: the database host is {host!r}, not a local staging database.')
    settings.ALLOWED_HOSTS=['testserver','localhost','127.0.0.1'];settings.ONLINE_API_KEY=KEY;settings.DEBUG=False


def build():
    User=get_user_model();owner=User.objects.filter(is_superuser=True).first()
    if owner:return owner
    owner=User.objects.create_superuser('staging-owner',password=PASSWORD);coa.install_standard(owner)
    for code,name in (('STGA','Staging Shop A'),('STGB','Staging Shop B')):
        shop=setup.create_shop(dict(code=code,name=name,company='Staging Co',payment_methods=['Cash','Bank Transfer','Easypaisa','JazzCash']),owner)
        shop_accounts.create_login(shop,'staging-'+code.lower(),PASSWORD)
    shop_accounts.create_online_login('Staging Online','staging-online',PASSWORD);oo.online_branch()
    return owner


def grow(owner,times):
    shops=list(Branch.objects.filter(enabled=True,is_online=False).order_by('code')[:2]);done=[]
    for i in range(times):
        started=time.perf_counter()
        seed=20261110+Document.objects.count()
        result=stress_data.Story(owner,shops,tag=f'STAGING-{seed}',seed=seed,days=90,log=lambda *a:None).run()
        done.append(dict(seconds=round(time.perf_counter()-started,1),events=sum(v for k,v in result['counts'].items() if not k.startswith(('rejected','CRASH'))),crashes=len([f for f in result['failures'] if f[1].startswith('CRASH')])))
        print('grown:',done[-1],flush=True)
    return done


def publish(count=60):
    """Put phones on the website so that online orders have something to buy (specs + a photo record make a page live)."""
    specs={'Display':{'Screen size':'6.6 inch'},'Performance':{'Chipset':'Staging','RAM':'8 GB','Operating system':'Android'},'Camera':{'Main camera':'50 MP'},'Battery':{'Battery':'5000 mAh'}}
    live=0
    for product in Product.objects.filter(serialized=True,enabled=True,device__status='Available').distinct()[:count]:
        if not product.slug:product.slug=f'stg-{product.pk.hex[:10]}'
        product.specs=product.specs or specs;product.save()
        if not product.images.exists():ProductImage.objects.create(product=product,path='staging/x.jpg',url='https://img.invalid/x.jpg')
        online_store.refresh_live(product);live+=1
    OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('0'),free_delivery_from=None,cod_enabled=True))
    return live


def sizes():
    return dict(documents=Document.objects.count(),ledger_rows=LedgerEntry.objects.count(),stock_movements=StockMovement.objects.count(),phones=Device.objects.count(),imeis=IMEI.objects.count(),
                parties=Party.objects.count(),products=Product.objects.count(),online_orders=OnlineOrder.objects.count(),audit_rows=Audit.objects.count())


def pct(values,f):
    ordered=sorted(values);return ordered[min(len(ordered)-1,round(f*(len(ordered)-1)))]


def pages(owner,rounds):
    shop=Branch.objects.filter(is_online=False).order_by('code').first();login=Staff.objects.filter(branch=shop,user__isnull=False).first().user
    online=OnlineStaff.objects.first().user;today=timezone.localdate();start=today.replace(year=today.year-1)
    sale=Document.objects.filter(branch=shop,kind__in=s.SELL,status='Submitted',is_return=False).order_by('-created').first()
    party=Party.objects.filter(branch=shop,kind='Customer').first();device=Device.objects.filter(branch=shop).first();number=IMEI.objects.filter(device__branch=shop).values_list('number',flat=True).first() or ''
    q=f'?branch={shop.pk}&from_date={start}&to_date={today}'
    plan=[('owner','Dashboard (all shops)',reverse('dashboard')),('shop','Dashboard',reverse('dashboard')),('shop','New Sale (POS)',reverse('pos')),('owner','New Sale (all shops)',reverse('pos')),
          ('shop','Phone inventory',reverse('collection',args=['Mobile Device'])),('shop','Sales history',reverse('collection',args=['Sales Invoice'])),('shop','Sales history page 3',reverse('collection',args=['Sales Invoice'])+'?page=3'),
          ('shop','Sales history search',reverse('collection',args=['Sales Invoice'])+'?search=test'),('shop','Purchase history',reverse('collection',args=['Purchase Invoice'])),('shop','Payments list',reverse('collection',args=['Payment Entry'])),
          ('shop','Customers',reverse('collection',args=['Customer'])),('shop','IMEI search (exact)',reverse('imei_lookup')+'?q='+number),('shop','IMEI search (partial)',reverse('imei_lookup')+'?q='+number[:8]),('shop','Global search',reverse('search')+'?q=iphone'),
          ('shop','Sale detail',reverse('detail',args=[sale.pk])),('shop','Receipt',reverse('receipt',args=[sale.pk])),('shop','Phone history',reverse('device_history',args=[device.pk])),('shop','Customer account',reverse('party_account',args=[party.pk])),
          ('shop','Transaction history',reverse('money_history')),('shop','Cash and bank',reverse('cash_and_bank')),('shop','Customer dues',reverse('customer_dues')),('shop','Supplier dues',reverse('supplier_dues')),('shop','Accessories',reverse('accessories')),
          ('shop','Returns: find a sale',reverse('returns')+'?q=test'),('shop','Stock in all shops',reverse('stock_everywhere')),('shop','Overdue payments',reverse('overdue')),
          ('owner','Profit and loss (1 year)',reverse('report',args=['Profit and Loss Statement'])+q),('owner','Trial balance',reverse('report',args=['Trial Balance'])+q),('owner','Balance sheet',reverse('report',args=['Balance Sheet'])+q),
          ('owner','General ledger (1 year)',reverse('report',args=['General Ledger'])+q),('owner','Sales register (1 year)',reverse('report',args=['Sales Register'])+q),('owner','Stock ledger',reverse('report',args=['Stock Ledger'])+q),
          ('owner','Mobile inventory report',reverse('report',args=['Mobile Inventory'])+q),('owner','Device profit',reverse('report',args=['Device Profit'])+q),('owner','Branch comparison',reverse('report',args=['Branch Comparison'])+f'?from_date={start}&to_date={today}'),
          ('owner','Staff and commission',reverse('staff_list')),('owner','Audit log',reverse('audit_log')),('owner','Stock transfers',reverse('stock_transfer')),('owner','Chart of accounts',reverse('chart')),
          ('online','Online dashboard',reverse('online_dashboard')),('online','Online orders',reverse('online_orders')),('online','Online stock',reverse('online_stock')),('online','Products and photos',reverse('online_store')),('online','New online order',reverse('online_order_new')),
          ('shop','Live badge poll',reverse('live_state')),('shop','Orders to pack',reverse('shop_online_orders'))]
    clients={}
    for role,user in (('owner',owner),('shop',login),('online',online)):
        clients[role]=Client();clients[role].force_login(user)
    out=[]
    for role,name,url in plan:
        times=[];queries=0;status=0;size=0
        for i in range(rounds):
            reset_queries()                                   # Django keeps at most 9,000 logged statements: start each page from zero
            with CaptureQueriesContext(connection) as captured:
                started=time.perf_counter();response=clients[role].get(url);elapsed=(time.perf_counter()-started)*1000
            status=response.status_code;size=len(response.content);queries=len(captured);db_ms=sum(float(x['time']) for x in captured)*1000
            if i:times.append(elapsed)
        row=dict(role=role,page=name,status=status,queries=queries,db_ms=round(db_ms,1),median_ms=round(statistics.median(times),1),p95_ms=round(pct(times,0.95),1),max_ms=round(max(times),1),kb=round(size/1024,1))
        out.append(row);print(f"{role:<6} {name:<28} {status}  {queries:>4} queries  median {row['median_ms']:>8} ms  p95 {row['p95_ms']:>8} ms  {row['kb']:>7} KB",flush=True)
    return out


SPECS={'Display':{'Screen size':'6.6 inch'},'Performance':{'Chipset':'Staging','RAM':'8 GB','Operating system':'Android'},'Camera':{'Main camera':'50 MP'},'Battery':{'Battery':'5000 mAh'}}


def stock_up(owner,shops,count):
    """Buy `count` identical phones (on credit, half in each shop) of one published model, so every load level has real stock to fight over."""
    model,_=PhoneModel.objects.get_or_create(code='LOAD-M',defaults=dict(name='Load Test Phone',brand='Staging'))
    product,_=Product.objects.get_or_create(code='LOAD-P',defaults=dict(name='Load Test Phone 128GB Black',model=model,brand='Staging',storage='128GB',colour='Black',serialized=True,stock_item=True,specs=SPECS,slug='load-test-phone'))
    if not product.images.exists():ProductImage.objects.create(product=product,path='staging/load.jpg',url='https://img.invalid/load.jpg')
    online_store.refresh_live(product);next_number=900000-IMEI.objects.filter(number__startswith='35693803').count()-1;left=count;made=[]
    while left>0:
        for shop in shops:
            take=min(20,left)
            if take<=0:break
            numbers=[]
            while len(numbers)<take:
                next_number-=1;number=imei(next_number)
                if not IMEI.objects.filter(number=number).exists():numbers.append([number])
            me=shopdefaults.shop_person(shop)
            bill=easy_retail.purchase(dict(branch=shop,party_name='Load Supplier',warehouse=shopdefaults.warehouse(shop),product=product,quantity=take,price='40000',asking_price='50000',pta_status='PTA',device_type='Used',condition='Good',
                staff=me,handler=me,posting_date=timezone.localdate(),payment_status='unpaid',amount_paid=None,method='Cash',imeis=numbers,notes='Load test stock'),owner)
            made+=[l.device_id for l in bill.reference.lines.all()];left-=take
    return product,made


def load(owner,levels,per_level):
    """Mixed work at rising concurrency. Every worker has its own session and database connection. Before each level the shops are stocked with
    2 x per_level identical phones; per_level counter sales name exact phones while per_level + 25% online orders take whichever phones are free,
    so the two channels really compete for the same stock and some requests MUST be refused (409 / 400). None may fail with a server error."""
    shops=list(Branch.objects.filter(is_online=False).order_by('code'));logins={b.pk:Staff.objects.filter(branch=b,user__isnull=False).first().user for b in shops}
    numbers=list(IMEI.objects.values_list('number',flat=True)[:500]);out=[]
    for level in levels:
        product,made=stock_up(owner,shops,per_level*2)
        variants=[v for v in oo.variants() if v['product'].pk==product.pk];phones=list(Device.objects.filter(pk__in=made[:per_level]).select_related('product','branch'))
        debtors=list(Document.objects.filter(kind__in=s.SELL,status='Submitted',is_return=False,outstanding__gt=1000)[:per_level])
        jobs=[];counter=[0];lock=threading.Lock()
        def order(variant,n):
            def run():
                body=dict(external_ref=f'TM-LOAD{uuid.uuid4().hex[:10].upper()}-ABCDEF',customer=dict(name='Load Test',phone=f'+92301{n:07d}',city='Lahore',address='Street',province='Punjab'),payment_method='Cash on Delivery',delivery_fee='0',
                          items=[dict(product_id=str(variant['product'].pk),condition=variant['condition'],pta_status=variant['pta_status'],device_type=variant['device_type'],price=str(variant['price']),quantity=1)])
                return 'online order',Client().post(reverse('api_online_order'),json.dumps(body),content_type='application/json',HTTP_X_API_KEY=KEY).status_code
            return run
        def sale(device):
            def run():
                client=Client();client.force_login(logins[device.branch_id])
                body=dict(branch=str(device.branch_id),customer_name='Load Buyer',payments=[dict(mode='Cash',amount=str(device.asking_price))],request_key=str(uuid.uuid4()),adjustments=dict(discount_type='amount',discount_value='0',tax_rule='',tax_version=''),
                          items=[dict(product=str(device.product_id),device=str(device.pk),warehouse=str(device.warehouse_id),quantity='1',rate=str(device.asking_price))])
                return 'counter sale',client.post(reverse('pos_checkout'),json.dumps(body),content_type='application/json').status_code
            return run
        def lookup(number,branch):
            def run():
                client=Client();client.force_login(logins[branch]);return 'IMEI search',client.get(reverse('imei_lookup'),{'q':number}).status_code
            return run
        def pay(doc):
            def run():
                pe=s.new_doc('Payment Entry',doc.branch,customer=doc.customer,reference=doc,handler=shopdefaults.shop_person(doc.branch),data=dict(amount='500.00',direction='Receive',mode='Cash'))
                try:s.submit(pe,owner);return 'payment',200
                except Exception as error:return 'payment',400
            return run
        for i in range(per_level//4):jobs.append(order(variants[0],level*1000+900+i))             # the extra 25%: more orders than there can be phones
        for i in range(per_level):
            if variants:jobs.append(order(variants[i%len(variants)],level*1000+i))
            if i<len(phones):jobs.append(sale(phones[i]))
            if numbers:jobs.append(lookup(numbers[(level*37+i)%len(numbers)],shops[i%len(shops)].pk))
            if i<len(debtors):jobs.append(pay(debtors[i]))
        results=[];errors=[]
        def worker(job):
            close_old_connections();started=time.perf_counter()
            try:kind,status=job()
            except Exception as error:kind,status='crash',type(error).__name__;errors.append(repr(error)[:200])
            finally:connections.close_all()
            return kind,status,(time.perf_counter()-started)*1000
        started=time.perf_counter()
        with ThreadPoolExecutor(max_workers=level) as pool:results=list(pool.map(worker,jobs))
        wall=time.perf_counter()-started;by={}
        for kind,status,ms in results:by.setdefault(kind,[]).append((status,ms))
        sold=Device.objects.filter(pk__in=made,status='Sold').count();held=Device.objects.filter(pk__in=made,status='Reserved').count();free=Device.objects.filter(pk__in=made,status='Available').count()
        row=dict(concurrency=level,requests=len(results),seconds=round(wall,1),per_second=round(len(results)/wall,1),stocked=len(made),sold_at_counter=sold,held_for_online_orders=held,still_free=free,server_errors=sum(1 for _,st,_ in results if st==500 or not isinstance(st,int)),crashes=errors[:5],kinds={})
        for kind,items in by.items():
            times=[ms for _,ms in items];codes={}
            for st,_ in items:codes[str(st)]=codes.get(str(st),0)+1
            row['kinds'][kind]=dict(count=len(items),statuses=dict(sorted(codes.items())),median_ms=round(statistics.median(times),1),p95_ms=round(pct(times,0.95),1),p99_ms=round(pct(times,0.99),1),max_ms=round(max(times),1))
        out.append(row);print('load:',json.dumps(row)[:600],flush=True)
    return out


def checks():
    problems=[f'{n}: {p}' for n,found in books_check.run_all() for p in found]+online_check.problems()+invariants.all_problems()
    return dict(problems=len(problems),first=problems[:10])


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--grow',type=int,default=1);parser.add_argument('--rounds',type=int,default=6);parser.add_argument('--levels',default='1,4,8,16')
    parser.add_argument('--per-level',type=int,default=40);parser.add_argument('--out',default='');args=parser.parse_args()
    import random;random.seed(1)
    guard();owner=build();report=dict(taken=time.strftime('%Y-%m-%d %H:%M:%S %z'),database='local PostgreSQL staging (isolated)',before=sizes())
    report['growth']=grow(owner,args.grow);report['published']=publish();report['after']=sizes();print('dataset:',report['after'],flush=True)
    report['pages']=pages(owner,args.rounds)
    report['load']=load(owner,[int(x) for x in args.levels.split(',')],args.per_level)
    report['books_after_load']=checks();print('books after load:',report['books_after_load'],flush=True)
    if args.out:
        with open(args.out,'w',encoding='utf-8') as handle:json.dump(report,handle,indent=1,default=str)


if __name__=='__main__':main()
