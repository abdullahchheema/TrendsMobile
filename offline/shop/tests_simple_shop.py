"""One super admin, one login per shop, no staff or warehouse questions, own-shop analytics, shop-to-shop transfers with a slip."""
import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei,Workflows
from .tests_transfers import transfer_setup
from . import bulk,catalog_entry,dashboard_metrics as dm,easy_retail,reports,shop_accounts,shopdefaults,transfers,services as s


class Base(TestCase):
    def setUp(self):
        transfer_setup(self);self.today=timezone.localdate()
        self.shop_a=shop_accounts.create_login(self.branch,'shop-a-login','Strong-pass-for-A-77!')
        self.shop_b=shop_accounts.create_login(self.other,'shop-b-login','Strong-pass-for-B-77!')
    def login(self,user):self.client.force_login(user)


class Accounts(Base):
    def test_each_shop_gets_one_login_tied_to_its_own_shop_only(self):
        a=Staff.objects.get(user=self.shop_a);self.assertEqual((a.branch_id,a.manager,a.can_buy,a.can_sell,a.can_handle_payments),(self.branch.pk,True,True,True,True))
        with self.assertRaisesMessage(ValidationError,'already has a login'):shop_accounts.create_login(self.branch,'another','Strong-pass-for-A-77!')
        with self.assertRaises(ValidationError):shop_accounts.create_login(Branch.objects.create(code='NEW',name='New',company=self.branch.company),'shop-a-login','Strong-pass-for-A-77!')       # username taken
    def test_super_admin_manages_accounts_and_a_shop_cannot(self):
        self.login(self.user);page=self.client.get(reverse('users'));self.assertContains(page,'Shop accounts');self.assertContains(page,'shop-a-login')
        self.login(self.shop_a);self.assertEqual(self.client.get(reverse('users')).status_code,403)
    def test_password_reset_and_disable_work(self):
        shop_accounts.reset_password(self.shop_a,'Brand-new-pass-for-A-99!');self.shop_a.refresh_from_db();self.assertTrue(self.shop_a.check_password('Brand-new-pass-for-A-99!'))
        self.login(self.user);person=Staff.objects.get(user=self.shop_a)
        self.client.post(reverse('users'),dict(action='toggle',person=str(person.pk)));self.shop_a.refresh_from_db();self.assertFalse(self.shop_a.is_active)
    def test_shop_creation_needs_no_warehouse_or_till_questions(self):
        from .setup_forms import CreateShopForm
        form=CreateShopForm();self.assertNotIn('warehouse_name',form.fields);self.assertNotIn('till_name',form.fields)


