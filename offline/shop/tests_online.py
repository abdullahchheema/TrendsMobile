"""Online store: product photos, online price, publishing rules, and the read-only views the website reads."""
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
from . import bulk

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
        rows=[dict(brand='Apple',model='iPhone 13',storage='128GB',colour='Blue',quantity=1,condition='Good',device_type='Used',grade='8',pta_status='PTA',cost='100000',cost_mode='each',asking_price='120000',imeis=imei(6000+i),notes='') for i in range(3)]
        data=dict(branch=self.branch,party_name='S',warehouse=self.warehouse,posting_date=self.today,staff=self.buyer,payment_status='unpaid',method='Cash',handler=self.handler)
        bulk.post(data,bulk.parse_rows(json.dumps(rows)),self.user)
        self.product=Device.objects.filter(imeis__number=imei(6000)).first().product
    def upload(self,raw=PNG,name='a.png',product=None):
        return self.client.post(reverse('online_photo_upload',args=[(product or self.product).pk]),{'file':SimpleUploadedFile(name,raw,content_type='image/png')})
    def rows(self,view):
        with connection.cursor() as c:
            c.execute(f'SELECT * FROM {view}');cols=[d[0] for d in c.description];return [dict(zip(cols,r)) for r in c.fetchall()]


class Photos(OnlineBase):
    def test_upload_reorder_cover_and_delete(self):
        a=self.upload().json();b=self.upload(JPG,'b.jpg').json()
        self.assertEqual(list(ProductImage.objects.filter(product=self.product).values_list('position',flat=True)),[0,1])
        order=lambda ids:self.client.post(reverse('online_photo_order',args=[self.product.pk]),json.dumps({'order':ids}),content_type='application/json')
        self.assertEqual(order([b['id'],a['id']]).status_code,200);self.assertEqual(ProductImage.objects.filter(product=self.product).first().pk.hex,b['id'].replace('-',''))
        self.assertEqual(order([a['id']]).status_code,409)               # an incomplete list is refused
        self.client.post(reverse('online_photo_delete',args=[b['id']]));self.assertEqual(list(ProductImage.objects.values_list('position',flat=True)),[0])
        self.assertEqual(self.client.get(a['url']).status_code,200)       # the stored file is served
    def test_only_real_photos_are_accepted(self):
        bad=self.upload(b'<?php echo 1;?>','evil.png');self.assertEqual(bad.status_code,400);self.assertIn('JPG, PNG or WebP',bad.json()['error'])
        big=self.upload(PNG+b'0'*(5*1024*1024));self.assertEqual(big.status_code,400);self.assertIn('at most 5 MB',big.json()['error'])
        self.assertFalse(ProductImage.objects.exists())
    def test_a_product_has_at_most_twelve_photos(self):
        for _ in range(12):self.assertEqual(self.upload().status_code,200)
        self.assertEqual(self.upload().status_code,400)
    def test_shop_logins_cannot_use_the_online_store(self):
        self.client.force_login(self.a) if hasattr(self,'a') else None
        if hasattr(self,'a'):
            self.assertEqual(self.client.get(reverse('online_store')).status_code,403);self.assertEqual(self.upload().status_code,403)


class Publishing(OnlineBase):
    def save(self,**data):
        base=dict(description='Clean phone',online_price='');base.update(data)
        return self.client.post(reverse('online_edit',args=[self.product.pk]),base,follow=True)
    def test_publishing_needs_a_photo_and_a_price(self):
        r=self.save(online_visible='on');self.assertContains(r,'add at least one photo');self.product.refresh_from_db();self.assertFalse(self.product.online_visible)
        self.upload();r=self.save(online_visible='on');self.assertContains(r,'live on the website')
        self.product.refresh_from_db();self.assertTrue(self.product.online_visible);self.assertEqual(self.product.slug,'apple-iphone-13-128gb-blue' if self.product.slug.startswith('apple') else self.product.slug)
    def test_online_price_must_be_positive_and_deleting_the_last_photo_unpublishes(self):
        self.assertContains(self.save(online_price='-5'),'more than zero')
        self.upload();self.save(online_visible='on',online_price='125000');self.product.refresh_from_db();self.assertEqual(self.product.online_price,Decimal('125000'))
        image=ProductImage.objects.get();self.assertTrue(self.client.post(reverse('online_photo_delete',args=[image.pk])).json()['unpublished'])
        self.product.refresh_from_db();self.assertFalse(self.product.online_visible)
    def test_store_screens_load(self):
        self.assertContains(self.client.get(reverse('online_store')),'Needs a photo');self.assertContains(self.client.get(reverse('online_edit',args=[self.product.pk])),'Drop photos here')


