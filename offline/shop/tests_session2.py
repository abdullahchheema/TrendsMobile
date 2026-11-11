"""Amount in words, payment sanity checks, faster purchases, accessories, grouped inventory, the Returns & Exchanges flow and small screen fixes."""
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import bulk,easy_retail,returns_flow,services as s
from .words import amount_in_words


class Words(TestCase):
    def test_pakistani_grouping(self):
        self.assertEqual(amount_in_words(2527000),'Twenty Five Lakh Twenty Seven Thousand Rupees Only')
        self.assertEqual(amount_in_words('100000'),'One Lakh Rupees Only')
        self.assertEqual(amount_in_words(10000000),'One Crore Rupees Only')
        self.assertEqual(amount_in_words('1250.50'),'One Thousand Two Hundred Fifty Rupees and Fifty Paisa Only')
        self.assertEqual(amount_in_words(0),'Zero Rupees Only');self.assertEqual(amount_in_words('abc'),'')


class PaymentRules(TestCase):
    def test_choice_and_amount_must_agree(self):
        total=Decimal('2527000')
        with self.assertRaisesMessage(ValidationError,'You chose “Paid in full”'):easy_retail.paid_amount(dict(payment_status='full',amount_paid=Decimal('1000000')),total)
        with self.assertRaisesMessage(ValidationError,'You chose “Pay later”'):easy_retail.paid_amount(dict(payment_status='unpaid',amount_paid=Decimal('5')),total)
        self.assertEqual(easy_retail.paid_amount(dict(payment_status='full',amount_paid=total),total),total)
        self.assertEqual(easy_retail.paid_amount(dict(payment_status='part',amount_paid=Decimal('2000000')),total),Decimal('2000000'))
    def test_cannot_pay_more_than_the_bill_or_a_negative_amount(self):
        with self.assertRaisesMessage(ValidationError,'cannot pay PKR 6,000,000.00'):easy_retail.paid_amount(dict(payment_status='part',amount_paid=Decimal('6000000')),Decimal('5000000'))
        with self.assertRaisesMessage(ValidationError,'cannot be negative'):easy_retail.paid_amount(dict(payment_status='part',amount_paid=Decimal('-5')),Decimal('100'))


