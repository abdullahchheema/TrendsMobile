"""The unified POS: shop-only stock, no shift needed, customer phone, answer for printing and sharing, return/exchange entry points."""
import json
import uuid
from decimal import Decimal
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests_money import Base,D
from . import customer_names,shopdefaults,services as s


class PosBase(Base):
    def stock(self,cost=50000):
        bill=self.phone(cost);return bill.reference.lines.get().device
    def payload(self,device,name='Pos Customer',phone='0300 1234567',price='60000',payments=None,branch=None):
        branch=branch or self.branch;me=shopdefaults.person(self.a,branch)
        return dict(request_key=str(uuid.uuid4()),branch=str(branch.pk),customer_name=name,customer_phone=phone,seller=str(me.pk),handler=str(me.pk),
                    items=[dict(product=str(device.product_id),device=str(device.pk),warehouse=str(device.warehouse_id),quantity=1,rate=price,pta_status='PTA')],
                    adjustments=dict(discount_type='amount',discount_value='0',tax_rule='',tax_version=''),payments=payments if payments is not None else [dict(mode='Cash',amount=price)])
    def checkout(self,data):return self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json')


class Terminal(PosBase):
    def test_pos_is_a_two_panel_screen_with_cards_filters_customer_and_remaining_balance(self):
        self.stock();self.login(self.a);page=self.client.get(reverse('pos'));html=page.content.decode()
        for text in ('New Sale','pos-products','pos-order','Current order','Remaining balance','Total payable','pos-customer-phone','pos-brand','pos-model','pos-storage','pos-condition','Scan an IMEI'):self.assertIn(text,html,text)
        self.assertNotIn('Sold by',html);self.assertNotIn('Payment collected by',html)
    def test_only_this_shops_available_phones_are_listed_with_card_details(self):
        mine=self.stock();sold=self.stock()
        Device.objects.filter(pk=sold.pk).update(status='Sold');Device.objects.filter(pk=mine.pk).update(condition='Good')
        theirs=Device.objects.create(code='B-PHONE',product=self.product,warehouse=Warehouse.objects.get(branch=self.other),branch=self.other,status='Available',purchase_cost=1)
        held=self.stock();Device.objects.filter(pk=held.pk).update(status='Holding')
        self.login(self.a);items=self.client.get(reverse('pos')).context['items'];ids={i['device'] for i in items if i.get('serialized')}
        self.assertEqual(ids,{str(mine.pk)});item=next(i for i in items if i['device']==str(mine.pk));self.assertTrue(all(k in item for k in ('model','brand','storage','colour','condition','imeis','rate')))
    def test_unpriced_phones_show_a_zero_rate_so_the_cashier_types_the_price(self):
        device=self.stock();self.login(self.a);item=next(i for i in self.client.get(reverse('pos')).context['items'] if i['device']==str(device.pk));self.assertEqual(Decimal(item['rate']),0)
    def test_old_single_phone_sale_page_goes_to_the_pos(self):
        device=self.stock();self.login(self.a);page=self.client.get(reverse('easy_entry',args=['sale']),{'phone':str(device.pk)})
        self.assertEqual(page.status_code,302);self.assertIn(reverse('pos'),page.url);self.assertIn('phone='+str(device.pk),page.url)
        self.assertContains(self.client.get(page.url),'data-add-device="'+str(device.pk)+'"')


