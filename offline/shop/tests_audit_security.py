"""QA audit, phase 11: who can reach what. Enforced by the server, tested without the screens.

Every named page of the ERP is requested as each kind of user (nobody, shop A, shop B's records as shop A, the online login, the owner),
with the record ids in the address swapped for records that belong to someone else. Then the same is tried with POSTs that would change
something. A hidden button is not a permission: what counts is the answer of the server and whether anything changed."""
import csv
import json
import os
import uuid
from decimal import Decimal
from pathlib import Path
from django.db.models import Sum
from django.test import Client,TestCase,override_settings
from django.urls import get_resolver,reverse
from .models import *
from . import online_orders as oo,online_store,shopdefaults,services as s
from .tests_audit_base import AuditMixin,P,PASSWORD
from .tests_audit_online import KEY,SPECS

RESULTS=Path(__file__).resolve().parents[2]/'qa-audit'/'test-results'/'access-matrix.csv'
PUBLIC={'login','health',None}                                   # reachable without signing in (health shows no business data)
KEYED={'api_online_order','api_online_order_status','api_online_config'}       # the website's API: its own shared key, no session
OWNER_ONLY={'staff_list','staff_detail','setup','setup_new','setup_finance','users','tax_rules','tax_rule_new','tax_rule_edit','audit_log','periods','chart','year_end','assets','deferrals','loans',
            'journals','journal_new','journal_edit','stock_transfer','transfer_detail','transfer_slip','change_staff'}
ONLINE_PAGES={'online_settings','online_dashboard','online_store','online_edit','online_orders','online_order','online_order_new','online_stock','online_accounts',
              'online_money_in','online_money_out','online_expenses','online_customer_dues','online_cash_and_bank','online_history','online_photo_upload','online_photo_order','online_photo_update','online_photo_delete'}


