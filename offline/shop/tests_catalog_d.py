"""Phase D: brand suggestions, condition grade on the device form, accessory low-stock alerts."""
from decimal import Decimal
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import services as s,dashboard_metrics as dm


class Catalog(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def test_model_and_item_forms_suggest_the_brand_list(self):
        for kind in ('Mobile Model','Item'):
            page=self.client.get(reverse('master_new',args=[kind]));self.assertContains(page,'id="brand-list"');self.assertContains(page,'Infinix');self.assertContains(page,'list="brand-list"')
    def test_any_brand_can_still_be_typed_manually(self):
        self.client.post(reverse('master_new',args=['Mobile Model']),dict(name='Spark 20',brand='Brand New Maker',description=''))
        self.assertEqual(PhoneModel.objects.get(name='Spark 20').brand,'Brand New Maker');self.assertTrue(Brand.objects.filter(name='Brand New Maker').exists())
    def test_device_form_has_no_grade_and_accepts_the_jv_pta_status(self):
        device=Device.objects.create(code='GRD',branch=self.branch,product=self.product,warehouse=self.warehouse)
        page=self.client.get(reverse('device',args=[device.pk]));self.assertNotContains(page,'name="grade"')      # the grade is no longer asked for
        self.client.post(reverse('device',args=[device.pk]),dict(device_type='Used',pta_status='JV',condition='Good',inspection_status='Pending',battery='0',asking_price='1000',minimum_price='0',wholesale_price='0',warranty_days='0'))
        device.refresh_from_db();self.assertEqual(device.pta_status,'JV')


class LowStock(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
        self.cable=Product.objects.create(code='CABLE',name='USB cable',stock_item=True,serialized=False,reorder_level=Decimal('5'))
        self.plain=Product.objects.create(code='STRAP',name='Wrist strap',stock_item=True,serialized=False)         # no reorder level: never alerts
    def receive(self,product,qty):
        doc=s.new_doc('Purchase Receipt',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer)
        Line.objects.create(document=doc,product=product,warehouse=self.warehouse,quantity=qty,rate=Decimal('100'));return s.submit(doc,self.user)
    def test_items_at_or_below_their_reorder_level_are_listed_with_their_quantity(self):
        self.receive(self.cable,5);self.receive(self.plain,1)
        rows=dm.low_stock(self.branch);self.assertEqual([(r['product'].code,r['quantity']) for r in rows],[('CABLE',Decimal('5.000'))])
    def test_zero_stock_still_alerts_and_plenty_does_not(self):
        self.assertEqual([r['product'].code for r in dm.low_stock(self.branch)],['CABLE'])        # nothing received yet
        self.receive(self.cable,20);self.assertEqual(dm.low_stock(self.branch),[])
    def test_dashboard_shows_the_running_low_panel(self):
        self.receive(self.cable,2);page=self.client.get(reverse('dashboard'));self.assertContains(page,'RUNNING LOW');self.assertContains(page,'USB cable')
    def test_stock_is_counted_per_shop(self):
        from . import setup
        other=setup.create_shop(dict(code='LOW2',name='Second shop',company='X',warehouse_name='Second stock',till_name='T2',payment_methods=['Cash']),self.user);place=Warehouse.objects.get(branch=other)
        doc=s.new_doc('Purchase Receipt',other,supplier=self.supplier,warehouse=place,buyer=Staff.objects.create(code='B2',name='Buyer 2',branch=other,can_buy=True))
        Line.objects.create(document=doc,product=self.cable,warehouse=place,quantity=50,rate=Decimal('100'));s.submit(doc,self.user)
        self.assertEqual([r['product'].code for r in dm.low_stock(self.branch)],['CABLE'])                # the other shop's 50 do not hide this shop's gap
