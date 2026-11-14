"""Online order lifecycle, stock visibility and safety scenarios: stages and history, tracking, history list, returns without stock moves,
out-of-stock pages that stay visible, shop stock totals, the consistency check, and a real two-thread race for the last phone."""
import threading
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection,connections
from django.test import TransactionTestCase
from django.urls import reverse
from .models import *
from .tests_online_orders import OrderBase
from . import online_check,online_orders as oo,shopdefaults,shop_accounts,services as s


def web(sql,*params):
    with connection.cursor() as c:
        c.execute(sql,params);return c.fetchall()


class Views(OrderBase):
    """What the website sees (the read-only views), checked on the real database."""
    def qty(self,product):return web('select coalesce(sum(quantity),0) from web_variants where product_id=%s',product.pk)[0][0]
    def stock_total(self,product):return web('select stock_total from web_listings where product_id=%s',product.pk)
    def sell_in_shop(self,device,branch):
        doc=s.new_doc('Sales Invoice',branch,customer=Party.objects.create(code=f'W{device.code}',name=f'Walk in {device.code}',kind='Customer',branch=branch),seller=shopdefaults.shop_person(branch))
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=device.warehouse,quantity=1,rate=device.asking_price);return s.submit(doc,self.user)
    def test_two_shops_add_up_and_a_physical_sale_lowers_the_website_number(self):
        for _ in range(5):self.phone(self.branch)
        three=[self.phone(self.other) for _ in range(3)]
        self.assertEqual(self.qty(self.red),8)
        a=Device.objects.filter(branch=self.branch,status='Available').first();self.sell_in_shop(a,self.branch);self.assertEqual(self.qty(self.red),7)
        for d in Device.objects.filter(branch=self.branch,status='Available'):self.sell_in_shop(d,self.branch)
        self.assertEqual(self.qty(self.red),3)                                       # the other shop still sells it
        for d in three:self.sell_in_shop(d,self.other)
        self.assertEqual(self.qty(self.red),0)
    def test_a_sold_out_page_stays_listed_and_comes_back_when_restocked(self):
        d=self.phone(self.branch);self.assertEqual(self.stock_total(self.red),[(1,)]);self.sell_in_shop(d,self.branch)
        self.assertEqual(self.qty(self.red),0);self.assertEqual(self.stock_total(self.red),[(0,)])     # the product row stays, with zero stock
        self.assertEqual(web('select count(*) from web_families where family_slug=%s',self.red.family_slug)[0][0],1)
        self.assertEqual(web('select count(*) from web_images where product_id=%s',self.red.pk)[0][0],1)   # photos and page survive
        self.phone(self.other);self.assertEqual(self.qty(self.red),1)
    def test_one_variant_going_out_does_not_make_another_look_available(self):
        self.phone(self.branch,self.red);d=self.phone(self.branch,self.blue);Product.objects.filter(pk=self.blue.pk).update(online_visible=True,slug='blue')
        self.assertEqual((self.qty(self.red),self.qty(self.blue)),(1,1));self.sell_in_shop(d,self.branch);self.assertEqual((self.qty(self.red),self.qty(self.blue)),(1,0))
    def test_a_reserved_phone_leaves_the_website_and_returns_when_cancelled(self):
        self.phone(self.branch);o=self.order([self.item()]);self.assertEqual(self.qty(self.red),0);oo.cancel(o,self.user,'test');self.assertEqual(self.qty(self.red),1)
    def test_a_pending_transfer_holds_the_phone_and_a_reserved_phone_cannot_be_transferred(self):
        from . import transfers as tr
        a=self.phone(self.branch);b=self.phone(self.branch);o=self.order([self.item()])
        held=Device.objects.get(pk=a.pk) if Device.objects.get(pk=a.pk).status=='Reserved' else Device.objects.get(pk=b.pk)
        free=b if held.pk==a.pk else a
        with self.assertRaises(ValidationError):tr.create(self.user,self.branch,self.other,[held])
        doc=tr.create(self.user,self.branch,self.other,[free]);free.refresh_from_db();self.assertEqual(free.status,'Reserved');self.assertEqual(self.qty(self.red),0)
        with self.assertRaises(ValidationError):self.order([self.item()])                      # the transferring phone cannot be promised to a customer


