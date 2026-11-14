"""Paying staff commission (owner's decision D2, 14 Nov 2026): earned on the sale date as before; the owner pays one sale straight away or
everything pending once a month; returns and cancellations take commission back; every screen shows earned, paid and still to pay."""
import uuid
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import *
from . import commission as cm,easy_retail,returns_flow,shopdefaults,staff_views,services as s
from . import tests_audit_oracle as oracle
from .tests_audit_base import AuditMixin,P


class Base(AuditMixin,TestCase):
    def setUp(self):
        self.build();self.capital(self.a,'2000000');self.owner_client=self.as_user(self.owner)
        self.owner_client.post(reverse('staff_list'),dict(action='add',name='Bilal Seller',branch=str(self.a.pk),commission='2',designation='Sales'))
        self.person=Staff.objects.get(name='Bilal Seller')
    def sell(self,price,days_ago=0,cost='50000'):
        when=self.today-timedelta(days=days_ago);phone,_=self.buy_phone(self.a,cost,price,pay='unpaid',when=when);me=shopdefaults.shop_person(self.a)
        return easy_retail.sale(dict(branch=self.a,party_name='Commission Customer',phone=phone,pta_status='PTA',price=str(price),staff=self.person,handler=me,posting_date=when,payment_status='full',amount_paid=None,method='Cash',notes=''),self.owner),phone
    def page(self,**query):
        return self.owner_client.get(reverse('staff_detail',args=[self.person.pk]),query)
    def pay(self,amount,sale=None,client=None,key=None,method='Cash',note=''):
        data=dict(request_key=str(key or uuid.uuid4()),amount=str(amount),method=method,date=str(self.today),note=note)
        if sale:data['sale']=str(sale.pk)
        return (client or self.owner_client).post(reverse('staff_detail',args=[self.person.pk]),data,follow=True)
    def expense(self):
        return sum(P(e.debit)-P(e.credit) for e in LedgerEntry.objects.filter(branch=self.a,account__code='COA-6750'))


class EarnedPaidPending(Base):
    def test_pay_one_sale_now_then_the_rest_at_the_end_of_the_month(self):
        first,_=self.sell('60000',days_ago=20);second,_=self.sell('80000.50',days_ago=10);third,_=self.sell('40000',days_ago=2)
        want=[oracle.to_paisa(oracle.F(v)*2/100) for v in ('60000','80000.50','40000')]          # 2% of each net amount, worked out independently
        position=cm.account(self.person);self.assertEqual((P(position['earned']),P(position['paid']),P(position['pending'])),(sum(want),0,sum(want)))
        cash=self.balance(self.a,'Cash')
        self.pay(oracle.rupees(want[1]),sale=second)                                                         # "pay now" for the second sale only
        position=cm.account(self.person);self.assertEqual(P(position['sales'][second.pk]['pending']),0);self.assertEqual(P(position['sales'][first.pk]['pending']),want[0])
        self.assertEqual((cash-self.balance(self.a,'Cash'),self.expense()),(want[1],want[1]))                 # paid from the shop's cash, an expense of the shop
        page=self.page(**{'from':str(self.today-timedelta(days=30)),'to':str(self.today)});self.assertEqual(P(page.context['position']['pending']),want[0]+want[2])
        self.pay(oracle.rupees(want[0]+want[2]),note='November commission')                                  # the monthly payment of everything pending
        position=cm.account(self.person);self.assertEqual((P(position['pending']),P(position['paid'])),(0,sum(want)))
        self.assertEqual({k:P(v['pending']) for k,v in position['sales'].items()},{first.pk:0,second.pk:0,third.pk:0})
        self.assertEqual(self.expense(),sum(want));self.assert_books_clean()
    def test_a_payment_settles_the_oldest_sales_first(self):
        old,_=self.sell('50000',days_ago=15);new,_=self.sell('50000',days_ago=1)
        self.pay('1000.00');position=cm.account(self.person)
        self.assertEqual((P(position['sales'][old.pk]['pending']),P(position['sales'][new.pk]['pending'])),(0,100000))
    def test_a_return_takes_commission_back_even_after_it_was_paid(self):
        sale,phone=self.sell('60000',days_ago=5);other,_=self.sell('50000',days_ago=3)
        self.pay('2200.00')                                                                                   # everything paid: 1,200 + 1,000
        returns_flow.settle(self.owner,sale,[str(phone.pk)],None,returns_flow.CONDITIONS[0],returns_flow.REASONS[1],'refund','Cash','')
        position=cm.account(self.person);self.assertEqual(P(position['pending']),-120000)                    # paid ahead by the 1,200 of the returned sale
        page=self.page();self.assertContains(page,'Paid ahead');self.assertEqual(self.pay('1.00').status_code,200)
        self.assertEqual(Document.objects.filter(kind='Voucher',data__commission_staff=str(self.person.pk)).count(),1)      # nothing more can be paid
        later,_=self.sell('100000',days_ago=0);position=cm.account(self.person)
        self.assertEqual((P(position['pending']),P(position['sales'][later.pk]['pending'])),(80000,80000))      # the next sale absorbs the 1,200 paid ahead
        self.assert_books_clean()
    def test_a_cancelled_sale_earns_nothing(self):
        sale,_=self.sell('60000',days_ago=1);self.assertEqual(P(cm.account(self.person)['pending']),120000)
        payment=Document.objects.get(kind='Payment Entry',reference=sale);s.cancel(payment,self.owner);s.cancel(sale,self.owner)
        self.assertEqual(P(cm.account(self.person)['earned']),0)
    def test_paid_all_time_and_paid_in_a_chosen_period(self):
        self.sell('50000',days_ago=40);self.sell('50000',days_ago=5)
        old=self.today-timedelta(days=35)
        cm.pay(self.owner,self.person,'1000.00','Cash',old);cm.pay(self.owner,self.person,'500.00','Cash',self.today)
        page=self.page(**{'from':str(self.today-timedelta(days=10)),'to':str(self.today)})
        self.assertEqual((P(page.context['paid_period_total']),P(page.context['position']['paid']),P(page.context['position']['pending'])),(50000,150000,50000))
        self.assertEqual(P(page.context['commission']),100000)                                              # earned in the period: the recent sale only
        listing=self.owner_client.get(reverse('staff_list'));row=next(r for r in listing.context['people'] if r['p'].pk==self.person.pk)
        self.assertEqual((P(row['paid']),P(row['pending'])),(150000,50000))
    def test_commission_follows_the_rate_of_the_sale_day(self):
        sale,_=self.sell('60000',days_ago=20);staff_views.set_rate(self.person,Decimal('5'),self.today-timedelta(days=10));self.person.save()
        later,_=self.sell('60000',days_ago=1);position=cm.account(self.person)
        self.assertEqual((P(position['sales'][sale.pk]['earned']),P(position['sales'][later.pk]['earned'])),(120000,300000))