class Checkout(PosBase):
    def test_sale_needs_no_shift_saves_the_phone_and_returns_what_the_screen_needs(self):
        self.fund();device=self.stock();self.login(self.a)
        result=self.checkout(self.payload(device,payments=[dict(mode='Cash',amount='25000')]));self.assertEqual(result.status_code,200,result.content)
        body=result.json();self.assertEqual((body['total'],body['outstanding'],body['shop']),('60000.00','35000.00',self.branch.name));self.assertTrue(body['code']);self.assertIn('/receipt/',body['receipt'])
        sale=Document.objects.get(pk=body['id']);self.assertEqual(sale.kind,'Sales Invoice');device.refresh_from_db();self.assertEqual(device.status,'Sold')
        customer=sale.customer;self.assertEqual((customer.phone,customer.branch_id),('0300 1234567',self.branch.pk))
        self.assertEqual(money_total(sale),D('60000.00'))
        self.assertContains(self.client.get(body['receipt']),'<h1>'+self.branch.name+'</h1>')
    def test_a_credit_sale_with_no_payment_shows_the_full_balance_as_due(self):
        device=self.stock();self.login(self.a);body=self.checkout(self.payload(device,payments=[])).json();self.assertEqual(body['outstanding'],'60000.00')
        from . import money;self.assertEqual(money.customer_dues([self.branch])[0]['remaining'],D('60000.00'))
    def test_a_phone_that_is_not_available_or_belongs_to_another_shop_is_refused(self):
        device=self.stock();self.login(self.a);self.assertEqual(self.checkout(self.payload(device)).status_code,200)
        again=self.checkout(self.payload(device));self.assertEqual(again.status_code,400)
        other=Device.objects.create(code='OTHER-ONLY',product=self.product,warehouse=Warehouse.objects.get(branch=self.other),branch=self.other,status='Available',purchase_cost=1)
        self.assertEqual(self.checkout(self.payload(other)).status_code,404)
    def test_same_request_key_posts_once(self):
        self.fund();device=self.stock();self.login(self.a);data=self.payload(device);one=self.checkout(data);two=self.checkout(data)
        self.assertEqual(one.json()['id'],two.json()['id']);self.assertEqual(Document.objects.filter(kind='Sales Invoice',customer__name='Pos Customer').count(),1)
    def test_an_old_open_shift_must_be_closed_first(self):
        device=self.stock();person=shopdefaults.person(self.a,self.branch)
        old=s.new_doc('POS Opening Entry',self.branch,handler=person,till=shopdefaults.till(self.branch),posting_date=timezone.localdate()-timezone.timedelta(days=3),data={'opening_float':'0'})
        s.submit(old,self.a);self.login(self.a);self.assertContains(self.client.get(reverse('pos')),'old till shift')
        bad=self.checkout(self.payload(device));self.assertEqual(bad.status_code,400);self.assertIn('Close your earlier till shift',bad.json()['error'])
    def test_a_bad_phone_number_is_refused_with_a_message(self):
        device=self.stock();self.login(self.a);bad=self.checkout(self.payload(device,phone='abc'));self.assertEqual(bad.status_code,400);self.assertIn('phone',bad.json()['error'].lower())


class ReturnsAndExchange(PosBase):
    def test_sale_page_offers_return_refund_exchange_and_replacement(self):
        self.fund();device=self.stock();self.login(self.a);sale=Document.objects.get(pk=self.checkout(self.payload(device)).json()['id'])
        page=self.client.get(reverse('detail',args=[sale.pk]));self.assertContains(page,'Return, refund or exchange');self.assertContains(page,'Select every phone');self.assertContains(page,'Exchange for another phone');self.assertContains(page,'Sell a replacement')
        self.assertContains(page,'customer=Pos%20Customer');self.assertNotContains(page,'Receiving warehouse')
    def test_exchange_and_pos_open_with_the_customer_filled_in(self):
        self.login(self.user);page=self.client.get(reverse('exchange'),{'branch':str(self.branch.pk),'customer':'Pos Customer'});self.assertContains(page,'value="Pos Customer"')
        self.login(self.a);self.assertContains(self.client.get(reverse('pos'),{'customer':'Pos Customer'}),'value="Pos Customer"')
    def test_full_return_puts_the_phone_in_holding_and_clears_the_customer_balance(self):
        self.fund();device=self.stock();self.login(self.a);sale=Document.objects.get(pk=self.checkout(self.payload(device,payments=[])).json()['id'])
        from . import money,returns
        draft=returns.create(sale,self.a,[str(device.pk)],'Good','Changed mind',warehouse=shopdefaults.warehouse(self.branch));s.submit(draft,self.a);device.refresh_from_db();self.assertEqual(device.status,'Holding')
        self.assertEqual(money.customer_dues([self.branch]),[])


def money_total(sale):return sale.total
