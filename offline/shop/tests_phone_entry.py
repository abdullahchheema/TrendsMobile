"""Typed phone details on the buy screens: brand, model, storage, colour and notes create or reuse catalogue records."""
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fund,fixtures,imei
from . import bulk,catalog_entry,easy_retail


class Resolver(TestCase):
    def test_creates_brand_model_and_variant_once_and_matches_ignoring_case_and_spaces(self):
        a=catalog_entry.resolve_variant(dict(brand='iphone',model='12 Pro Max',storage='512GB',colour='Grey'))
        b=catalog_entry.resolve_variant(dict(brand=' IPHONE ',model='12 Pro Max',storage='512GB',colour='grey'))
        self.assertEqual(a.pk,b.pk);self.assertEqual((a.storage,a.colour,a.serialized,a.model.brand,a.model.name),('512GB','Grey',True,'iphone','12 Pro Max'))
        self.assertEqual((Brand.objects.filter(name__iexact='iphone').count(),PhoneModel.objects.count(),Product.objects.count()),(1,1,1));self.assertEqual(a.name,'iphone 12 Pro Max 512GB Grey')
    def test_new_storage_makes_a_new_variant_of_the_same_model(self):
        a=catalog_entry.resolve_variant(dict(brand='iPhone',model='14 Pro Max',storage='256GB',colour='Black'))
        b=catalog_entry.resolve_variant(dict(brand='iPhone',model='14 Pro Max',storage='512GB',colour='Black'))
        self.assertNotEqual(a.pk,b.pk);self.assertEqual(a.model_id,b.model_id)
    def test_models_are_listed_only_under_their_own_brand(self):
        catalog_entry.resolve_variant(dict(brand='iPhone',model='12 Pro Max',storage='128GB',colour='Blue'))
        catalog_entry.resolve_variant(dict(brand='Samsung',model='S23 Ultra',storage='256GB',colour='Black'))
        cat=catalog_entry.catalog();self.assertEqual(cat['models']['iphone'],['12 Pro Max']);self.assertEqual(cat['models']['samsung'],['S23 Ultra'])
        self.assertIn('Samsung',cat['brands']);self.assertIn('Xiaomi',cat['brands']);self.assertIn('1TB',cat['storage']);self.assertIn('Grey',cat['colours'])
    def test_missing_or_unknown_details_are_refused_with_plain_messages(self):
        good=dict(brand='Samsung',model='A54',storage='128GB',colour='Black')
        for key,text in (('brand','brand'),('model','model'),('storage','storage'),('colour','colour')):
            with self.assertRaisesMessage(ValidationError,text):catalog_entry.check({**good,key:''})
        with self.assertRaisesMessage(ValidationError,'storage'):catalog_entry.check({**good,'storage':'999GB'})


class Screens(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
    def single(self,**extra):
        data=dict(branch=self.branch,party_name='Typed Seller',brand='iPhone',model='12 Pro Max',storage='512GB',colour='Grey',quantity=1,price=Decimal('90000'),asking_price=Decimal('110000'),pta_status='PTA',device_type='Used',condition='Good',
                  warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=self.today,payment_status='full',method='Cash',imeis=[[imei(701)]],phone_notes='Battery changed, display replaced')
        data['variant']=catalog_entry.check(data);data.update(extra)
        if data['payment_status']=='full':fund(self.branch,self.user,data['price']*data['quantity'])      # money cannot leave an empty till (D8): the owner puts in what this purchase pays
        return easy_retail.purchase(data,self.user)
    def test_single_purchase_creates_the_variant_and_keeps_the_notes(self):
        bill=self.single();device=bill.reference.lines.get().device
        self.assertEqual((device.product.model.brand,device.product.model.name,device.product.storage,device.product.colour,device.pta_status),('iPhone','12 Pro Max','512GB','Grey','PTA'))
        self.assertEqual(device.details['condition_notes'],'Battery changed, display replaced')
        self.assertContains(self.client.get(reverse('device_history',args=[device.pk])),'Battery changed, display replaced')
    def test_second_purchase_reuses_the_variant(self):
        self.single();self.single(imeis=[[imei(702)]]);self.assertEqual(Product.objects.filter(model__name='12 Pro Max').count(),1)
    def test_screen_offers_typed_fields_not_a_forced_variant_list_and_validates_them(self):
        old=self.client.get(reverse('easy_entry',args=['purchase']));self.assertRedirects(old,reverse('bulk_purchase'))       # one purchase screen now
        post=self.client.post(reverse('easy_entry',args=['purchase']),dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-000000000001',party_name='X',quantity='1',price='1',asking_price='2',pta_status='PTA',device_type='Used',condition='Good',payment_status='unpaid',warehouse=str(self.warehouse.pk),staff=str(self.buyer.pk),posting_date=str(self.today)))
        self.assertContains(post,'Enter the brand');self.assertContains(post,'Enter the model')
    def test_screen_posts_typed_details_end_to_end(self):
        post=self.client.post(reverse('easy_entry',args=['purchase']),dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-000000000002',party_name='Walk In Seller',brand='Samsung',model='S23 Ultra',storage='256GB',colour='Green',quantity='1',price='80000',asking_price='95000',pta_status='PTA',device_type='Used',condition='Good',
            phone_notes='Back glass replaced',payment_status='unpaid',warehouse=str(self.warehouse.pk),staff=str(self.buyer.pk),posting_date=str(self.today),imeis=imei(703)))
        self.assertEqual(post.status_code,302);device=Device.objects.get(imeis__number=imei(703));self.assertEqual((device.product.name,device.details['condition_notes']),('Samsung S23 Ultra 256GB Green','Back glass replaced'))


class Bulk(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
    def rows(self,*rows):return json.dumps(list(rows))
    def row(self,**extra):
        r=dict(brand='Samsung',model='A54',storage='128GB',colour='Black',quantity=1,condition='Good',device_type='Used',grade='',pta_status='PTA',cost='40000',cost_mode='each',asking_price='50000',imeis='',notes='')
        r.update(extra);return r
    def form(self):return dict(branch=self.branch,party_name='Bulk Seller',warehouse=self.warehouse,posting_date=self.today,staff=self.buyer,payment_status='unpaid',method='Cash',handler=self.handler)
    def test_typed_rows_create_catalogue_records_and_per_phone_notes_in_the_right_order(self):
        rows=bulk.parse_rows(self.rows(self.row(imeis=imei(711),notes='Display changed'),self.row(brand='iPhone',model='13',storage='128GB',colour='Blue',imeis=imei(712),notes='Battery 100%')))
        bill=bulk.post(self.form(),rows,self.user)
        by_imei={d.imeis.get().number:d for d in Device.objects.all()}
        self.assertEqual((by_imei[imei(711)].product.model.name,by_imei[imei(711)].details['condition_notes']),('A54','Display changed'))
        self.assertEqual((by_imei[imei(712)].product.model.name,by_imei[imei(712)].details['condition_notes']),('13','Battery 100%'));self.assertEqual(bill.total,Decimal('80000.00'))
    def test_missing_details_are_refused_per_line(self):
        for key,text in (('brand','Line 1: Enter the phone brand'),('model','Line 1: Enter the phone model'),('storage','Line 1: Choose the storage'),('colour','Line 1: Enter the phone colour')):
            with self.assertRaisesMessage(ValidationError,text):bulk.parse_rows(self.rows(self.row(**{key:''})),require_imei=False)
    def test_page_renders_the_catalogue_lists(self):
        page=self.client.get(reverse('bulk_purchase'));self.assertContains(page,'phone-brands');self.assertContains(page,'phone_entry.');self.assertContains(page,'Add phone')