class SecurityBase(AuditMixin,TestCase):
    def setUp(self):
        self.build();self.capital(self.a,'1000000');self.capital(self.b,'1000000')
        Product.objects.filter(pk=self.phone_product.pk).update(specs=SPECS,slug='galaxy-a99-128-black')
        self.image=ProductImage.objects.create(product=self.phone_product,path='audit/a99.jpg',url='https://img.invalid/a99.jpg');self.phone_product.refresh_from_db();online_store.refresh_live(self.phone_product)
        OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('0'),free_delivery_from=None,cod_enabled=True));self.online=oo.online_branch()
        # shop B's private records
        self.b_phone,self.b_bill=self.buy_phone(self.b,'70000','90000',pay='unpaid',supplier='Supplier Of B')
        self.b_sold,_=self.buy_phone(self.b,'60000','85000',pay='full')
        self.b_sale=Document.objects.get(pk=self.pos(self.login_b,self.b,[(self.b_sold,1,'85000')],[dict(mode='Cash',amount='5000.00')],customer='Customer Of B').json()['id'])
        self.b_customer=self.b_sale.customer;self.b_supplier=self.b_bill.supplier;self.b_staff=shopdefaults.shop_person(self.b)
        # shop A's own records, and one online order supplied by shop A
        self.a_phone,_=self.buy_phone(self.a,'80000','100000',pay='full');self.a_online_phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
        Device.objects.filter(pk=self.a_phone.pk).update(asking_price=Decimal('99000'))
        self.order=oo.create(dict(external_ref='TM-SECURITY01-ABCDEF',customer=dict(name='Online Buyer',phone='+923331234567',address='Flat 3',city='Karachi'),payment_method='Cash on Delivery',delivery_fee='0',
            items=[dict(product_id=str(self.phone_product.pk),condition='Good',pta_status='PTA',device_type='Used',price='100000',quantity=1)]))[0]
        oo.confirm(self.order,self.owner);oo.dispatch(self.order,self.owner,'TCS','T-1');oo.deliver(self.order,self.owner,'Cash');self.online_invoice=Document.objects.get(online_order=self.order,kind__in=s.SELL)
        self.rule_b=self.tax_rule(self.b,'17')
        self.transfer=None
    def kwargs_for(self,name,pattern,target='b'):
        """Address parameters for a page, pointing at shop B's records (or, for target='online', at the online order's)."""
        doc=self.b_sale if target=='b' else self.online_invoice
        route=str(pattern.pattern);out={}
        for part in route.split('<')[1:]:
            spec=part.split('>')[0];kind,_,key=spec.partition(':') if ':' in spec else ('str','',spec)
            if key=='pk':
                value={'party':self.b_customer.pk,'party_account':self.b_customer.pk,'statement':self.b_customer.pk,'device':self.b_phone.pk,'device_history':self.b_phone.pk,'accept_holding':self.b_phone.pk,
                       'staff_detail':self.b_staff.pk,'setup_finance':self.b.pk,'online_order':self.order.pk,'online_edit':self.phone_product.pk,'online_photo_upload':self.phone_product.pk,'online_photo_order':self.phone_product.pk,
                       'online_photo_update':self.image.pk,'online_photo_delete':self.image.pk,'accessory_edit':self.phone_product.pk,'tax_rule_edit':self.rule_b.pk,'purchase_return':self.b_bill.pk,
                       'master_edit':self.b_customer.pk,'evidence':'356938030000019'}.get(name,doc.pk)
                out['pk']=value
            elif key=='kind':out['kind']={'master_new':'Customer','master_edit':'Customer','new':'Sales Invoice','edit':'Sales Invoice','easy_entry':'sale'}.get(name,'Sales Invoice')
            elif key=='name':out['name']='Sales Register' if name=='report' else 'cancel' if name=='action' else 'x.pdf'
            elif key=='path':out['path']='audit/a99.jpg'
            elif key=='ref':out['ref']='TM-SECURITY01-ABCDEF'
            elif key=='slug':out['slug']='sales-invoice'
            elif key=='code':out['code']=doc.code
        return out
    def pages(self):
        return [(p.name,p) for p in get_resolver().url_patterns if getattr(p,'name',None)]
    def sweep(self,client,target='b'):
        """GET every named page. Returns {name: (status, where a redirect went)}."""
        out={}
        for name,pattern in self.pages():
            if name in ('logout',):continue
            response=client.get(reverse(name,kwargs=self.kwargs_for(name,pattern,target)))
            out[name]=(response.status_code,response.headers.get('Location',''))
        return out
    def private_text_of_b(self):
        return ('Customer Of B','Supplier Of B',self.b_sale.code,self.b_bill.code,self.b_phone.code,self.b_phone.imeis.get().number,self.b_sold.imeis.get().number)
    def snapshot(self):
        return (Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count(),Party.objects.count(),tuple(Device.objects.order_by('pk').values_list('status','branch_id')),
                tuple(Document.objects.order_by('pk').values_list('status','outstanding')),OnlineOrder.objects.get(pk=self.order.pk).status,Branch.objects.get(pk=self.b.pk).lock_through)


_matrix={}


def tearDownModule():
    if os.environ.get('AUDIT_WRITE_RESULTS')!='1' or not _matrix:return
    RESULTS.parent.mkdir(parents=True,exist_ok=True);roles=sorted(_matrix);names=sorted({n for r in _matrix.values() for n in r})
    with open(RESULTS,'w',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle);writer.writerow(['Page (URL name)']+roles)
        for name in names:writer.writerow([name]+[str(_matrix[r].get(name,('',''))[0]) for r in roles])


