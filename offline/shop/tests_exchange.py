"""Phase D: one-screen phone exchange with trade-in holding stock."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import coa,exchange,easy_retail,vouchers,services as s


class ExchangeBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());coa.install_standard(self.user);self.client.force_login(self.user);self.today=timezone.localdate()
        self.model=PhoneModel.objects.create(code='I12',name='iPhone 12',brand='Apple');self.old=Product.objects.create(code='IP12-64',name='iPhone 12 64GB',model=self.model,storage='64GB',serialized=True)
        vouchers.post(dict(branch=self.branch,type='capital',posting_date=self.today,amount=Decimal('500000'),account=str(Account.objects.get(code='COA-3100').pk),mode='Cash',description='Float'),self.user)
        data=dict(branch=self.branch,party_name='Ex Supplier',product=self.product,quantity=1,price=100000,asking_price=150000,pta_status='PTA',device_type='New',condition='Excellent',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,
                  posting_date=self.today,payment_status='full',method='Cash',imeis=[[imei(401)]])
        self.new_phone=easy_retail.purchase(data,self.user).reference.lines.get().device
    def form(self,price=150000,**extra):
        data=dict(branch=self.branch,party_name='Exchange Customer',phone=self.new_phone,pta_status='PTA',price=Decimal(price),posting_date=self.today,staff=self.seller,buyer=self.buyer,handler=self.handler,warehouse=self.warehouse,method='Cash',payment_reference='',notes='')
        data.update(extra);return data
    def incoming(self,value=65000,number=402,**extra):
        row=dict(product=self.old,value=Decimal(value),imeis=[imei(number)],condition='Good',grade=8,pta='PTA',notes='Small scratch');row.update(extra);return row
    def cash(self):return s.account_balance(self.branch,Account.objects.get(pk=ShopSettings.objects.get(branch=self.branch).payment_methods['Cash']))


class Posting(ExchangeBase):
    def test_customer_pays_the_difference_and_the_old_phone_waits_in_holding(self):
        before=self.cash();doc=exchange.post(self.form(150000),[self.incoming(65000)],self.user)
        self.assertEqual((doc.state,Decimal(doc.data['difference'])),('Settled',Decimal('85000')));self.assertEqual(self.cash(),before+Decimal('85000'))
        old=IMEI.objects.get(number=imei(402)).device;self.new_phone.refresh_from_db()
        self.assertEqual((old.status,old.purchase_cost,old.condition,old.grade,old.inspection_status),('Holding',Decimal('65000'),'Good',8,'Pending'));self.assertEqual(self.new_phone.status,'Sold')
        sale=Document.objects.get(pk=doc.data['sales_invoice']);bill=Document.objects.get(pk=doc.data['purchase_invoice']);self.assertEqual((sale.outstanding,bill.outstanding),(Decimal('0'),Decimal('0')))
        for d in (sale,bill,doc):
            t=d.ledger.aggregate(x=Sum('debit'),y=Sum('credit'));self.assertEqual(t['x'],t['y'])
    def test_holding_phone_cannot_be_sold_until_accepted_and_profit_is_tracked_when_resold(self):
        exchange.post(self.form(150000),[self.incoming(65000)],self.user);old=IMEI.objects.get(number=imei(402)).device
        with self.assertRaises(Exception):easy_retail.sale(dict(branch=self.branch,party_name='Eager',phone=old,price=80000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today,payment_status='full',method='Cash'),self.user)
        self.client.post(reverse('accept_holding',args=[old.pk]));old.refresh_from_db();self.assertEqual(old.status,'Available')
        resale=easy_retail.sale(dict(branch=self.branch,party_name='Eager',phone=old,price=80000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today,payment_status='full',method='Cash'),self.user)
        cost=resale.ledger.filter(account__pk=s.account(resale,'cogs').pk).aggregate(d=Sum('debit'))['d'];self.assertEqual(cost,Decimal('65000.00'))       # margin 15,000 on the trade-in
    def test_shop_pays_the_customer_when_the_old_phone_is_worth_more(self):
        before=self.cash();doc=exchange.post(self.form(100000),[self.incoming(130000)],self.user)
        self.assertEqual(Decimal(doc.data['difference']),Decimal('-30000'));self.assertEqual(self.cash(),before-Decimal('30000'))
    def test_two_old_phones_and_an_even_swap(self):
        before=self.cash();doc=exchange.post(self.form(100000),[self.incoming(40000,403),self.incoming(60000,404,condition='Fair',grade=6)],self.user)
        self.assertEqual(Decimal(doc.data['trade_in_value']),Decimal('100000'));self.assertEqual(self.cash(),before)
        self.assertEqual(sorted((d.condition,d.grade) for d in Device.objects.filter(status='Holding')),[('Fair',6),('Good',8)])
    def test_bank_difference_needs_a_reference(self):
        with self.assertRaises(ValidationError):
            form=exchange.ExchangeForm({**{k:(v.pk if hasattr(v,'pk') else str(v)) for k,v in self.form().items()},'method':'Bank Transfer'},user=self.user,branch=self.branch);self.assertFalse(form.is_valid());raise ValidationError('x')
    def test_everything_is_rolled_back_if_the_trade_in_imei_already_exists(self):
        taken=Device.objects.create(code='TAKEN',branch=self.branch,product=self.old,warehouse=self.warehouse);IMEI.objects.create(number=imei(405),device=taken,slot=1)
        with self.assertRaises(Exception):exchange.post(self.form(),[self.incoming(65000,405)],self.user)
        self.new_phone.refresh_from_db();self.assertEqual(self.new_phone.status,'Available');self.assertFalse(Document.objects.filter(kind='Mobile Exchange').exists())


class Screen(ExchangeBase):
    def body(self,**extra):
        body=dict(request_key='4d2a1d5e-bbbb-4ccc-8ddd-0123456789ab',branch=str(self.branch.pk),party_name='Screen Customer',phone=str(self.new_phone.pk),pta_status='PTA',price='150000',posting_date=str(self.today),staff=str(self.seller.pk),buyer=str(self.buyer.pk),handler=str(self.handler.pk),
                  warehouse=str(self.warehouse.pk),method='Cash',in_product_1=str(self.old.pk),in_value_1='65000',in_imeis_1=imei(406),in_condition_1='Good',in_grade_1='8',in_pta_1='PTA');body.update(extra);return body
    def test_screen_posts_once_and_explains_the_result(self):
        first=self.client.post(reverse('exchange'),self.body(),follow=True);self.client.post(reverse('exchange'),self.body())
        self.assertContains(first,'Customer paid PKR 85,000.00');self.assertEqual(Document.objects.filter(kind='Mobile Exchange').count(),1)
    def test_missing_imei_or_old_phone_is_reported_without_posting(self):
        page=self.client.post(reverse('exchange'),self.body(in_imeis_1=''));self.assertContains(page,'enter its IMEI');self.assertEqual(Device.objects.filter(status='Holding').count(),0)
        page=self.client.post(reverse('exchange'),self.body(in_product_1='',in_value_1='',in_imeis_1=''));self.assertContains(page,'at least one old phone')
    def test_page_renders_for_a_manager(self):
        page=self.client.get(reverse('exchange'));self.assertContains(page,'Old phone 1');self.assertContains(page,'iPhone 12 64GB')
