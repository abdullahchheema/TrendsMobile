"""Phase D: IMEI lookup, device history and the Holding (inspection) gate."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import easy_retail,returns,services as s


class LookupBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate()
    def stock(self,number,cost=50000):
        data=dict(branch=self.branch,party_name='History Supplier',product=self.product,quantity=1,price=cost,asking_price=80000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,
                  posting_date=self.today,payment_status='full',method='Cash',imeis=[[imei(number)]])
        bill=easy_retail.purchase(data,self.user);return bill,bill.reference.lines.get().device
    def sell(self,device,price=70000):
        return easy_retail.sale(dict(branch=self.branch,party_name='History Buyer',phone=device,price=price,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today,payment_status='full',method='Cash'),self.user)


class Lookup(LookupBase):
    def test_exact_imei_opens_the_phone_and_partial_lists_matches(self):
        _,device=self.stock(201)
        self.assertRedirects(self.client.get(reverse('imei_lookup'),{'q':imei(201)}),reverse('device_history',args=[device.pk]),fetch_redirect_response=False)
        page=self.client.get(reverse('imei_lookup'),{'q':imei(201)[:8]});self.assertContains(page,device.code)
        self.assertContains(self.client.get(reverse('imei_lookup'),{'q':'0000000'}),'No phone matches')
    def test_history_shows_purchase_then_sale_with_parties_and_amounts(self):
        bill,device=self.stock(202,cost=50000);self.sell(device,70000)
        page=self.client.get(reverse('device_history',args=[device.pk]));labels=[e['label'] for e in page.context['events']]
        self.assertEqual(labels,['Received into stock','Sold']);self.assertContains(page,'History Supplier');self.assertContains(page,'History Buyer')
        self.assertEqual(page.context['events'][0]['amount'],Decimal('50000.00'));self.assertContains(page,imei(202))
    def test_other_shops_phones_are_not_visible(self):
        _,device=self.stock(204);other=Branch.objects.create(code='HOTH',name='Other',company='X')
        user=get_user_model().objects.create_user('mgr-hist',password='Unrelated-test-password-729!');Staff.objects.create(code='MGR-H',name='Mgr H',branch=other,user=user,role=Role.objects.get(code='manager'))
        self.client.force_login(user);self.assertEqual(self.client.get(reverse('device_history',args=[device.pk])).status_code,404)
        self.assertNotContains(self.client.get(reverse('imei_lookup'),{'q':imei(204)},follow=True),device.code)
    def test_search_palette_links_phones_to_their_history(self):
        _,device=self.stock(205);hit=self.client.get(reverse('search'),{'q':imei(205)[:10]}).json()['results'][0]
        self.assertEqual(hit['href'],reverse('device_history',args=[device.pk]))


class HoldingGate(LookupBase):
    def test_a_returned_phone_waits_in_holding_and_cannot_be_sold_until_accepted(self):
        bill,device=self.stock(206);sale=self.sell(device,70000)
        credit=s.submit(s.return_sale(sale,self.user,[str(device.pk)],'Good','Changed mind',{},self.warehouse),self.user)
        device.refresh_from_db();self.assertEqual((device.status,device.inspection_status),('Holding','Pending'))
        self.assertEqual(s.on_hand(self.product,self.warehouse)[0],Decimal(1))                       # still owned stock, valued
        with self.assertRaises(Exception):self.sell(device,70000)
        self.assertEqual([e['label'] for e in self.client.get(reverse('device_history',args=[device.pk])).context['events']],['Received into stock','Sold','Sales return'])
        self.client.post(reverse('accept_holding',args=[device.pk]));device.refresh_from_db();self.assertEqual(device.status,'Available')
    def test_accept_moves_holding_to_available_and_is_audited(self):
        _,device=self.stock(207);Device.objects.filter(pk=device.pk).update(status='Holding');device.refresh_from_db()
        with self.assertRaises(Exception):easy_retail.sale(dict(branch=self.branch,party_name='Too Soon',phone=device,price=70000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today,payment_status='full',method='Cash'),self.user)
        self.client.post(reverse('accept_holding',args=[device.pk]));device.refresh_from_db()
        self.assertEqual((device.status,device.inspection_status),('Available','Passed'));self.assertTrue(Audit.objects.filter(action='Accept phone into stock').exists())
        self.assertTrue(self.sell(device,70000).pk)
class BrandCatalog(TestCase):
    def test_standard_brands_are_seeded_and_grade_is_validated(self):
        names=set(Brand.objects.values_list('name',flat=True))
        self.assertTrue({'Apple','Samsung','Xiaomi','Infinix','Tecno','Realme','Vivo','Oppo','OnePlus'}<=names)
        from django.core.exceptions import ValidationError
        fx=fixtures();device=Device(code='GRADE-1',branch=fx['branch'],product=fx['product'],warehouse=fx['warehouse'],grade=11)
        with self.assertRaisesMessage(ValidationError,'between 1 and 10'):device.full_clean()