class Purchases(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
    def rows(self,n,**extra):
        return json.dumps([dict(brand='Apple',model='iPhone 17 Pro Max',storage='512GB',colour='White',quantity=1,condition='Good',device_type='Used',grade='',pta_status='PTA',cost='505400',cost_mode='each',asking_price='600000',imeis=imei(5100+i),notes='',**extra) for i in range(n)])
    def post(self,n,**form):
        data=dict(branch=str(self.branch.pk),request_key=str(__import__('uuid').uuid4()),party_name='Muhammad Rafay',posting_date=str(self.today),payment_status='part',amount_paid='2000000',method='Cash',price_mode='each',rows_json=self.rows(n))
        data.update(form);return self.client.post(reverse('bulk_purchase'),data)
    def test_part_payment_is_kept_as_typed(self):
        r=self.post(5);self.assertEqual(r.status_code,302)
        bill=Document.objects.filter(kind='Purchase Invoice').get()
        self.assertEqual((bill.total,bill.outstanding),(Decimal('2527000.00'),Decimal('527000.00')))
    def test_paid_in_full_with_a_different_typed_amount_is_refused_not_guessed(self):
        r=self.post(5,payment_status='full',amount_paid='1000000')
        self.assertContains(r,'You chose “Paid in full”');self.assertFalse(Document.objects.filter(kind='Purchase Invoice').exists())
    def test_paying_more_than_the_bill_is_refused(self):
        r=self.post(2,amount_paid='9000000');self.assertContains(r,'cannot pay');self.assertFalse(Document.objects.filter(kind='Purchase Invoice').exists())
    def test_selling_price_is_required_for_every_phone(self):
        rows=json.loads(self.rows(1));rows[0]['asking_price']=''
        with self.assertRaisesMessage(ValidationError,'enter the selling price'):bulk.parse_rows(json.dumps(rows))
    def test_phones_get_their_selling_price(self):
        self.post(2,amount_paid='100000');self.assertEqual(set(Device.objects.values_list('asking_price',flat=True)),{Decimal('600000.00')})
    def test_query_count_does_not_grow_with_the_number_of_phones(self):
        counts=[]
        for n in (2,12):
            with CaptureQueriesContext(connection) as q:self.post(n,payment_status='unpaid',amount_paid='',party_name=f'Seller {n}')
            counts.append(len(q))
        self.assertLessEqual(counts[1]-counts[0],14,counts)


class Accessories(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
    def buy(self,rows,**extra):
        data=dict(branch=str(self.branch.pk),request_key=str(__import__('uuid').uuid4()),party_name='Accessory Supplier',posting_date=str(self.today),payment_status='unpaid',amount_paid='',method='Cash',rows_json=json.dumps(rows))
        data.update(extra);return self.client.post(reverse('accessory_buy'),data)
    def test_buying_accessories_adds_stock_with_a_selling_price(self):
        r=self.buy([dict(name='Silicone cover',category='Cover / Case',quantity='10',cost='300',price='800'),dict(name='20W adapter',category='Adapter',quantity='4',cost='1500',price='2500')])
        self.assertEqual(r.status_code,302)
        cover=Product.objects.get(name='Silicone cover');self.assertEqual((cover.selling_price,cover.serialized,cover.group),(Decimal('800.00'),False,'Cover / Case'))
        self.assertEqual(s.on_hand(cover,self.warehouse),(10,3000))
        self.assertEqual(Document.objects.get(kind='Purchase Invoice').total,Decimal('9000.00'))
    def test_second_purchase_reuses_the_item_and_updates_its_price(self):
        self.buy([dict(name='Silicone cover',category='Cover / Case',quantity='10',cost='300',price='800')])
        self.buy([dict(name='silicone COVER',category='Cover / Case',quantity='5',cost='320',price='900')])
        self.assertEqual(Product.objects.filter(serialized=False,name__iexact='silicone cover').count(),1)
        cover=Product.objects.get(name='Silicone cover');self.assertEqual(cover.selling_price,Decimal('900.00'));self.assertEqual(s.on_hand(cover,self.warehouse)[0],15)
    def test_price_and_quantity_are_required(self):
        self.assertContains(self.buy([dict(name='Cable',category='Cable',quantity='1',cost='100',price='')]),'enter the selling price')
        self.assertContains(self.buy([dict(name='Cable',category='Cable',quantity='0',cost='100',price='200')]),'between 1 and 10,000')
    def test_list_and_pos_show_the_accessory(self):
        self.buy([dict(name='Tempered glass',category='Screen Protector',quantity='20',cost='100',price='500')])
        page=self.client.get(reverse('accessories')+'?branch='+str(self.branch.pk));self.assertContains(page,'Tempered glass');self.assertContains(page,'20')
        pos=self.client.get(reverse('pos'));self.assertContains(pos,'Tempered glass')


class InventoryAndScreens(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
    def stock(self,n,condition='Good',base=5300):
        rows=[dict(brand='Apple',model='iPhone 13',storage='128GB',colour='Blue',quantity=1,condition=condition,device_type='Used',grade='8',pta_status='PTA',cost='100000',cost_mode='each',asking_price='120000',imeis=imei(base+i),notes='') for i in range(n)]
        data=dict(branch=self.branch,party_name='S',warehouse=self.warehouse,posting_date=self.today,staff=self.buyer,payment_status='unpaid',method='Cash',handler=self.handler)
        return bulk.post(data,bulk.parse_rows(json.dumps(rows)),self.user)
    def test_identical_phones_are_one_line_with_a_count(self):
        self.stock(5);self.stock(2,'Fair',5400)
        page=self.client.get(reverse('collection',args=['Mobile Device']))
        self.assertContains(page,'<strong>7</strong>');self.assertNotContains(page,'>Record<');self.assertContains(page,'<th>Colour</th>');self.assertNotContains(page,'<th>Condition</th>')
        group=page.context['rows']                       # condition no longer splits the line
        self.assertEqual([r['count'] for r in group],[7])
        units=self.client.get(reverse('collection',args=['Mobile Device'])+'?'+group[0]['query'])
        self.assertEqual(len(units.context['rows']),group[0]['count'])
    def test_header_names_the_shop_or_admin(self):
        self.assertContains(self.client.get(reverse('dashboard')),'Admin')
    def test_display_name_can_be_changed(self):
        self.client.post(reverse('display_name'),{'name':'Boss Owner'});self.assertContains(self.client.get(reverse('dashboard')),'Boss Owner')
    def test_stock_gain_is_not_a_negative_expense(self):
        product=Product.objects.create(code='CASE',name='Case',selling_price=500)
        doc=s.new_doc('Stock Adjustment',self.branch,warehouse=self.warehouse,data=dict(reason=s.ADJUST_REASONS[0]))
        Line.objects.create(document=doc,product=product,warehouse=self.warehouse,quantity=4,rate=700);s.submit(doc,self.user)
        expense=Account.objects.get(code='expense')
        self.assertEqual(LedgerEntry.objects.filter(account=expense).count(),0)


class ReturnsFlow(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
        capital=s.new_doc('Voucher',self.branch,posting_date=self.today,data=dict(type='capital',amount='5000000',mode='Cash',description='Opening cash',account=str(Account.objects.create(code='eq',name='Owner capital',account_type='Equity',root_type='Equity').pk)))
        s.submit(capital,self.user)
    def phone(self,n,price=100000):
        rows=[dict(brand='Apple',model='iPhone 13',storage='128GB',colour='Blue',quantity=1,condition='Good',device_type='Used',grade='',pta_status='PTA',cost='80000',cost_mode='each',asking_price=str(price),imeis=imei(5500+n),notes='')]
        data=dict(branch=self.branch,party_name='S',warehouse=self.warehouse,posting_date=self.today,staff=self.buyer,payment_status='unpaid',method='Cash',handler=self.handler)
        bulk.post(data,bulk.parse_rows(json.dumps(rows)),self.user);return Device.objects.get(imeis__number=imei(5500+n))
    def sale(self,device,paid):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=self.warehouse,quantity=1,rate=100000)
        return s.checkout(doc,self.user,[dict(mode='Cash',amount=str(paid))] if paid else [],self.handler)
    def test_refund_pays_the_customer_and_puts_the_phone_in_holding(self):
        device=self.phone(1);sale=self.sale(device,100000)
        credit=returns_flow.settle(self.user,sale,[str(device.pk)],{},'Good, phone only','Customer changed mind','refund','Cash','')
        device.refresh_from_db();self.assertEqual(device.status,'Holding')
        self.assertEqual(credit.data['returns_flow']['refunded'],'100000.00');self.assertEqual(credit.outstanding,0)
    def test_unpaid_sale_reduces_what_the_customer_owes_before_any_refund(self):
        device=self.phone(2);sale=self.sale(device,30000)
        credit=returns_flow.settle(self.user,sale,[str(device.pk)],{},'Good, phone only','Faulty / not working','refund','Cash','')
        flow=credit.data['returns_flow'];self.assertEqual(flow['applied_to_balance'],'70000.00');self.assertEqual(flow['refunded'],'30000.00')
        sale.refresh_from_db();self.assertEqual(sale.outstanding,0)
    def test_cannot_refund_more_than_is_due(self):
        device=self.phone(3);sale=self.sale(device,100000)
        with self.assertRaisesMessage(ValidationError,'at most'):returns_flow.settle(self.user,sale,[str(device.pk)],{},'Good, phone only','Other','refund','Cash','150000')
        device.refresh_from_db();self.assertEqual(device.status,'Sold')     # nothing was saved
    def test_exchange_credit_pays_for_the_replacement(self):
        old=self.phone(4);sale=self.sale(old,100000)
        credit=returns_flow.settle(self.user,sale,[str(old.pk)],{},'Good, phone only','Wrong item given','exchange','','')
        self.assertEqual(credit.outstanding,Decimal('-100000.00'))
        new=self.phone(5,price=120000);doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=doc,product=new.product,device=new,warehouse=self.warehouse,quantity=1,rate=120000)
        replacement=s.checkout(doc,self.user,[],self.handler)
        from . import returns
        used=returns.apply_to_sale(credit,replacement,self.user);replacement.refresh_from_db();credit.refresh_from_db()
        self.assertEqual((used,replacement.outstanding,credit.outstanding),(Decimal('100000.00'),Decimal('20000.00'),Decimal('0.00')))
    def test_screens_open(self):
        device=self.phone(6);sale=self.sale(device,100000)
        self.assertContains(self.client.get(reverse('returns')+'?q='+imei(5506)),'Return / exchange')
        self.assertContains(self.client.get(reverse('return_sale',args=[sale.pk])),'What is coming back?')
        r=self.client.post(reverse('return_sale',args=[sale.pk]),dict(request_key=str(__import__('uuid').uuid4()),devices=[str(device.pk)],reason='Other',condition='Good, phone only',resolution='refund',refund_method='Cash'))
        self.assertEqual(r.status_code,302);self.assertContains(self.client.get(r['Location']),'Return saved')
        self.assertContains(self.client.get(reverse('return_history')),'Refunded')
    def test_receipt_shows_no_warranty_line_when_none_given(self):
        device=self.phone(7);sale=self.sale(device,100000)
        self.assertNotContains(self.client.get(reverse('receipt',args=[sale.pk])),'No warranty')


class DuesHistoryAndNotes(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
    def sell(self,price='60000',paid='10000',note=''):
        from .tests_pos import PosBase
        return None
    def test_advance_payment_shows_as_what_you_owe_the_customer(self):
        from . import money
        cust=Party.objects.create(kind='Customer',name='Advance Cust',branch=self.branch)
        money.record_party_payment(self.user,cust,Decimal('800000'),'Cash',self.today)
        rows=money.customer_balances([self.branch]);row=[r for r in rows if r['party']==cust][0]
        self.assertEqual((row['owes_you'],row['you_owe']),(Decimal('0.00'),Decimal('800000.00')))
        page=self.client.get(reverse('customer_dues'));self.assertContains(page,'Advance Cust');self.assertContains(page,'You owe customers')
        hist=self.client.get(reverse('customer_payments'));self.assertContains(hist,'Advance / on account')
    def test_payment_history_and_back_button_pages_load(self):
        for name in ('customer_payments','supplier_payments','supplier_dues','purchase_returns','cash_and_bank'):
            r=self.client.get(reverse(name));self.assertEqual(r.status_code,200,name)
        self.assertContains(self.client.get(reverse('cash_and_bank')),'data-go-back')