class Lifecycle(OrderBase):
    def setUp(self):
        super().setUp();self.web=shop_accounts.create_online_login('Web Team','web-team','Strong-pass-for-WT-77!')
    def kinds(self,o):return [e.kind for e in o.events.all()]
    def test_an_order_is_confirmed_then_fulfilment_moves_and_every_step_is_recorded(self):
        self.phone(self.branch);o=self.order([self.item()]);self.assertEqual((oo.order_status(o),oo.display_stage(o)),('Confirmed','Not dispatched'))
        with self.assertRaises(ValidationError):oo.set_stage(o,self.user,'Processing')                   # no intermediate order statuses
        oo.dispatch(o,self.user,'TCS','T-1');o.refresh_from_db();self.assertEqual((o.status,oo.order_status(o),oo.display_stage(o)),('Dispatched','Confirmed','Dispatched'))
        oo.set_stage(o,self.user,'In Transit');oo.deliver(o,self.user);o.refresh_from_db();self.assertEqual((oo.order_status(o),oo.display_stage(o)),('Fulfilled','Delivered'))
        self.assertEqual(self.kinds(o),['Order placed','Confirmed by phone','Dispatched','Fulfilment','Delivered'])
        with self.assertRaises(ValidationError):oo.set_stage(o,self.user,'In Transit')
        with self.assertRaises(ValidationError):oo.update_shipment(o,self.user,'DHL','X')       # a delivered order is closed
    def test_a_prepaid_order_is_confirmed_at_once_but_cannot_ship_until_paid(self):
        self.phone(self.branch);o=self.order([self.item()]);OnlineOrder.objects.filter(pk=o.pk).update(payment_method='Easypaisa',payment_status='Unpaid');o.refresh_from_db()
        self.assertEqual(oo.order_status(o),'Confirmed')
        with self.assertRaises(ValidationError):oo.dispatch(o,self.user,'TCS','T')
        oo.mark_paid(o,self.user,'TX1');oo.dispatch(o,self.user,'TCS','T');o.refresh_from_db();self.assertEqual(o.status,'Dispatched')
    def test_tracking_is_required_saved_edited_and_survives_a_reload(self):
        d=self.phone(self.branch);o=self.order([self.item()]);self.client.force_login(self.web);url=reverse('online_order',args=[o.pk])
        self.client.post(url,dict(action='dispatch',courier='',tracking=''),follow=True);o.refresh_from_db();self.assertEqual(o.status,'New')       # no courier and tracking: refused
        r=self.client.post(url,dict(action='dispatch',courier='TCS',tracking='TRK-100',notes='Fragile'),follow=True);self.assertEqual(r.status_code,200)
        o.refresh_from_db();self.assertEqual((o.status,o.courier,o.tracking,o.dispatch_notes),('Dispatched','TCS','TRK-100','Fragile'))
        self.assertContains(self.client.get(url),'TRK-100')                                                  # persists after refresh
        self.client.post(url,dict(action='shipment',courier='Leopards',tracking='LEO-9',notes='Re-booked'),follow=True);o.refresh_from_db();self.assertEqual((o.courier,o.tracking),('Leopards','LEO-9'))
        self.client.post(url,dict(action='stage',stage='In Transit'),follow=True);o.refresh_from_db();self.assertEqual(oo.display_stage(o),'In Transit')
        self.client.post(url,dict(action='deliver',method='Cash'),follow=True);o.refresh_from_db();self.assertEqual(o.status,'Delivered')
        self.client.post(url,dict(action='shipment',courier='X',tracking='Y'),follow=True);o.refresh_from_db();self.assertEqual(o.tracking,'LEO-9')    # closed: unchanged
        self.assertTrue(any(e.kind=='Shipment' for e in o.events.all()))
    def test_shop_logins_cannot_touch_online_orders(self):
        shop=shop_accounts.create_login(self.branch,'shop-a','Strong-pass-for-SA-77!');self.phone(self.branch);o=self.order([self.item()]);self.client.force_login(shop)
        self.assertEqual(self.client.post(reverse('online_order',args=[o.pk]),dict(action='dispatch',courier='A',tracking='B')).status_code,403);o.refresh_from_db();self.assertEqual(o.status,'New')
    def test_delivered_orders_move_to_history_and_keep_their_original_price_and_details(self):
        self.phone(self.branch);o=self.order([self.item()]);oo.dispatch(o,self.user,'TCS','T-7');oo.deliver(o,self.user);self.client.force_login(self.web)
        self.assertNotContains(self.client.get(reverse('online_orders')),o.code)
        self.assertContains(self.client.get(reverse('online_orders')+'?status=history'),o.code);self.assertContains(self.client.get(reverse('online_orders')+'?status=all'),o.code)
        Product.objects.filter(pk=self.red.pk).update(online_price=Decimal('1'),enabled=False)                  # change the price, then archive the product
        page=self.client.get(reverse('online_order',args=[o.pk]));self.assertEqual(page.status_code,200);self.assertContains(page,'T-7');self.assertContains(page,'Ali Khan')
        o.refresh_from_db();self.assertEqual((o.lines.get().unit_price,o.total),(Decimal('100000.00'),Decimal('100000.00')))
    def test_a_return_request_moves_no_stock_and_a_returned_phone_waits_in_holding(self):
        from . import returns
        d=self.phone(self.branch);o=self.order([self.item()]);inv=self.ship(o)
        oo.request_return(o,self.user,'Not as described');d.refresh_from_db();o.refresh_from_db();self.assertEqual((d.status,oo.display_stage(o)),('Sold','Return Requested'))
        ret=s.submit(returns.create(inv,self.user,[str(d.pk)],'Screen scratched','Not as described'),self.user);d.refresh_from_db()
        self.assertEqual(d.status,'Holding');self.assertEqual(oo.display_stage(o),'Returned')
        self.assertEqual(web('select coalesce(sum(quantity),0) from web_variants where product_id=%s',self.red.pk)[0][0],0)    # not for sale until inspected
    def test_a_cancelled_order_releases_stock_but_a_dispatched_one_cannot_be_cancelled(self):
        d=self.phone(self.branch);o=self.order([self.item()]);oo.cancel(o,self.user,'No answer');d.refresh_from_db();self.assertEqual(d.status,'Available')
        o2=self.order([self.item()]);oo.dispatch(o2,self.user,'TCS','T')
        with self.assertRaises(ValidationError):oo.cancel(o2,self.user,'late')
    def test_orders_with_bad_variants_prices_or_duplicate_lines_are_refused(self):
        self.phone(self.branch)
        with self.assertRaises(ValidationError):self.order([self.item(price='1')])
        with self.assertRaises(ValidationError):self.order([dict(self.item(),product_id='00000000-0000-0000-0000-000000000000')])
        with self.assertRaises(ValidationError):self.order([self.item(),self.item()])
        self.assertEqual(OnlineOrder.objects.count(),0);self.assertEqual(Device.objects.filter(status='Reserved').count(),0)
    def test_status_api_shows_the_step(self):
        from django.test import override_settings
        self.phone(self.branch)
        data=dict(external_ref='W-77',customer=dict(name='Sara',phone='03211234567'),items=[self.item()]);o=oo.create(data)[0]
        with override_settings(ONLINE_API_KEY='k'):
            get=lambda:self.client.get(reverse('api_online_order_status',args=['W-77']),HTTP_X_API_KEY='k').json()
            self.assertEqual((get()['order_status'],get()['status'],get()['stage']),('Unconfirmed','New','Not dispatched'))
            oo.confirm(o,self.user);self.assertEqual(get()['order_status'],'Confirmed')
            oo.dispatch(o,self.user,'TCS','T-1');self.assertEqual((get()['order_status'],get()['tracking']),('Confirmed','T-1'))       # shipped, cash not yet collected: still unfulfilled
            oo.set_stage(o,self.user,'In Transit');self.assertEqual(get()['stage'],'In Transit')
            oo.deliver(o,self.user);self.assertEqual(get()['order_status'],'Fulfilled')                                                  # cash received + tracking = fulfilled