class WebsiteViews(OnlineBase):
    def publish(self,price=''):
        self.upload();self.client.post(reverse('online_edit',args=[self.product.pk]),dict(description='x',online_price=price,online_visible='on'))
    def test_nothing_is_visible_until_published(self):
        self.assertEqual(self.rows('web_listings'),[]);self.assertEqual(self.rows('web_stock'),[])
    def test_listing_stock_units_and_images(self):
        self.publish('125000')
        [listing]=self.rows('web_listings');self.assertEqual((listing['stock_total'],float(listing['price_from']),float(listing['price_to'])),(3,125000.0,125000.0))
        self.assertTrue(listing['cover_url']);self.assertEqual((listing['brand'],listing['storage'],listing['colour']),('Apple','128GB','Blue'))
        [stock]=self.rows('web_stock');self.assertEqual((stock['shop_name'],stock['quantity']),(self.branch.name,3))
        units=self.rows('web_phone_units');self.assertEqual(len(units),3);self.assertEqual({float(u['price']) for u in units},{125000.0})
        self.assertEqual(len(self.rows('web_images')),1)
    def test_without_an_online_price_the_shop_price_shows_and_a_single_phone_can_differ(self):
        self.publish('')
        d=Device.objects.filter(product=self.product).first();d.online_price=Decimal('99000');d.save()
        [listing]=self.rows('web_listings');self.assertEqual((float(listing['price_from']),float(listing['price_to'])),(99000.0,120000.0))
    def test_sold_phones_leave_the_website_stock(self):
        self.publish('');d=Device.objects.filter(product=self.product).first();d.status='Sold';d.save()
        self.assertEqual(self.rows('web_stock')[0]['quantity'],2);self.assertEqual(self.rows('web_listings')[0]['stock_total'],2)
    def test_views_never_expose_cost_imei_or_customers(self):
        self.publish('')
        for view in ('web_listings','web_stock','web_phone_units','web_images'):
            cols=' '.join(self.rows(view)[0]).lower() if self.rows(view) else ''
            for secret in ('cost','imei','supplier','customer','purchase','minimum','wholesale'):self.assertNotIn(secret,cols,view)


class Dashboard(OnlineBase):
    def test_dashboard_counts_live_stock_value_and_hidden_stock(self):
        page=self.client.get(reverse('online_dashboard'));self.assertContains(page,'not on the website yet');self.assertEqual(page.context['totals']['units'],0);self.assertEqual(page.context['hidden_total'],3)
        self.upload();self.client.post(reverse('online_edit',args=[self.product.pk]),dict(description='x',online_price='125000',online_visible='on'))
        page=self.client.get(reverse('online_dashboard'));t=page.context['totals']
        self.assertEqual((page.context['live'],t['units'],t['value'],t['cost'],t['margin']),(1,3,Decimal('375000'),Decimal('300000'),Decimal('75000')))
        self.assertEqual(page.context['shops'][0]['units'],3);self.assertEqual(page.context['hidden_total'],0);self.assertContains(page,'Online stock by shop')
    def test_a_phone_listed_below_cost_is_flagged(self):
        self.upload();self.client.post(reverse('online_edit',args=[self.product.pk]),dict(description='x',online_price='90000',online_visible='on'))
        self.assertEqual(len(self.client.get(reverse('online_dashboard')).context['below_cost']),3)


class MandatoryPrice(TestCase):
    def test_an_item_cannot_be_saved_without_a_selling_price(self):
        p=Product(code='X1',name='Cover',stock_item=True,serialized=False,selling_price=Decimal('0'))
        with self.assertRaisesMessage(ValidationError,'Enter the selling price'):p.clean()
        p.selling_price=Decimal('500');p.clean()
    def test_online_price_cannot_be_zero(self):
        p=Product(code='X2',name='Cover',stock_item=True,serialized=False,selling_price=Decimal('500'),online_price=Decimal('0'))
        with self.assertRaisesMessage(ValidationError,'online price'):p.clean()