class WhoCanOpenWhat(SecurityBase):
    def test_nobody_signed_in_reaches_nothing(self):
        seen=self.sweep(Client());_matrix['1 not signed in']=seen
        for name,(status,where) in seen.items():
            if name in PUBLIC:continue
            if name in KEYED:self.assertIn(status,(401,405),name);continue
            if name=='product_image_file':self.assertIn(status,(200,404),name);continue          # developer machines only; the live system answers 404 (see test below)
            self.assertEqual(status,302,name);self.assertTrue(where.startswith('/login/'),f'{name} -> {where}')
        for name,pattern in self.pages():                                                       # and cannot change anything either
            if name in PUBLIC or name in KEYED or name=='logout':continue
            self.assertIn(Client().post(reverse(name,kwargs=self.kwargs_for(name,pattern)),{}).status_code,(302,403,404,405),name)
    def test_a_shop_cannot_open_another_shops_records(self):
        client=self.as_user(self.login_a);seen=self.sweep(client);_matrix['2 shop A asking for shop B records']=seen;private=self.private_text_of_b()
        for name,pattern in self.pages():
            if name=='logout' or name in PUBLIC or name in KEYED:continue
            status,where=seen[name]
            if name in OWNER_ONLY or name in ONLINE_PAGES:self.assertIn(status,(403,404,405),name);continue          # 405: the page only takes a POST, which is tried in WhoCanChangeWhat
            if 'pk' in self.kwargs_for(name,pattern):self.assertIn(status,(403,404,302,405),f'{name} answered {status} for a record of another shop')
            if status==200:
                text=client.get(reverse(name,kwargs=self.kwargs_for(name,pattern))).content.decode()
                for secret in private:self.assertNotIn(secret,text,f'{name} shows {secret} of shop B to shop A')
    def test_a_shop_cannot_list_or_search_another_shops_records(self):
        client=self.as_user(self.login_a);private=self.private_text_of_b()
        pages=[reverse('collection',args=[kind]) for kind in ('Sales Invoice','Purchase Invoice','Purchase Receipt','Payment Entry','Customer','Supplier','Mobile Device')]
        pages+=[reverse('search')+'?q='+term for term in ('Of B','Audit Supplier','Galaxy','PHONE-','SALE-','BILL-')]          # words that match shop B's records without being the secret itself (the page repeats what was typed)
        pages+=[reverse('imei_lookup')+'?q=Galaxy',reverse('imei_lookup')+'?q=35693803',reverse('customer_dues'),reverse('supplier_dues'),reverse('money_history'),reverse('cash_and_bank'),reverse('customer_payments'),reverse('supplier_payments'),
                reverse('returns')+'?q=Customer',reverse('overdue'),reverse('accessories'),reverse('pos'),reverse('dashboard'),reverse('report_center')]
        pages+=[reverse('report',args=[name])+f'?branch={self.b.pk}' for name in ('Sales Register','Purchase Register','Accounts Receivable','Accounts Payable','Mobile Inventory','Profit and Loss Statement','Daily Sales','IMEI Report')]
        pages+=[reverse('report',args=[name]) for name in ('Sales Register','Accounts Receivable','Accounts Payable','Mobile Inventory','Profit and Loss Statement','Stock Balance','Device Profit','Expense Report')]
        pages+=[reverse('ledger')+f'?branch={self.b.pk}&book=cash',reverse('pos')+f'?branch={self.b.pk}',reverse('dashboard')+f'?branch={self.b.pk}',reverse('money_history')+f'?branch={self.b.pk}',reverse('customer_dues')+f'?branch={self.b.pk}']
        for url in pages:
            response=client.get(url);text=response.content.decode()
            for secret in private:self.assertNotIn(secret,text,f'{url} ({response.status_code}) shows {secret}')
    def test_the_stock_in_all_shops_page_shows_quantities_but_no_costs_or_people(self):
        text=self.as_user(self.login_a).get(reverse('stock_everywhere')).content.decode()
        for secret in ('Customer Of B','Supplier Of B','70,000','70000','60,000','60000'):self.assertNotIn(secret,text)
    def test_the_online_login_is_shut_out_of_the_shops(self):
        client=self.as_user(self.online_login);seen=self.sweep(client);_matrix['3 online login asking for shop B records']=seen
        for name,(status,where) in seen.items():
            if name in PUBLIC or name in KEYED:continue
            if name in ONLINE_PAGES or name in ('live_state','password','display_name'):self.assertIn(status,(200,302,405),name);continue
            if name=='dashboard':self.assertEqual((status,where),(302,reverse('online_dashboard')));continue
            if name=='product_image_file':continue
            self.assertEqual(status,403,f'{name} answered {status} to the online login')
        online=self.sweep(client,'online');_matrix['4 online login asking for online records']=online
        for name in ('detail','receipt','return_sale'):self.assertEqual(online[name][0],200,name)             # the invoice of an online order is theirs to open
        for name in ('action','edit','payment_plan','change_staff','purchase_return'):self.assertIn(online[name][0],(403,405),name)
        # shop money, shop customers, shop reports: nothing
        for url in (reverse('money_in'),reverse('cash_and_bank'),reverse('customer_dues'),reverse('report',args=['Profit and Loss Statement']),reverse('pos'),reverse('collection',args=['Sales Invoice']),
                    reverse('party_account',args=[self.b_customer.pk]),reverse('statement',args=[self.b_customer.pk]),reverse('search')+'?q=Customer',reverse('ledger')+'?book=cash'):
            self.assertEqual(client.get(url).status_code,403,url)
    def test_the_online_finance_pages_show_only_the_online_stores_money(self):
        client=self.as_user(self.online_login)
        for name in ('online_money_in','online_money_out','online_expenses','online_customer_dues','online_cash_and_bank','online_history','online_dashboard'):
            text=client.get(reverse(name)).content.decode()
            for secret in ('Customer Of B','Supplier Of B',self.b_sale.code,'Audit Shop B · Cash','1,000,000'):self.assertNotIn(secret,text,f'{name} shows {secret}')
            text=client.get(reverse(name)+f'?branch={self.b.pk}').content.decode()                    # asking for a shop by id changes nothing
            for secret in ('Customer Of B','Supplier Of B',self.b_sale.code):self.assertNotIn(secret,text,f'{name}?branch shows {secret}')
    def test_a_shop_is_shut_out_of_the_online_store(self):
        client=self.as_user(self.login_a)
        for name in sorted(ONLINE_PAGES):
            pattern=dict(self.pages())[name];url=reverse(name,kwargs=self.kwargs_for(name,pattern))
            self.assertIn(client.get(url).status_code,(403,405),name);self.assertEqual(client.post(url,{}).status_code,403,name)
        self.assertTrue(ProductImage.objects.filter(pk=self.image.pk).exists())
        self.assertEqual(client.post(reverse('online_order',args=[self.order.pk]),dict(action='deliver',method='Cash')).status_code,403)
        self.assertEqual(client.post(reverse('online_accounts'),dict(action='pay_shop',shop=str(self.a.pk),amount='80000',method='Cash',shop_method='Cash')).status_code,403)
        self.assertEqual(client.post(reverse('online_settings'),dict(action='delivery',delivery_fee='0')).status_code,403)
        text=client.get(reverse('detail',args=[self.online_invoice.pk]));self.assertIn(text.status_code,(403,404))           # the online invoice is in the Online Store's books, not the shop's
    def test_the_owner_reaches_everything(self):
        seen=self.sweep(self.as_user(self.owner));_matrix['5 owner']=seen
        for name,(status,where) in seen.items():
            if name in KEYED:continue
            if name in ('journal_edit','fund_transfer_edit'):continue                              # these open only their own kind of draft; the record in the address here is a sale
            self.assertIn(status,(200,302,405,404),f'{name} answered {status} to the owner')
    def test_photo_files_are_not_served_by_the_live_system_and_cannot_leave_their_folder(self):
        for path in ('../../config/settings.py','..%2F..%2Fmanage.py','/etc/passwd','products/../../manage.py'):
            self.assertEqual(Client().get('/product-images/'+path).status_code,404,path)
        with override_settings(SUPABASE_AUTH_ENABLED=True):self.assertEqual(Client().get(reverse('product_image_file',args=['audit/a99.jpg'])).status_code,404)


