"""Online sales and the books: profit, credit purchases, failed deliveries and returns to a shop, the order badge, what a shop may see, and the
website notification. Phones here come from real purchase documents (cash and credit), not shortcuts."""
import json
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.models import Sum
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from .models import *
from . import tests as base_tests
from .tests_online_orders import OrderBase
from . import money_views,online_accounts,online_orders as oo,shop_accounts,shopdefaults,services as s,website_sync


class Money(OrderBase):
    def bought(self,cost=120000,pay_cash=False,n=1):
        """A real purchase: receive the phone, bill the supplier (credit), optionally pay the supplier in full at once."""
        _,receipt,device=base_tests.Workflows.intake(self,n,cost=cost)
        bill=s.submit(s.supplier_bill(receipt,self.user),self.user)
        if pay_cash:          # money cannot leave an account that does not hold it (D8): the owner puts the cash in first
            from . import money
            money.record_voucher(self.user,self.branch,'capital',Account.objects.get(code='COA-3100'),str(cost),'Cash',timezone.localdate(),'Owner funds for the purchase')
            s.submit(s.new_doc('Payment Entry',self.branch,supplier=self.supplier,handler=self.handler,reference=bill,data=dict(amount=str(cost),direction='Pay',mode='Cash')),self.user);bill.refresh_from_db()
        Product.objects.filter(pk=self.product.pk).update(online_visible=True,slug='iphone-13')
        Device.objects.filter(pk=device.pk).update(online_price=Decimal('135000'));device.refresh_from_db()
        return device,bill
    def sell(self,price='135000',**extra):
        return self.order([dict(product_id=str(self.product.pk),condition='Not Inspected',pta_status='Not recorded',device_type='Used',price=price,quantity=1)],**extra)
    def balanced(self):
        t=LedgerEntry.objects.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(t['d'],t['c'])
    def shop_sales(self):
        return Document.objects.filter(branch=self.branch,kind__in=list(s.SELL),is_return=False).count()
    def shop_income(self):
        return LedgerEntry.objects.filter(branch=self.branch,account__account_type='Income Account').aggregate(c=Sum('credit'))['c'] or 0
    def web_qty(self):
        with connection.cursor() as c:
            c.execute('select coalesce(sum(quantity),0) from web_variants where product_id=%s',[self.product.pk]);return c.fetchone()[0]

    def test_a_cash_purchase_sold_online_makes_15000_profit_for_the_online_channel_and_none_for_the_shop(self):
        d,bill=self.bought(pay_cash=True);self.assertEqual((bill.outstanding,self.web_qty()),(0,1))
        o=self.sell();oo.dispatch(o,self.user,'TCS','T-1');oo.deliver(o,self.user)
        a=online_accounts.summary();self.assertEqual((a['revenue'],a['cogs'],a['gross_profit']),(Decimal('135000'),Decimal('120000'),Decimal('15000')))
        self.assertEqual((self.shop_sales(),self.shop_income()),(0,0))                                   # no fictitious POS sale or revenue in the shop
        d.refresh_from_db();self.assertEqual((d.status,d.purchase_cost),('Sold',Decimal('120000')));self.assertEqual(self.web_qty(),0)
        self.assertEqual(self.inventory(self.branch),Decimal('0'));self.assertEqual(self.clearing(self.branch),Decimal('120000'))   # the shop is owed its cost by the online channel
        self.assertEqual(Document.objects.filter(branch=self.branch,kind='Purchase Receipt').count(),1);self.balanced()

    def test_a_credit_purchase_sold_online_keeps_the_supplier_payable_and_books_the_same_profit(self):
        d,bill=self.bought(pay_cash=False);self.assertEqual(bill.outstanding,120000)
        o=self.sell();oo.dispatch(o,self.user,'TCS','T-2');oo.deliver(o,self.user);bill.refresh_from_db()
        a=online_accounts.summary();self.assertEqual((a['revenue'],a['cogs'],a['gross_profit']),(Decimal('135000'),Decimal('120000'),Decimal('15000')))
        self.assertEqual(bill.outstanding,120000)                                                         # selling the phone does not pay the supplier
        self.assertEqual((self.shop_sales(),self.shop_income(),self.inventory(self.branch)),(0,0,Decimal('0')));self.balanced()

    def test_a_failed_delivery_sells_nothing_and_the_phone_is_for_sale_again_only_after_it_is_back_and_approved(self):
        d,bill=self.bought(pay_cash=False);o=self.sell();oo.dispatch(o,self.user,'TCS','TRK-9');oo.delivery_failed(o,self.user,'Customer refused');o.refresh_from_db()
        self.assertEqual((o.status,oo.display_stage(o),o.failed_reason),('Dispatched','Delivery Failed','Customer refused'))
        d.refresh_from_db();self.assertEqual((d.status,self.web_qty()),('Reserved',0))                  # out with the courier: still the shop's stock in the books (nothing is sold before delivery, D4), not for sale
        self.assertEqual((self.inventory(self.branch),self.clearing(self.branch),oo.invoices(o).count()),(Decimal('120000'),0,0))
        with self.assertRaises(ValidationError):oo.deliver(o,self.user)                                  # a failed delivery cannot be marked delivered
        wh=shopdefaults.warehouse(self.branch);cat=money_views._accounts('Expense',('COA-6950','COA-5200')).order_by('code').first()
        with self.assertRaises(ValidationError):oo.receive_returned(o,self.user,wh,'','approve')         # the condition must be written
        from . import money
        money_views._ensure_chart(self.user);money.record_voucher(self.user,oo.online_branch(),'capital',money_views._by_code('COA-3100'),'5000','Cash',timezone.localdate(),'Owner money for courier costs')
        with self.assertRaisesMessage(ValidationError,'goes back to the shop that sent it'):oo.receive_returned(o,self.user,shopdefaults.warehouse(self.other),'Sealed box','hold')
        self.assertIsNone(oo.receive_returned(o,self.user,wh,'Sealed box','hold',courier_charge='350',expense_account=cat,method='Cash'))      # cash on delivery: nothing is invoiced
        o.refresh_from_db();d.refresh_from_db()
        self.assertEqual((o.status,o.tracking,o.courier,o.lines.get().unit_price,o.total),('Returned','TRK-9','TCS',Decimal('135000.00'),Decimal('135000.00')))   # the order and its tracking stay as they were
        self.assertEqual((d.status,d.branch_id,d.purchase_cost,self.web_qty()),('Holding',self.branch.pk,Decimal('120000'),0))   # back in the shop at its original cost, but not for sale yet
        self.assertEqual(self.inventory(self.branch),Decimal('120000'))                                  # inventory never left: still the ORIGINAL cost, never expensed
        bill.refresh_from_db();self.assertEqual(bill.outstanding,120000)                                  # the supplier's invoice is untouched
        a=online_accounts.summary();self.assertEqual((a['revenue'],a['cogs'],a['gross_profit'],a['expenses'],a['net_profit']),(0,0,0,Decimal('350'),Decimal('-350')))   # no profit from a failed sale; the courier charge is its own cost
        self.assertEqual(self.clearing(self.branch),Decimal('0'));self.assertEqual((self.shop_sales(),self.shop_income()),(0,0));self.assertTrue(a['books_agree']);self.balanced()
        self.assertEqual(a['pending'],0)                                                                  # the customer owes nothing for the refused phone
        d.status='Available';d.save()                                                                      # (a manager accepts it) only then is it for sale again
        self.assertEqual(self.web_qty(),1)
        self.assertEqual([e.kind for e in o.events.all()][-3:],['Dispatched','Delivery failed','Returned to shop'])

    def test_approving_resale_at_receipt_makes_the_phone_available_and_damaged_does_not(self):
        for decision,expected in (('approve','Available'),('damaged','Unavailable')):
            d,bill=self.bought(n=10 if decision=='approve' else 20);o=self.sell();oo.dispatch(o,self.user,'TCS','T');oo.delivery_failed(o,self.user,'Wrong address')
            oo.receive_returned(o,self.user,shopdefaults.warehouse(self.branch),'Checked',decision);d.refresh_from_db();self.assertEqual(d.status,expected)
            Device.objects.filter(pk=d.pk).update(status='Sold')       # next round uses a fresh phone

    def test_a_retried_delivery_can_still_succeed_and_a_phone_cannot_be_received_without_a_failed_delivery(self):
        d,_=self.bought();o=self.sell();oo.dispatch(o,self.user,'TCS','T')
        with self.assertRaises(ValidationError):oo.receive_returned(o,self.user,shopdefaults.warehouse(self.branch),'x','approve')
        oo.delivery_failed(o,self.user,'Not home');oo.set_stage(o,self.user,'In Transit');oo.deliver(o,self.user);o.refresh_from_db();self.assertEqual(o.status,'Delivered')


