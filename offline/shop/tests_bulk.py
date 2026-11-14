"""Phase D: bulk purchase with per-device IMEI, cost, condition and grade."""
import json
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fund,fixtures,imei
from . import bulk,services as s


class BulkBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
        self.model2=PhoneModel.objects.create(code='S21',name='Galaxy S21',brand='Samsung')
        self.s21=Product.objects.create(code='S21-256',name='Samsung S21 256GB',model=self.model2,storage='256GB',serialized=True)
    def row(self,product,qty,cost,imeis=None,**extra):
        base=dict(product=str(product.pk),quantity=qty,condition='Good',device_type='Used',grade='',pta_status='PTA',cost=str(cost),cost_mode='each',asking_price='90000',imeis=chr(10).join(imeis or []));base.update(extra);return base
    def data(self,**extra):
        base=dict(branch=self.branch,party_name='Bulk Supplier',warehouse=self.warehouse,posting_date=self.today,staff=self.buyer,payment_status='unpaid',method='Cash',handler=self.handler,notes='');base.update(extra);return base
    def device(self,number):return IMEI.objects.get(number=imei(number)).device


class Posting(BulkBase):
    def test_mixed_models_conditions_costs_and_imeis_land_on_the_right_phones(self):
        rows=bulk.parse_rows(json.dumps([
            self.row(self.s21,2,60000,[imei(301),imei(302)],condition='Excellent',grade='10'),
            self.row(self.product,1,50000,[imei(303)],condition='Fair',grade='7'),
            self.row(self.product,2,55000,[imei(304),imei(305)],condition='Good',grade='9'),
            self.row(self.s21,1,58000,[f'{imei(306)} {imei(307)}'],condition='Very Good')]))
        bill=bulk.post(self.data(),rows,self.user)
        expect={301:('Excellent',10,60000,self.s21),302:('Excellent',10,60000,self.s21),303:('Fair',7,50000,self.product),304:('Good',9,55000,self.product),305:('Good',9,55000,self.product),306:('Very Good',None,58000,self.s21)}
        for number,(cond,grade,cost,product) in expect.items():
            d=self.device(number);self.assertEqual((d.condition,d.grade,d.purchase_cost,d.product_id),(cond,grade,Decimal(cost),product.pk),number)
        self.assertEqual({i.number for i in self.device(306).imeis.all()},{imei(306),imei(307)})            # dual-SIM on one phone
        self.assertEqual(Device.objects.count(),6);self.assertEqual(bill.total,Decimal(60000*2+50000+55000*2+58000))
        ledger=bill.reference.ledger.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(ledger['d'],ledger['c'])
    def test_line_total_pricing_splits_exactly(self):
        self.assertEqual(sum(bulk.split_cost(Decimal('100.00'),3)),Decimal('100.00'));self.assertEqual(bulk.split_cost(Decimal('100.00'),3),[Decimal('33.34'),Decimal('33.33'),Decimal('33.33')])
        rows=bulk.parse_rows(json.dumps([dict(self.row(self.product,3,100000,[imei(311),imei(312),imei(313)]),cost_mode='total')]))
        bill=bulk.post(self.data(),rows,self.user);self.assertEqual(bill.total,Decimal('100000.00'))
        self.assertEqual(sorted(d.purchase_cost for d in Device.objects.all()),[Decimal('33333.33'),Decimal('33333.33'),Decimal('33333.34')])
    def test_part_payment_and_supplier_balance(self):
        rows=bulk.parse_rows(json.dumps([self.row(self.product,2,50000,[imei(321),imei(322)])]));fund(self.branch,self.user,'30000')      # money cannot leave an empty till (D8): the owner puts in what this purchase pays
        bill=bulk.post(self.data(payment_status='part',amount_paid=Decimal('30000')),rows,self.user);self.assertEqual((bill.total,bill.outstanding),(Decimal('100000.00'),Decimal('70000.00')))
    def test_everything_rolls_back_if_one_phone_is_invalid_at_posting_time(self):
        IMEI.objects.create(number=imei(331),device=Device.objects.create(code='PRE',branch=self.branch,product=self.product,warehouse=self.warehouse),slot=1)
        rows=bulk.parse_rows(json.dumps([self.row(self.product,1,1000,[imei(332)])]))
        rows[0]['imeis']=[[imei(331)]]                                      # slips past parsing: the database must still refuse it
        with self.assertRaises(Exception):bulk.post(self.data(),rows,self.user)
        self.assertEqual(Device.objects.exclude(code='PRE').count(),0);self.assertEqual(Document.objects.filter(kind='Purchase Receipt').count(),0)