class Consistency(OrderBase):
    def test_the_check_is_clean_through_a_whole_order_and_catches_a_broken_reservation(self):
        d=self.phone(self.branch);self.accessories(self.branch,5);self.assertEqual(online_check.problems(),[])
        o=self.order([self.item(),dict(product_id=str(self.cover.pk),quantity=2)]);self.assertEqual(online_check.problems(),[])
        Device.objects.filter(pk=d.pk).update(status='Available');self.assertTrue(any('not Reserved' in p for p in online_check.problems()))
        Device.objects.filter(pk=d.pk).update(status='Reserved');self.assertEqual(online_check.problems(),[])
        oo.dispatch(o,self.user,'TCS','T');oo.deliver(o,self.user);self.assertEqual(online_check.problems(),[])
    def test_a_dispatched_order_without_tracking_is_reported(self):
        self.phone(self.branch);o=self.order([self.item()]);oo.dispatch(o,self.user);self.assertTrue(any('without courier' in p for p in online_check.problems()))


class Race(TransactionTestCase):
    """Real concurrent requests on real connections: the last phone can only be promised once, and a repeated reference is one order."""
    setUp=OrderBase.setUp;phone=OrderBase.phone;item=OrderBase.item
    def go(self,jobs):
        results=[None]*len(jobs)
        def run(i,job):
            try:results[i]=('ok',job())
            except Exception as error:results[i]=('error',error)
            finally:connections.close_all()
        threads=[threading.Thread(target=run,args=(i,j)) for i,j in enumerate(jobs)]
        [t.start() for t in threads];[t.join(60) for t in threads];return results
    def data(self,ref,qty=1):
        return dict(external_ref=ref,customer=dict(name='Ali Khan',phone='0300 1234567'),payment_method='Cash on Delivery',items=[self.item(qty=qty)])
    def test_two_customers_racing_for_the_last_phone_only_one_wins(self):
        self.phone(self.branch);r=self.go([lambda:oo.create(self.data('RACE-A'))[0].pk,lambda:oo.create(self.data('RACE-B'))[0].pk])
        self.assertEqual(sorted(x[0] for x in r),['error','ok']);self.assertEqual(OnlineOrder.objects.count(),1);self.assertEqual(Device.objects.filter(status='Reserved').count(),1)
    def test_the_same_reference_sent_twice_at_once_is_one_order(self):
        self.phone(self.branch);self.phone(self.branch);r=self.go([lambda:oo.create(self.data('SAME-1'))[0].pk,lambda:oo.create(self.data('SAME-1'))[0].pk])
        self.assertEqual([x[0] for x in r],['ok','ok']);self.assertEqual(r[0][1],r[1][1]);self.assertEqual(OnlineOrder.objects.count(),1);self.assertEqual(Device.objects.filter(status='Reserved').count(),1)