class Notifications(OrderBase):
    def setUp(self):
        super().setUp();self.web=shop_accounts.create_online_login('Web Team','web-team','Strong-pass-for-WT-77!')
        self.shop_a=shop_accounts.create_login(self.branch,'shop-a','Strong-pass-for-SA-77!');self.shop_b=shop_accounts.create_login(self.other,'shop-b','Strong-pass-for-SB-77!')
    def api(self,ref):
        data=dict(external_ref=ref,customer=dict(name='Ali Khan',phone='03001234567'),items=[self.item()])
        return self.client.post(reverse('api_online_order'),json.dumps(data),content_type='application/json',HTTP_X_API_KEY='k')
    def test_the_badge_counts_orders_still_to_confirm_and_drops_when_confirmed_or_cancelled(self):
        with override_settings(ONLINE_API_KEY='k'):
            self.phone(self.branch);self.phone(self.branch);self.assertEqual(oo.unseen_count(),0)
            self.assertEqual(self.api('W-1').status_code,201);self.assertEqual(self.api('W-1').status_code,200);self.assertEqual(oo.unseen_count(),1)      # a repeat is not a second badge
            self.assertEqual(self.api('W-2').status_code,201);self.assertEqual(oo.unseen_count(),2)
            self.client.force_login(self.web);o=OnlineOrder.objects.get(external_ref='W-1')
            self.assertEqual(self.client.get(reverse('live_state')).json()['online'],2);self.client.get(reverse('online_order',args=[o.pk]));self.assertEqual(oo.unseen_count(),2)   # just looking does not confirm it
            self.client.post(reverse('online_order',args=[o.pk]),dict(action='confirm'));self.assertEqual(oo.unseen_count(),1)
            oo.cancel(OnlineOrder.objects.get(external_ref='W-2'),self.user,'No answer');self.assertEqual(oo.unseen_count(),0)
            self.assertContains(self.client.get(reverse('online_orders')),'data-live-badge="online"')
    def test_a_team_member_typing_an_order_after_a_call_needs_no_confirmation(self):
        self.phone(self.branch);self.phone(self.branch)
        o=oo.create(dict(customer=dict(name='Sara',phone='03211234567'),items=[self.item()]),self.user,'Manual')[0];self.assertEqual((oo.unseen_count(),oo.order_status(o)),(0,'Confirmed'))
    def test_a_shop_packs_only_confirmed_orders_and_sees_who_to_deliver_to_but_no_money(self):
        with override_settings(ONLINE_API_KEY='k'):
            d=self.phone(self.branch);self.api('W-5');o=OnlineOrder.objects.get(external_ref='W-5')
            self.client.force_login(self.shop_a);self.assertNotContains(self.client.get(reverse('shop_online_orders')),o.code);self.assertEqual(self.client.get(reverse('live_state')).json()['shop'],0)   # not yet confirmed
            oo.confirm(o,self.user);page=self.client.get(reverse('shop_online_orders'))
            self.assertContains(page,o.code);self.assertContains(page,d.code);self.assertContains(page,'Ali Khan');self.assertContains(page,'03001234567');self.assertNotContains(page,'100000')      # the shop sees who to deliver to (name, phone, address), never the money
            self.assertEqual(self.client.get(reverse('live_state')).json()['shop'],1)
            self.client.post(reverse('shop_online_orders'),dict(order=str(o.pk)));self.assertEqual(self.client.get(reverse('live_state')).json()['shop'],0)
            self.client.force_login(self.shop_b);self.assertNotContains(self.client.get(reverse('shop_online_orders')),o.code)               # the other shop never sees it
            self.assertEqual(self.client.post(reverse('shop_online_orders'),dict(order=str(o.pk))).status_code,404)
            self.assertEqual(self.client.post(reverse('online_order',args=[o.pk]),dict(action='cancel',reason='x')).status_code,403)         # and cannot act on it
    def test_the_live_fingerprint_changes_when_stock_or_orders_change(self):
        self.client.force_login(self.web);t1=self.client.get(reverse('live_state')).json()['token'];self.assertEqual(t1,self.client.get(reverse('live_state')).json()['token'])
        self.phone(self.branch);t2=self.client.get(reverse('live_state')).json()['token'];self.assertNotEqual(t1,t2)
        self.order([self.item()]);t3=self.client.get(reverse('live_state')).json()['token'];self.assertNotEqual(t2,t3)
        self.assertEqual(self.client.get(reverse('live_state')).headers['Cache-Control'],'no-store')
        self.client.logout();self.assertNotEqual(self.client.get(reverse('live_state')).status_code,200)