class NoStaffNoWarehouse(Base):
    def buy_page(self):return self.client.get(reverse('easy_entry',args=['purchase'])).content.decode()
    def test_buy_and_sell_screens_do_not_ask_for_staff_warehouse_or_selling_price(self):
        self.login(self.shop_a);buy=self.buy_page()
        for text in ('Bought by','Payment handled by','Keep stock at','Expected selling price','Sold by'):self.assertNotIn(text,buy,text)
        self.assertIn('name="warehouse"',buy)        # carried silently as a hidden value
        self.assertNotIn('Sold by',self.client.get(reverse('easy_entry',args=['sale'])).content.decode())
        self.assertNotIn('Sold by',self.client.get(reverse('pos')).content.decode())
        for name in ('bulk_purchase','exchange'):
            page=self.client.get(reverse(name)).content.decode()
            for text in ('Bought by','Money handled by','Keep old phones at','Selling price (each)','Receiving stock location'):self.assertNotIn(text,page,(name,text))
    def test_a_shop_login_buys_and_sells_without_choosing_anyone(self):
        self.login(self.shop_a)
        post=self.client.post(reverse('easy_entry',args=['purchase']),dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-0000000000a1',party_name='Seller One',brand='Samsung',model='A54',storage='128GB',colour='Black',quantity='1',price='40000',asking_price='55000',pta_status='PTA',device_type='Used',condition='Good',payment_status='unpaid',posting_date=str(self.today),imeis=imei(801)))
        self.assertEqual(post.status_code,302,getattr(post,'context',None) and post.context['form'].errors)
        device=Device.objects.get(imeis__number=imei(801));me=Staff.objects.get(user=self.shop_a)
        self.assertEqual((device.asking_price,device.branch_id,device.warehouse.branch_id),(55000,self.branch.pk,self.branch.pk));self.assertEqual(Document.objects.filter(kind='Purchase Receipt',buyer=me).count(),1)
        sale=self.client.post(reverse('easy_entry',args=['sale']),dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-0000000000a2',party_name='Buyer One',phone=str(device.pk),pta_status='PTA',price='55000',payment_status='unpaid',posting_date=str(self.today)))
        self.assertEqual(sale.status_code,302);self.assertEqual(Document.objects.get(kind='Sales Invoice').seller_id,me.pk)
    def test_bulk_rows_need_a_selling_price(self):
        row=dict(brand='Samsung',model='A54',storage='128GB',colour='Black',quantity=1,condition='Good',device_type='Used',grade='',pta_status='PTA',cost='40000',cost_mode='each',imeis=imei(802),notes='')
        with self.assertRaisesMessage(ValidationError,'selling price'):bulk.parse_rows(json.dumps([row]))
        self.assertEqual(bulk.parse_rows(json.dumps([dict(row,asking_price='55000')]))[0]['asking'],55000)
    def test_pos_lists_a_phone_without_a_price_so_the_cashier_types_it(self):
        self.login(self.shop_a)
        data=dict(branch=self.branch,party_name='P',brand='Oppo',model='A5',storage='64GB',colour='Blue',variant=catalog_entry.check(dict(brand='Oppo',model='A5',storage='64GB',colour='Blue')),quantity=1,price=Decimal('20000'),pta_status='PTA',device_type='Used',condition='Good',
                  warehouse=shopdefaults.warehouse(self.branch),staff=shopdefaults.person(self.shop_a,self.branch),handler=shopdefaults.person(self.shop_a,self.branch),posting_date=self.today,payment_status='unpaid',method='Cash',imeis=[[imei(803)]])
        easy_retail.purchase(data,self.shop_a)
        items=self.client.get(reverse('pos')).context['items'];item=next(i for i in items if i.get('serialized') and i['imeis']==[imei(803)]);self.assertEqual(Decimal(item['rate']),0)


class OwnShopAnalytics(Base):
    def test_super_admin_sees_every_shop_and_a_shop_sees_only_its_own(self):
        self.login(self.user);both=self.client.get(reverse('dashboard')).context['overview'];self.assertEqual({r['shop'] for r in both['compare']},{self.branch.name,self.other.name})
        self.login(self.shop_a);own=self.client.get(reverse('dashboard')).context['overview'];self.assertEqual([r['shop'] for r in own['compare']],[self.branch.name])
    def test_a_shop_cannot_open_another_shops_numbers_by_changing_the_address(self):
        self.login(self.shop_a);page=self.client.get(reverse('dashboard'),{'branch':str(self.other.pk)});self.assertEqual(page.status_code,404)
        with self.assertRaises(PermissionDenied):reports.run('Branch Comparison',self.shop_a,{})          # comparing shops is for the owner
        _,rows=reports.run('Daily Sales',self.shop_a,{'branch':str(self.other.pk)});self.assertEqual(rows,[])        # asking for another shop gives that shop's rows to nobody
    def test_menu_has_no_staff_roles_warehouses_or_tills_and_hides_owner_tools_from_a_shop(self):
        self.login(self.shop_a);side=self.client.get(reverse('dashboard')).content.decode().split('<aside',1)[1].split('</aside>',1)[0]
        for text in ('Staff','Roles','Warehouses','Cash tills','Chart of Accounts','Shop Accounts','All Shops Inventory','Posting accounts'):self.assertNotIn(text,side,text)
        for text in ('New Sale','IMEI Management','Expenses','Customer Dues'):self.assertIn(text,side,text)
        self.assertNotIn('Stock Transfers',side);self.assertNotIn('Send phones',side)
        self.login(self.user);owner=self.client.get(reverse('dashboard')).content.decode().split('<aside',1)[1].split('</aside>',1)[0]
        for text in ('Shop Accounts','Chart of Accounts','Fixed Assets','Stock Transfers','All Shops Inventory'):self.assertIn(text,owner,text)
        for text in ('Warehouses','Cash tills','Roles & permissions','Staff'):self.assertNotIn(text,owner,text)


class Receipt(Base):
    def test_receipt_headline_is_the_shop_that_sold_the_phone(self):
        device=Workflows.intake(self,5)[2]
        self.login(self.shop_a)
        sale=easy_retail.sale(dict(branch=self.branch,party_name='Receipt Buyer',phone=device,price=60000,pta_status='PTA',staff=shopdefaults.person(self.shop_a,self.branch),handler=shopdefaults.person(self.shop_a,self.branch),posting_date=self.today,payment_status='full',method='Cash'),self.shop_a)
        page=self.client.get(reverse('receipt',args=[sale.pk])).content.decode()
        self.assertIn('<h1>'+self.branch.name+'</h1>',page);self.assertNotIn('<h1>Covison ERP</h1>',page);self.assertNotIn('Sold by',page)