class Validation(BulkBase):
    def check(self,rows,message,**kwargs):
        with self.assertRaisesMessage(ValidationError,message):bulk.parse_rows(json.dumps(rows),**kwargs)
    def test_duplicates_counts_and_check_digits_are_caught_before_posting(self):
        self.check([self.row(self.product,1,1,[imei(341)]),self.row(self.s21,1,1,[imei(341)])],'used twice')
        self.check([self.row(self.product,2,1,[imei(342)])],'one line per phone')
        self.check([self.row(self.product,1,1,['123456789012345'])],'not a valid')
    def test_existing_imei_cannot_be_bought_again(self):
        IMEI.objects.create(number=imei(351),device=Device.objects.create(code='OLD',branch=self.branch,product=self.product,warehouse=self.warehouse),slot=1)
        self.check([self.row(self.product,1,1,[imei(351)])],'cannot be bought twice')
    def test_imeis_are_required_unless_the_user_opts_out(self):
        self.check([self.row(self.product,2,1,[])],'scan or type 2 IMEI')
        rows=bulk.parse_rows(json.dumps([self.row(self.product,2,50000,[])]),require_imei=False);bill=bulk.post(self.data(),rows,self.user)
        self.assertEqual((Device.objects.count(),IMEI.objects.count()),(2,0));self.assertEqual(bill.total,Decimal('100000.00'))
    def test_row_fields_are_validated(self):
        self.check([],'at least one phone');self.check([self.row(self.product,0,1,[])],'between 1 and 100',require_imei=False)
        self.check([self.row(self.product,1,0,[imei(361)])],'more than zero');self.check([dict(self.row(self.product,1,1,[imei(362)]),grade='11')],'1 to 10')
        self.check([dict(self.row(self.product,1,1,[imei(363)]),condition='Mint')],'valid condition');self.check([dict(self.row(self.product,1,1,[imei(364)]),pta_status='')],'PTA, Non-PTA or JV')


class Screen(BulkBase):
    def payload(self,key='9c2a1d5e-aaaa-4bbb-8ccc-0123456789ab'):
        rows=[self.row(self.product,2,50000,[imei(371),imei(372)],condition='Good',grade='8')]
        return dict(request_key=key,branch=str(self.branch.pk),party_name='Screen Supplier',warehouse=str(self.warehouse.pk),posting_date=str(self.today),staff=str(self.buyer.pk),payment_status='unpaid',rows_json=json.dumps(rows))
    def test_page_renders_with_the_scanner_and_catalogue_data(self):
        page=self.client.get(reverse('bulk_purchase'));self.assertContains(page,'Add phone');self.assertContains(page,'One price for all the phones');self.assertContains(page,'Samsung S21 256GB');self.assertContains(page,'data-bulk')
    def test_posting_twice_creates_one_purchase_and_shows_the_confirmation(self):
        first=self.client.post(reverse('bulk_purchase'),self.payload(),follow=True);self.client.post(reverse('bulk_purchase'),self.payload())
        self.assertEqual(Document.objects.filter(kind='Purchase Receipt').count(),1);self.assertEqual(Device.objects.count(),2);self.assertContains(first,'Purchase saved')
    def test_errors_keep_the_rows_so_nothing_is_retyped(self):
        data=self.payload();data['rows_json']=json.dumps([self.row(self.product,2,50000,[imei(373)])])
        page=self.client.post(reverse('bulk_purchase'),data);self.assertContains(page,'one line per phone');self.assertContains(page,'&quot;quantity&quot;: 2')
        self.assertEqual(Device.objects.count(),0)

class OneBulkPrice(BulkBase):
    def test_one_total_is_shared_equally_over_every_phone_to_the_paisa(self):
        rows=bulk.parse_rows(json.dumps([dict(self.row(self.product,1,0,[imei(381)]),cost=''),dict(self.row(self.s21,2,0,[imei(382),imei(383)]),cost='')]),bulk_total=Decimal('100000.01'))
        shares=[c for r in rows for c in r['costs']];self.assertEqual(sum(shares),Decimal('100000.01'));self.assertEqual(len(shares),3);self.assertLessEqual(max(shares)-min(shares),Decimal('0.01'))
    def test_saving_with_one_total_posts_that_total(self):
        rows=bulk.parse_rows(json.dumps([dict(self.row(self.product,1,0,[imei(391)]),cost=''),dict(self.row(self.product,1,0,[imei(392)]),cost='')]),bulk_total=Decimal('90000'))
        self.assertEqual(bulk.post(self.data(),rows,self.user).total,Decimal('90000.00'))
