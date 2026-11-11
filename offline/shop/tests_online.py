"""Online catalogue: photos (page-wide and per variant), live-when-it-has-a-photo, prices and discounts, and the read-only views the website reads."""
import json,shutil,tempfile
from decimal import Decimal
from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import bulk,online_store

PNG=b'\x89PNG\r\n\x1a\n'+b'\x00'*64
JPG=b'\xff\xd8\xff\xe0'+b'\x00'*64
TMP=tempfile.mkdtemp()


@override_settings(PRODUCT_IMAGE_DIR=TMP)
class OnlineBase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass();shutil.rmtree(TMP,ignore_errors=True)
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
        self.make('Blue',6000);self.make('Red',6100)
        self.product=Device.objects.filter(imeis__number=imei(6000)).first().product;self.red=Device.objects.filter(imeis__number=imei(6100)).first().product
    def make(self,colour,base,n=3):
        rows=[dict(brand='Apple',model='iPhone 13',storage='128GB',colour=colour,quantity=1,condition='Good',device_type='Used',grade='8',pta_status='PTA',cost='100000',cost_mode='each',asking_price='120000',imeis=imei(base+i),notes='') for i in range(n)]
        data=dict(branch=self.branch,party_name='S',warehouse=self.warehouse,posting_date=self.today,staff=self.buyer,payment_status='unpaid',method='Cash',handler=self.handler)
        bulk.post(data,bulk.parse_rows(json.dumps(rows)),self.user)
    def upload(self,raw=PNG,name='a.png',product=None,**extra):
        return self.client.post(reverse('online_photo_upload',args=[(product or self.product).pk]),{'file':SimpleUploadedFile(name,raw,content_type='image/png'),**extra})
    def rows(self,view):
        with connection.cursor() as c:
            c.execute(f'SELECT * FROM {view}');cols=[d[0] for d in c.description];return [dict(zip(cols,r)) for r in c.fetchall()]
    def live(self,*products):
        return [Product.objects.get(pk=p.pk).online_visible for p in products]


class Photos(OnlineBase):
    def test_upload_reorder_cover_and_delete(self):
        a=self.upload().json();b=self.upload(JPG,'b.jpg').json()
        self.assertEqual(sorted(ProductImage.objects.values_list('position',flat=True)),[0,1])
        order=lambda ids:self.client.post(reverse('online_photo_order',args=[self.product.pk]),json.dumps({'order':ids}),content_type='application/json')
        self.assertEqual(order([b['id'],a['id']]).status_code,200);self.assertEqual(ProductImage.objects.order_by('position').first().pk.hex,b['id'].replace('-',''))
        self.assertEqual(order([a['id']]).status_code,409)
        self.client.post(reverse('online_photo_delete',args=[b['id']]));self.assertEqual(list(ProductImage.objects.values_list('position',flat=True)),[0])
        self.assertEqual(self.client.get(a['url']).status_code,200)
    def test_only_real_photos_are_accepted(self):
        bad=self.upload(b'<?php echo 1;?>','evil.png');self.assertEqual(bad.status_code,400);self.assertIn('JPG, PNG or WebP',bad.json()['error'])
        big=self.upload(PNG+b'0'*(5*1024*1024));self.assertEqual(big.status_code,400);self.assertIn('at most 5 MB',big.json()['error'])
        self.assertFalse(ProductImage.objects.exists())
    def test_a_page_has_at_most_twelve_shared_photos(self):
        for _ in range(12):self.assertEqual(self.upload().status_code,200)
        self.assertEqual(self.upload().status_code,400)
    def test_a_photo_can_belong_to_one_variant_only(self):
        r=self.upload(variant=str(self.red.pk));self.assertEqual(r.status_code,200)
        img=ProductImage.objects.get();self.assertEqual((img.product_id,img.variant_only),(self.red.pk,True))
        self.assertEqual(self.upload(variant='00000000-0000-4000-8000-000000000000').status_code,404)
    def test_only_the_owner_and_the_online_login_can_use_it(self):
        from . import shop_accounts
        shop=shop_accounts.create_login(self.branch,'shop-z','Strong-pass-for-SZ-77!');self.client.force_login(shop)
        self.assertEqual(self.client.get(reverse('online_store')).status_code,403);self.assertEqual(self.upload().status_code,403)


class GoesLive(OnlineBase):
    def test_a_page_is_a_draft_until_it_has_a_photo_and_then_every_variant_is_live(self):
        self.assertEqual(self.live(self.product,self.red),[False,False])
        self.upload();self.assertEqual(self.live(self.product,self.red),[True,True])           # one photo on the page makes every colour live
        self.assertTrue(Product.objects.get(pk=self.red.pk).slug);self.assertEqual(Product.objects.get(pk=self.red.pk).family_slug,Product.objects.get(pk=self.product.pk).family_slug)
    def test_removing_the_last_photo_makes_it_a_draft_again(self):
        i=self.upload().json();r=self.client.post(reverse('online_photo_delete',args=[i['id']])).json();self.assertTrue(r['unpublished']);self.assertEqual(self.live(self.product,self.red),[False,False])
    def test_hiding_overrides_photos_and_a_new_colour_added_later_goes_live_by_itself(self):
        self.upload();self.client.post(reverse('online_edit',args=[self.product.pk]),dict(description='x',online_hidden='on'));self.assertEqual(self.live(self.product,self.red),[False,False])
        self.client.post(reverse('online_edit',args=[self.product.pk]),dict(description='x'));self.assertEqual(self.live(self.product,self.red),[True,True])
        self.make('Green',6200);green=Device.objects.filter(imeis__number=imei(6200)).first().product;self.assertTrue(Product.objects.get(pk=green.pk).online_visible)
    def test_store_screens_group_by_brand_and_model_once(self):
        page=self.client.get(reverse('online_store'));self.assertContains(page,'Apple');self.assertEqual(page.content.decode().count('iPhone 13</strong>'),1)
        self.assertContains(self.client.get(reverse('online_edit',args=[self.product.pk])),'Drop photos here')