@override_settings(WEBSITE_REVALIDATE_URL='https://site.test/api/revalidate',ONLINE_API_KEY='k')
class Website(OrderBase):
    def test_a_confirmed_order_tells_the_website_at_once_and_a_failure_loses_nothing(self):
        self.phone(self.branch);website_sync._last[0]=0
        with patch('shop.website_sync.urllib.request.urlopen') as call:
            with self.captureOnCommitCallbacks(execute=True):self.order([self.item()])
            self.assertEqual(call.call_count,1);request=call.call_args[0][0];self.assertEqual((request.full_url,request.get_header('X-api-key')),('https://site.test/api/revalidate','k'))
        website_sync._last[0]=0;self.phone(self.branch);before=website_sync.failures[0]
        with patch('shop.website_sync.urllib.request.urlopen',side_effect=OSError('site down')):
            with self.captureOnCommitCallbacks(execute=True):o=self.order([self.item()])         # the website being down never breaks or undoes the order
        self.assertEqual((OnlineOrder.objects.filter(pk=o.pk).exists(),website_sync.failures[0]),(True,before+1))
    @override_settings(WEBSITE_REVALIDATE_URL='')
    def test_nothing_is_called_when_no_website_is_configured(self):
        self.phone(self.branch)
        with patch('shop.website_sync.urllib.request.urlopen') as call:
            with self.captureOnCommitCallbacks(execute=True):self.order([self.item()])
            call.assert_not_called()
