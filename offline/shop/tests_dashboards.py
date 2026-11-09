"""Owner and Shop Accountant dashboards: every figure from real records, own-shop only for shops."""
from datetime import timedelta
from decimal import Decimal
from django.urls import reverse
from .models import *
from .tests_money import Base,D
from . import dashboard_metrics as dm,money


class Figures(Base):
    def snap(self,shops=None,start=None,end=None):
        return dm.snapshot(shops or [self.branch],start or self.today-timedelta(days=30),end or self.today)
    def test_sales_cash_credit_purchases_stock_dues_and_money_match_the_records(self):
        self.fund(500000);cash_sale=self.credit_sale(60000,name='Cash Cathy',paid='full');credit=self.credit_sale(40000,name='Credit Carl')
        money.record_voucher(self.user,self.branch,'expense',self.acc('6100'),D(8000),'Cash',self.today,'Rent')
        snap=self.snap()
        self.assertEqual(snap['income'],D('100000.00'));self.assertEqual((snap['mix']['cash_sales'],snap['mix']['credit_sales']),(D('60000.00'),D('40000.00')))
        self.assertEqual(snap['purchases'],D('100000.00'));self.assertEqual(snap['expenses'],D('8000.00'))
        self.assertEqual(snap['receivable'],D('40000.00'));self.assertEqual(snap['payable'],D('0.00'))
        self.assertEqual(snap['stock_phones'],0);self.assertEqual(snap['phones'],2)
        self.assertEqual(snap['cash_in_hand']+snap['bank_total'],sum(b['amount'] for b in money.balances([self.branch])))
        self.assertEqual(snap['net'],snap['gross']-snap['expenses'])
        self.assertEqual(len(snap['trend']),6);self.assertEqual(snap['trend'][-1]['sales'],D('100000.00'))
    def test_paying_a_credit_sale_later_does_not_turn_it_into_a_cash_sale(self):
        self.fund();sale=self.credit_sale(40000,name='Slow Payer');before=self.snap()['mix']
        money.record_party_payment(self.user,sale.customer,D(40000),'Cash',self.today)
        after=self.snap();self.assertEqual((after['mix']['cash_sales'],after['mix']['credit_sales']),(before['cash_sales'],before['credit_sales']));self.assertEqual(after['receivable'],0)
    def test_stock_counts_and_pending_transfers(self):
        self.fund();self.phone(30000)
        self.assertEqual(self.snap()['stock_phones'],1);self.assertEqual(self.snap()['stock'],D('30000.00'))
        self.assertEqual(self.snap()['pending_in'],0)
    def test_today_and_month_sales_use_their_own_windows(self):
        self.fund();self.credit_sale(25000,name='Today Tom',paid='full')
        snap=self.snap(start=self.today-timedelta(days=1),end=self.today);self.assertEqual(snap['today_sales'],snap['month_sales']) if self.today.day==1 else self.assertLessEqual(snap['today_sales'],snap['month_sales'])


class Screens(Base):
    def setUp(self):
        super().setUp();self.fund(500000);self.credit_sale(50000,name='Alpha Customer',paid='full')
        # a sale in the other shop must never reach shop A's screen
        self.cust_b=Party.objects.create(code='DB',kind='Customer',name='Beta Customer',branch=self.other)
    def test_a_shop_accountant_sees_only_their_own_shop_with_no_comparison(self):
        self.login(self.a);page=self.client.get(reverse('dashboard'));self.assertEqual(page.status_code,200)
        ov=page.context['overview'];self.assertEqual([r['shop'] for r in ov['compare']],[self.branch.name]);self.assertFalse(page.context['is_owner'])
        for text in ('MY SHOP','Today','Cash in hand','Customers owe you','Net profit','Phones in stock'):self.assertContains(page,text)
        self.assertNotContains(page,'SHOP COMPARISON');self.assertNotContains(page,'All shops');self.assertNotContains(page,self.other.name)
        self.assertEqual(self.client.get(reverse('dashboard'),{'branch':str(self.other.pk)}).status_code,404)
    def test_the_owner_sees_every_shop_can_pick_one_and_gets_a_comparison(self):
        self.login(self.user);page=self.client.get(reverse('dashboard'));ov=page.context['overview'];self.assertTrue(page.context['is_owner'])
        self.assertEqual({r['shop'] for r in ov['compare']},{self.branch.name,self.other.name});self.assertContains(page,'SHOP COMPARISON');self.assertContains(page,'Pending transfers');self.assertContains(page,'OWNER')
        one=self.client.get(reverse('dashboard'),{'branch':str(self.other.pk)}).context['overview'];self.assertEqual(one['scope'],self.other.name);self.assertEqual(one['income'],0)
    def test_cards_link_to_the_records_behind_them(self):
        self.login(self.a);page=self.client.get(reverse('dashboard'))
        for name in ('customer_dues','supplier_dues','cash_and_bank','money_expenses'):self.assertContains(page,reverse(name))
        self.assertContains(page,'/query-report/Daily%20Sales')
    def test_period_filter_and_bad_dates_fall_back_safely(self):
        self.login(self.a);self.assertEqual(self.client.get(reverse('dashboard'),{'range':'year'}).status_code,200)
        self.assertEqual(self.client.get(reverse('dashboard'),{'range':'custom','from_date':'junk'}).status_code,200)
