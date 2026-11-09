"""IMEI capture on purchase, duplicate-phone protection, IMEI release on cancellation, and stock adjustments."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import services as s,easy_retail


class Base(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def buy(self,imeis='',qty=1,cost=50000):
        data=dict(branch=self.branch,party_name='IMEI Supplier',product=self.product,quantity=qty,price=cost,asking_price=65000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,
                  staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash',imeis=easy_retail.parse_imeis(imeis,qty))
        return easy_retail.purchase(data,self.user)
    def ledger_balanced(self,doc):
        t=doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(t['d'],t['c'])


class ImeiOnPurchase(Base):
    def test_each_phone_gets_its_own_imei_including_dual_sim(self):
        first,second,third=imei(1),imei(2),imei(3)
        bill=self.buy(f'{first} {second}\n{third}',qty=2)
        devices={d.imeis.count():d for d in Device.objects.all()}
        self.assertEqual(sorted(devices),[1,2]);self.assertEqual({i.number for i in IMEI.objects.all()},{first,second,third})
        self.assertEqual({i.number for i in devices[2].imeis.all()},{first,second})
    def test_blank_imei_box_is_still_allowed(self):
        self.buy('');self.assertEqual(IMEI.objects.count(),0);self.assertEqual(Device.objects.count(),1)
    def test_wrong_line_count_bad_check_digit_and_repeats_are_rejected_before_posting(self):
        good=imei(5)
        for text,qty,message in ((good,2,'one line per phone'),('123456789012345',1,'not a valid'),(f'{good}\n{good}',2,'appears twice'),(f'{good} {imei(6)} {imei(7)}',1,'at most two')):
            with self.assertRaisesMessage(ValidationError,message):easy_retail.parse_imeis(text,qty)
    def test_same_phone_cannot_be_bought_twice(self):
        number=imei(8);self.buy(number)
        with self.assertRaisesMessage(ValidationError,'cannot be bought twice'):easy_retail.parse_imeis(number,1)
    def test_form_shows_the_duplicate_message_to_the_user(self):
        number=imei(9);self.buy(number)
        page=self.client.post(reverse('easy_entry',args=['purchase']),dict(request_key='5f2c8f3e-8d2e-4a4e-9d3a-1b1b1b1b1b1b',branch=str(self.branch.pk),party_name='X',product=str(self.product.pk),quantity=1,imeis=number,pta_status='PTA',price=100,asking_price=200,device_type='Used',condition='Good',payment_status='unpaid',staff=str(self.buyer.pk),posting_date=str(timezone.localdate()),warehouse=str(self.warehouse.pk)))
        self.assertContains(page,'cannot be bought twice')
    def test_sale_screen_lists_the_imei_and_search_finds_it(self):
        number=imei(10);self.buy(number)
        page=self.client.get(reverse('easy_entry',args=['sale']));self.assertContains(page,'IMEI '+number)


class CancelledReceiptFreesImei(Base):
    def test_cancelling_a_purchase_releases_the_imei_for_correct_re_entry(self):
        number=imei(11);bill=self.buy(number);receipt=bill.reference
        s.cancel(bill,self.user);s.cancel(receipt,self.user)
        device=Device.objects.get();self.assertEqual(device.status,'Unavailable');self.assertEqual(device.imeis.count(),0);self.assertEqual(device.details['released_imeis'],[number])
        self.buy(number)  # now accepted again
        self.assertEqual(IMEI.objects.get(number=number).device.status,'Available')


class StockAdjustments(Base):
    def accessory(self,name='Case'):return Product.objects.create(code=name.upper(),name=name,stock_item=True,serialized=False)
    def receive(self,product,qty,rate):
        doc=s.new_doc('Purchase Receipt',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer)
        Line.objects.create(document=doc,product=product,warehouse=self.warehouse,quantity=qty,rate=rate);return s.submit(doc,self.user)
    def adjust(self,reason='Damaged',**lines):
        doc=s.new_doc('Stock Adjustment',self.branch,warehouse=self.warehouse,data={'reason':reason})
        for l in lines['lines']:Line.objects.create(document=doc,warehouse=self.warehouse,**l)
        return s.submit(doc,self.user)
    def test_writing_off_a_phone_removes_it_at_cost_and_balances_the_ledger(self):
        bill=self.buy(imei(20),cost=50000);device=Device.objects.get()
        doc=self.adjust('Lost / stolen',lines=[dict(product=self.product,device=device,quantity=-1,rate=0)])
        device.refresh_from_db();self.assertEqual(device.status,'Written off');self.assertIsNone(device.warehouse_id)
        self.assertEqual(doc.total,Decimal('50000.00'));self.ledger_balanced(doc)
        self.assertEqual(s.on_hand(self.product,self.warehouse),(Decimal(0),Decimal('0.00')))
        self.assertEqual(doc.ledger.filter(account__pk=s.account(doc,'expense').pk).aggregate(d=Sum('debit'))['d'],Decimal('50000.00'))
    def test_accessory_write_off_uses_average_cost_and_last_units_clear_exactly(self):
        case=self.accessory();self.receive(case,3,Decimal('33.33'))   # total 99.99
        first=self.adjust(lines=[dict(product=case,quantity=-2,rate=0)]);self.assertEqual(first.total,Decimal('66.66'))
        last=self.adjust(lines=[dict(product=case,quantity=-1,rate=0)]);self.assertEqual(last.total,Decimal('33.33'))
        self.assertEqual(s.on_hand(case,self.warehouse),(Decimal(0),Decimal('0.00')))
        self.ledger_balanced(first);self.ledger_balanced(last)
    def test_cannot_remove_more_than_is_in_stock(self):
        case=self.accessory();self.receive(case,2,Decimal('10'))
        with self.assertRaisesMessage(ValidationError,'Only 2'):self.adjust(lines=[dict(product=case,quantity=-5,rate=0)])
        self.assertEqual(s.on_hand(case,self.warehouse)[0],Decimal(2))
    def test_count_gain_adds_stock_at_average_cost_and_credits_expense(self):
        case=self.accessory();self.receive(case,2,Decimal('10'))
        doc=self.adjust('Count correction',lines=[dict(product=case,quantity=3,rate=0)])
        self.assertEqual(s.on_hand(case,self.warehouse),(Decimal(5),Decimal('50.00')));self.assertEqual(Decimal(doc.data['gain']),Decimal('30.00'));self.ledger_balanced(doc)
    def test_reason_zero_quantity_and_second_write_off_of_same_phone_are_rejected(self):
        self.buy(imei(21));device=Device.objects.get()
        with self.assertRaises(ValidationError):self.adjust('',lines=[dict(product=self.product,device=device,quantity=-1,rate=0)])
        with self.assertRaises(ValidationError):self.adjust(lines=[dict(product=self.accessory('Zero'),quantity=0,rate=0)])
        self.adjust(lines=[dict(product=self.product,device=device,quantity=-1,rate=0)])
        with self.assertRaises(ValidationError):self.adjust(lines=[dict(product=self.product,device=device,quantity=-1,rate=0)])
    def test_cancelling_an_adjustment_restores_the_phone_and_stock_value(self):
        self.buy(imei(22));device=Device.objects.get()
        doc=self.adjust(lines=[dict(product=self.product,device=device,quantity=-1,rate=0)]);s.cancel(doc,self.user)
        device.refresh_from_db();self.assertEqual((device.status,device.warehouse_id),('Available',self.warehouse.pk))
        self.assertEqual(s.on_hand(self.product,self.warehouse)[1],Decimal('50000.00'))
    def test_screen_posts_a_phone_write_off_once_even_if_resubmitted(self):
        self.buy(imei(23));device=Device.objects.get()
        body=dict(request_key='7a1c3d5e-1111-4222-8333-444455556666',branch=str(self.branch.pk),warehouse=str(self.warehouse.pk),reason='Damaged',phone=str(device.pk),posting_date=str(timezone.localdate()))
        for _ in range(2):self.client.post(reverse('stock_adjust'),body)
        self.assertEqual(Document.objects.filter(kind='Stock Adjustment').count(),1)
        self.assertContains(self.client.get(reverse('stock_adjust')),'ADJ-')