class WhoCanChangeWhat(SecurityBase):
    def refused(self,client,url,data,json_body=False,allowed=(302,400,403,404,405)):
        before=self.snapshot()
        response=client.post(url,json.dumps(data),content_type='application/json') if json_body else client.post(url,data)
        self.assertIn(response.status_code,allowed,f'{url} answered {response.status_code}');self.assertEqual(self.snapshot(),before,f'{url} changed something')
        return response
    def test_a_shop_cannot_act_on_another_shops_records(self):
        a=self.as_user(self.login_a);key=lambda:str(uuid.uuid4());wb=shopdefaults.warehouse(self.b);wa=shopdefaults.warehouse(self.a)
        for name in ('cancel','submit','checkout','return','bill','allocate','apply-return-credit','settle'):
            self.refused(a,reverse('action',args=[self.b_sale.pk,name]),dict(request_key=key(),devices=[str(self.b_sold.pk)],condition='x',reason='y',payment_mode='Cash',payment_amount='1'),allowed=(403,404))
        self.refused(a,reverse('action',args=[self.b_bill.pk,'cancel']),dict(request_key=key()),allowed=(403,404))
        # sell shop B's phone: by naming shop B, or by slipping its phone into a sale of shop A
        row=dict(product=str(self.b_phone.product_id),device=str(self.b_phone.pk),quantity='1',rate='1')
        self.refused(a,reverse('pos_checkout'),dict(branch=str(self.b.pk),customer_name='X',items=[dict(row,warehouse=str(wb.pk))],payments=[],request_key=key()),True,(400,403))
        self.refused(a,reverse('pos_checkout'),dict(branch=str(self.a.pk),customer_name='X',items=[dict(row,warehouse=str(wb.pk))],payments=[],request_key=key()),True,(400,403,404))
        self.refused(a,reverse('pos_checkout'),dict(branch=str(self.a.pk),customer_name='X',items=[dict(row,warehouse=str(wa.pk))],payments=[],request_key=key()),True,(400,403,404))
        # sell to, collect from or refund shop B's customer; pay shop B's supplier
        self.refused(a,reverse('pos_checkout'),dict(branch=str(self.a.pk),customer=str(self.b_customer.pk),items=[dict(product=str(self.a_phone.product_id),device=str(self.a_phone.pk),warehouse=str(wa.pk),quantity='1',rate='99000')],payments=[],request_key=key()),True,(400,404))
        for party in (self.b_customer,self.b_supplier):
            self.refused(a,reverse('party_account',args=[party.pk]),dict(request_key=key(),amount='100',method='Cash',date=str(self.today)),allowed=(403,404))
            self.refused(a,reverse('master_edit',args=[party.kind,party.pk]),dict(name='Renamed by A',phone='0300 0000000'),allowed=(403,404))
        self.refused(a,reverse('money_in')+f'?branch={self.b.pk}',dict(action='customer_payment',request_key=key(),party=str(self.b_customer.pk),amount='100',method='Cash',date=str(self.today)),allowed=(200,302,403,404))
        self.refused(a,reverse('money_in'),dict(action='customer_payment',request_key=key(),party=str(self.b_customer.pk),amount='100',method='Cash',date=str(self.today),branch=str(self.b.pk)),allowed=(200,302,403,404))
        self.refused(a,reverse('money_out'),dict(action='supplier_payment',request_key=key(),party=str(self.b_supplier.pk),amount='100',method='Cash',date=str(self.today)),allowed=(200,302,403,404))
        # returns, purchase returns, payment plans, device changes, holding acceptance, write-offs, vouchers in shop B
        self.refused(a,reverse('return_sale',args=[self.b_sale.pk]),dict(request_key=key(),devices=[str(self.b_sold.pk)],reason='Other',condition='Good, phone only',resolution='credit'),allowed=(403,404))
        self.refused(a,reverse('purchase_return',args=[self.b_bill.pk]),dict(request_key=key(),devices=[str(self.b_phone.pk)],reason='x'),allowed=(403,404))
        self.refused(a,reverse('payment_plan',args=[self.b_sale.pk]),dict(first_due=str(self.today),instalments='3'),allowed=(403,404))
        self.refused(a,reverse('device',args=[self.b_phone.pk]),dict(asking_price='1',device_type='Used',pta_status='PTA',condition='Good'),allowed=(403,404))
        self.refused(a,reverse('accept_holding',args=[self.b_phone.pk]),{},allowed=(403,404))
        self.refused(a,reverse('stock_adjust'),dict(request_key=key(),branch=str(self.b.pk),reason='Lost / stolen',phone=str(self.b_phone.pk),posting_date=str(self.today)),allowed=(200,302,403,404))
        self.refused(a,reverse('voucher_new')+f'?branch={self.b.pk}',dict(request_key=key(),branch=str(self.b.pk),type='drawings',amount='500000',account=str(Account.objects.get(code='COA-3300').pk),mode='Cash',description='x',posting_date=str(self.today)),allowed=(200,302,403,404))
        self.refused(a,reverse('settings')+f'?branch={self.b.pk}',dict(shop_name='Taken over',shop_phone='1',ageing_days='1'),allowed=(403,404))
    def test_a_shop_cannot_use_the_owners_powers(self):
        a=self.as_user(self.login_a);key=lambda:str(uuid.uuid4())
        self.refused(a,reverse('stock_transfer'),dict(request_key=key(),branch=str(self.b.pk),target=str(self.a.pk),phone=[str(self.b_phone.pk)],mode='dispatch'),allowed=(403,))
        self.refused(a,reverse('stock_transfer'),dict(request_key=key(),branch=str(self.a.pk),target=str(self.b.pk),phone=[str(self.a_phone.pk)],mode='dispatch'),allowed=(403,))
        self.refused(a,reverse('periods'),dict(branch=str(self.a.pk),lock_through=str(self.today)),allowed=(403,))
        self.refused(a,reverse('year_end'),dict(branch=str(self.a.pk),from_date=str(self.today),to_date=str(self.today)),allowed=(403,))
        self.refused(a,reverse('journal_new'),dict(branch=str(self.a.pk)),allowed=(403,))
        self.refused(a,reverse('staff_list'),dict(action='add',name='Ghost',branch=str(self.a.pk),commission='50'),allowed=(403,))
        self.refused(a,reverse('change_staff',args=[self.b_sale.pk]),dict(staff=str(self.b_staff.pk)),allowed=(403,))
        self.refused(a,reverse('users'),dict(action='create',shop=str(self.b.pk),username='intruder',password=PASSWORD),allowed=(403,))
        self.refused(a,reverse('setup_new'),dict(code='EVIL',name='Evil',company='Evil'),allowed=(403,))
        self.refused(a,reverse('setup_finance',args=[self.a.pk]),{},allowed=(403,))
        for kind in ('Account','Mobile Staff','Warehouse','Till','Shop','Item','Mobile Model'):self.refused(a,reverse('master_new',args=[kind]),dict(name='x',code='x'),allowed=(302,403))
        self.refused(a,reverse('assets'),dict(request_key=key(),action='add',branch=str(self.a.pk)),allowed=(403,));self.refused(a,reverse('loans'),dict(request_key=key(),action='add',branch=str(self.a.pk)),allowed=(403,))
        loan=reverse('voucher_new');self.refused(a,loan,dict(request_key=key(),branch=str(self.a.pk),type='loan_in',amount='900000',account=str(Account.objects.get(code='COA-2500').pk),mode='Cash',description='x',posting_date=str(self.today)),allowed=(200,302,400,403))
    def test_the_online_login_cannot_touch_shop_money_or_shop_records(self):
        o=self.as_user(self.online_login);key=lambda:str(uuid.uuid4())
        self.refused(o,reverse('pos_checkout'),dict(branch=str(self.a.pk),customer_name='X',items=[],payments=[],request_key=key()),True,(403,))
        self.refused(o,reverse('action',args=[self.b_sale.pk,'cancel']),dict(request_key=key()),allowed=(403,))
        self.refused(o,reverse('action',args=[self.online_invoice.pk,'cancel']),dict(request_key=key()),allowed=(403,))
        self.refused(o,reverse('money_in'),dict(action='owner_funds',request_key=key(),amount='1',method='Cash',date=str(self.today)),allowed=(403,))
        self.refused(o,reverse('party_account',args=[self.b_customer.pk]),dict(request_key=key(),amount='100',method='Cash',date=str(self.today)),allowed=(403,))
        self.refused(o,reverse('stock_transfer'),dict(request_key=key(),branch=str(self.a.pk),target=str(self.b.pk),phone=[str(self.a_phone.pk)]),allowed=(403,))
        self.refused(o,reverse('stock_adjust'),dict(request_key=key(),branch=str(self.a.pk),reason='Lost / stolen',phone=str(self.a_phone.pk),posting_date=str(self.today)),allowed=(403,))
        self.refused(o,reverse('periods'),dict(branch=str(self.a.pk),lock_through=str(self.today)),allowed=(403,))
        # inside its own finance pages it cannot post into a shop, or take a shop's customer's money
        self.refused(o,reverse('online_money_in')+f'?branch={self.b.pk}',dict(action='customer_payment',request_key=key(),party=str(self.b_customer.pk),amount='100',method='Cash',date=str(self.today),branch=str(self.b.pk)),allowed=(200,302,403,404))
        before=sum(P(e.debit)-P(e.credit) for e in LedgerEntry.objects.filter(branch=self.b))
        o.post(reverse('online_money_out')+f'?branch={self.b.pk}',dict(action='expense',request_key=key(),branch=str(self.b.pk),category=str(Account.objects.get(code='COA-6100').pk),amount='1000',method='Cash',date=str(self.today)))
        self.assertEqual(LedgerEntry.objects.filter(branch=self.b,document__posted_by=self.online_login).count(),0)             # whatever it posts lands in the Online Store's books only
        self.assertEqual(sum(P(e.debit)-P(e.credit) for e in LedgerEntry.objects.filter(branch=self.b)),before)
        # only the owner manages the payment accounts customers are told to pay into
        o.post(reverse('online_settings'),dict(action='account_add',method='Bank Transfer',title='Not the real account',account_number='PK00FAKE'));self.assertEqual(OnlinePaymentAccount.objects.count(),0)
    def test_a_login_that_is_switched_off_loses_access_at_once(self):
        a=self.as_user(self.login_a);self.assertEqual(a.get(reverse('pos')).status_code,200)
        Staff.objects.filter(user=self.login_a).update(enabled=False)
        self.assertEqual(a.get(reverse('pos')).status_code,403);self.refused(a,reverse('pos_checkout'),dict(branch=str(self.a.pk),customer_name='X',items=[],payments=[],request_key=str(uuid.uuid4())),True,(403,))
        o=self.as_user(self.online_login);self.assertEqual(o.get(reverse('online_orders')).status_code,200)
        OnlineStaff.objects.filter(user=self.online_login).update(enabled=False);self.assertEqual(o.get(reverse('online_orders')).status_code,403)
        gone=self.as_user(self.login_b);type(self.login_b).objects.filter(pk=self.login_b.pk).update(is_active=False);self.assertEqual(gone.get(reverse('pos')).status_code,302)       # a deleted or deactivated user is signed out
    def test_signing_out_ends_the_session(self):
        a=self.as_user(self.login_a);self.assertEqual(a.get(reverse('logout')).status_code,405)          # signing out is a POST: a link cannot do it
        a.post(reverse('logout'));self.assertEqual(a.get(reverse('pos')).status_code,302)
    def test_forged_cross_site_posts_are_refused(self):
        client=Client(enforce_csrf_checks=True);client.force_login(self.login_a)
        self.assertEqual(client.post(reverse('pos_checkout'),'{}',content_type='application/json').status_code,403)
        before=self.snapshot();mine=Document.objects.filter(branch=self.a,kind='Purchase Invoice').first()
        self.assertIn(client.post(reverse('action',args=[mine.pk,'cancel']),{'request_key':str(uuid.uuid4())}).status_code,(302,403))      # a form post is sent back with 'page expired'; either way it is not carried out
        self.assertIn(client.post(reverse('money_in'),dict(action='owner_funds',amount='1',method='Cash')).status_code,(302,403));self.assertEqual(self.snapshot(),before)
        self.assertIn(client.post(reverse('logout')).status_code,(302,403));self.assertEqual(client.get(reverse('pos')).status_code,200)       # still signed in: the forged sign-out did nothing
    def test_wrong_passwords_lock_the_login_for_a_while(self):
        from django.core.cache import cache
        cache.clear();client=Client()
        with self.settings(LOGIN_MAX_FAILURES=3):
            codes=[client.post(reverse('login'),dict(username='audit-shop-a',password='wrong-'+str(i))).status_code for i in range(5)];self.assertEqual(codes,[200,200,200,429,429])
            self.assertEqual(client.post(reverse('login'),dict(username='audit-shop-a',password=PASSWORD)).status_code,429)      # even the right password waits out the lock
        cache.clear()
    def test_search_and_filters_survive_hostile_input(self):
        a=self.as_user(self.login_a)
        for term in ("' OR 1=1 --",'"; DROP TABLE shop_document; --','<script>alert(1)</script>','%','_','\\','٠٣٠٠ فون','😀'*20,'a'*5000,'\x00','abc\x00def'):
            for url in (reverse('search'),reverse('imei_lookup'),reverse('returns'),reverse('accessories')):
                response=a.get(url,{'q':term});self.assertIn(response.status_code,(200,302,400),f'{url} {term[:20]!r}');self.assertEqual(response.status_code==400,'\x00' in term)
                self.assertNotIn('<script>alert(1)</script>',response.content.decode())
            self.assertEqual(a.get(reverse('collection',args=['Customer']),{'search':term}).status_code,400 if '\x00' in term else 200)
        self.assertTrue(Document.objects.exists())
        # a NUL typed (or pasted) into a form or sent in a sale is refused cleanly, never a server error
        from urllib.parse import urlencode
        fields=dict(action='owner_funds',request_key=str(uuid.uuid4()),amount='1',method='Cash',date=str(self.today),description='bad\x00text');before=Document.objects.count()
        self.assertEqual(a.post(reverse('money_in'),urlencode(fields),content_type='application/x-www-form-urlencoded').status_code,400)      # how a browser sends a form
        self.assertIn(a.post(reverse('money_in'),fields).status_code,(200,400));self.assertEqual(Document.objects.count(),before)               # as a file-upload form: refused by the form itself
        nul=a.post(reverse('pos_checkout'),json.dumps(dict(branch=str(self.a.pk),customer_name='Bad\x00Name',items=[],payments=[],request_key=str(uuid.uuid4()))),content_type='application/json')
        self.assertEqual(nul.status_code,400);self.assertIn('error',nul.json())
        for page in ('abc','-1','0','99999999999999999999'):self.assertEqual(a.get(reverse('collection',args=['Sales Invoice']),{'page':page}).status_code,200)
        for value in ('not-a-uuid','00000000-0000-0000-0000-000000000000',"1' OR '1'='1"):
            self.assertIn(a.get(reverse('pos'),{'branch':value}).status_code,(302,404));self.assertIn(a.get(reverse('dashboard'),{'branch':value}).status_code,(404,))