class Prices(OnlineBase):
    def save(self,**data):
        base={f'online_price_{self.product.pk}':'',f'discount_{self.product.pk}':'',f'compare_{self.product.pk}':'',f'online_price_{self.red.pk}':'',f'discount_{self.red.pk}':'',f'compare_{self.red.pk}':'',description:'x'} if False else {}
        base.update(description='Clean phone');base.update(data);return self.client.post(reverse('online_edit',args=[self.product.pk]),base,follow=True)
    def test_percentage_off_for_the_whole_page_and_a_compare_price(self):
        self.upload();self.save(discount_all='10')
        for p in (self.product,self.red):self.assertEqual(Product.objects.get(pk=p.pk).discount_percent,Decimal('10.00'))
        v=self.rows('web_variants');self.assertEqual({(float(r['price']),float(r['compare_price'])) for r in v},{(108000.0,120000.0)})
        self.save(**{f'compare_{self.product.pk}':'150000'});[blue]=[r for r in self.rows('web_variants') if r['product_id']==self.product.pk.hex or str(r['product_id']).replace('-','')==self.product.pk.hex];self.assertEqual(float(blue['compare_price']),150000.0)
    def test_bad_discounts_and_compare_prices_are_refused(self):
        self.assertContains(self.save(discount_all='95'),'between 0 and 90');self.assertContains(self.save(**{f'online_price_{self.product.pk}':'-5'}),'more than zero')
        self.assertContains(self.save(**{f'compare_{self.product.pk}':'100000'}),'must be higher than the selling price')
        self.assertEqual(Product.objects.get(pk=self.product.pk).compare_price,None)           # nothing was saved
    def test_description_is_shared_by_every_variant_on_the_page(self):
        self.save(description='Shared text');self.assertEqual(Product.objects.get(pk=self.red.pk).description,'Shared text')


class WebsiteViews(OnlineBase):
    def test_nothing_is_visible_until_there_is_a_photo(self):
        self.assertEqual(self.rows('web_listings'),[]);self.assertEqual(self.rows('web_stock'),[])
    def test_families_variants_stock_by_shop_and_images(self):
        self.upload()
        fam=self.rows('web_families');self.assertEqual(len(fam),1);self.assertEqual((fam[0]['title'],fam[0]['stock_total'],fam[0]['variant_count']),('Apple iPhone 13',6,3))
        self.assertTrue(fam[0]['cover_url']);self.assertEqual(len(self.rows('web_listings')),3)
        v=self.rows('web_variants');self.assertEqual(sorted(r['quantity'] for r in v),[3,3]);stock=self.rows('web_variant_stock');self.assertEqual({(r['shop_name'],r['quantity']) for r in stock},{(self.branch.name,3)})
        self.assertEqual(len(self.rows('web_images')),1);self.assertEqual(len(self.rows('web_phone_units')),6)
    def test_a_sold_phone_leaves_the_website_stock_at_once(self):
        self.upload();d=Device.objects.filter(product=self.product).first();d.status='Sold';d.save()
        self.assertEqual(sum(r['quantity'] for r in self.rows('web_stock')),5)
    def test_views_never_expose_cost_imei_or_customers(self):
        self.upload()
        for view in ('web_listings','web_families','web_variants','web_variant_stock','web_stock','web_phone_units','web_images'):
            cols=' '.join(self.rows(view)[0]).lower() if self.rows(view) else ''
            for secret in ('cost','imei','supplier','customer','purchase','minimum','wholesale'):self.assertNotIn(secret,cols,view)


class Dashboard(OnlineBase):
    def test_dashboard_counts_live_stock_value_and_hidden_stock(self):
        page=self.client.get(reverse('online_dashboard'));self.assertContains(page,'not on the website yet');self.assertEqual(page.context['totals']['units'],0);self.assertEqual(page.context['hidden_total'],6)
        self.upload();self.client.post(reverse('online_edit',args=[self.product.pk]),dict(description='x',**{f'online_price_{self.product.pk}':'125000',f'online_price_{self.red.pk}':'125000'}))
        page=self.client.get(reverse('online_dashboard'));t=page.context['totals']
        self.assertEqual((page.context['live'],t['units'],t['value'],t['cost'],t['margin']),(3,6,Decimal('750000'),Decimal('600000'),Decimal('150000')));self.assertEqual(page.context['hidden_total'],0)
    def test_a_phone_listed_below_cost_is_flagged(self):
        self.upload();self.client.post(reverse('online_edit',args=[self.product.pk]),dict(description='x',discount_all='30'))
        self.assertEqual(len(self.client.get(reverse('online_dashboard')).context['below_cost']),6)


class MandatoryPrice(TestCase):
    def test_an_item_cannot_be_saved_without_a_selling_price(self):
        p=Product(code='X1',name='Cover',stock_item=True,serialized=False,selling_price=Decimal('0'))
        with self.assertRaisesMessage(ValidationError,'Enter the selling price'):p.clean()
        p.selling_price=Decimal('500');p.clean()
    def test_online_price_cannot_be_zero(self):
        p=Product(code='X2',name='Cover',stock_item=True,serialized=False,selling_price=Decimal('500'),online_price=Decimal('0'))
        with self.assertRaisesMessage(ValidationError,'online price'):p.clean()