class Rules(Base):
    def test_never_more_than_is_pending_and_never_for_another_persons_sale(self):
        sale,_=self.sell('60000',days_ago=1)
        with self.assertRaisesMessage(ValidationError,'still to be paid'):cm.pay(self.owner,self.person,'1200.01','Cash')
        with self.assertRaisesMessage(ValidationError,'more than zero'):cm.pay(self.owner,self.person,'0','Cash')
        with self.assertRaisesMessage(ValidationError,'future'):cm.pay(self.owner,self.person,'1','Cash',self.today+timedelta(days=1))
        self.owner_client.post(reverse('staff_list'),dict(action='add',name='Sana',branch=str(self.a.pk),commission='3'));sana=Staff.objects.get(name='Sana')
        with self.assertRaisesMessage(ValidationError,'not made by this person'):cm.pay(self.owner,sana,'1','Cash',sale=sale)
        self.assertEqual(Document.objects.filter(kind='Voucher',data__has_key='commission_staff').count(),0)
    def test_only_the_owner_pays(self):
        self.sell('60000',days_ago=1)
        with self.assertRaises(PermissionDenied):cm.pay(self.login_a,self.person,'100','Cash')
        self.assertEqual(self.pay('100.00',client=self.as_user(self.login_a)).status_code,403)
        self.assertEqual(self.pay('100.00',client=self.as_user(self.online_login)).status_code,403)
        self.assertEqual(P(cm.account(self.person)['paid']),0)
    def test_money_cannot_come_from_an_account_that_does_not_hold_it(self):
        self.sell('60000',days_ago=1)
        with self.assertRaisesMessage(ValidationError,'has only'):cm.pay(self.owner,self.person,'1200','Easypaisa')      # D8
        self.assertEqual(P(cm.account(self.person)['paid']),0)
    def test_pressing_pay_twice_pays_once(self):
        self.sell('60000',days_ago=1);key=uuid.uuid4()
        self.pay('600.00',key=key);self.pay('600.00',key=key)
        self.assertEqual((P(cm.account(self.person)['paid']),Document.objects.filter(kind='Voucher',data__commission_staff=str(self.person.pk)).count()),(60000,1))
    def test_a_cancelled_payment_is_owed_again(self):
        self.sell('60000',days_ago=1);doc=cm.pay(self.owner,self.person,'1200','Cash')
        s.cancel(doc,self.owner);self.assertEqual((P(cm.account(self.person)['paid']),P(cm.account(self.person)['pending'])),(0,120000));self.assertEqual(self.expense(),0)


class Screens(Base):
    def test_the_page_shows_earned_paid_and_still_to_pay_and_a_pay_now_link_per_sale(self):
        sale,_=self.sell('60000',days_ago=3);page=self.page();html=page.content.decode()
        for text in ('Earned (all time)','Paid (all time)','Still to pay','Pay commission','Pay now','1,200.00'):self.assertIn(text,html)
        page=self.page(sale=str(sale.pk),pay='1200.00');self.assertContains(page,'for one sale');self.assertContains(page,'value="1200.00"')
        self.pay('1200.00',sale=sale);page=self.page();self.assertContains(page,'Nothing to pay right now');self.assertContains(page,'Commission paid to Bilal Seller')
        self.assertContains(self.owner_client.get(reverse('staff_list')),'1,200.00')
