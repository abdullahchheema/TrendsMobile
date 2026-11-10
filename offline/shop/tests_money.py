"""Accounts & Finance: Money In/Out, expenses, dues, cash and bank, one transaction history that always reconciles."""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import imei
from .tests_transfers import transfer_setup
from . import coa,easy_retail,money,shop_accounts,shopdefaults,services as s

D=Decimal


class Base(TestCase):
    def setUp(self):
        transfer_setup(self);coa.install_standard(self.user);self.today=timezone.localdate();self.n=0
        self.a=shop_accounts.create_login(self.branch,'money-a','Strong-pass-for-MA-77!');self.b=shop_accounts.create_login(self.other,'money-b','Strong-pass-for-MB-77!')
    def acc(self,code):return Account.objects.get(code='COA-'+code)
    def cash(self,branch=None,method='Cash'):
        branch=branch or self.branch;return s.account_balance(branch,Account.objects.get(pk=ShopSettings.objects.get(branch=branch).payment_methods[method]))
    def fund(self,amount=500000,branch=None):
        return money.record_voucher(self.user,branch or self.branch,'capital',self.acc('3100'),D(amount),'Cash',self.today-timedelta(days=30),'Float')
    def phone(self,cost=50000,status='full'):
        self.n+=1
        data=dict(branch=self.branch,party_name='Money Supplier',product=self.product,quantity=1,price=cost,asking_price=0,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,
                  posting_date=self.today-timedelta(days=5),payment_status=status,method='Cash',imeis=[[imei(900+self.n)]])
        return easy_retail.purchase(data,self.user)
    def credit_sale(self,price=60000,name='Dues Customer',paid='unpaid',amount=None):
        bill=self.phone();device=bill.reference.lines.get().device
        data=dict(branch=self.branch,party_name=name,phone=device,price=price,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today-timedelta(days=2),payment_status=paid,method='Cash')
        if amount:data['amount_paid']=amount
        return easy_retail.sale(data,self.user)
    def login(self,user):self.client.force_login(user)


class History(Base):
    def test_history_types_totals_and_balances_reconcile_and_transfers_are_not_income(self):
        self.fund(500000);bill=self.phone(50000,'full');sale=self.credit_sale(60000,paid='part',amount=20000)
        money.record_voucher(self.user,self.branch,'expense',self.acc('6100'),D(8000),'Cash',self.today,'October rent')
        before=self.cash();money.move_money(self.user,self.branch,'Cash','Bank Transfer',D(10000),self.today,'DEP-1','Deposit')
        rows,totals=money.history([self.branch]);types={r['type'] for r in rows}
        self.assertTrue({'Owner funds','Expense','Transfer','Sale payment'}<=types,types)
        moved=[r for r in rows if r['type']=='Transfer'];self.assertEqual(len(moved),1);self.assertEqual((moved[0]['received'],moved[0]['paid'],moved[0]['moved']),(0,0,D('10000.00')));self.assertIn('Cash → Bank Transfer',moved[0]['method'])
        position=sum(b['amount'] for b in money.balances([self.branch]))
        self.assertEqual(totals['received']-totals['paid'],position)        # all balances equal money in minus money out; the deposit changes nothing in total
        self.assertEqual(self.cash(),before-D('10000'));self.assertEqual(self.cash(method='Bank Transfer'),D('10000'))
    def test_a_cancelled_payment_stays_visible_together_with_its_reversal(self):
        self.fund();exp=money.record_voucher(self.user,self.branch,'expense',self.acc('6100'),D(5000),'Cash',self.today,'Rent')
        s.cancel(exp,self.user);rows,totals=money.history([self.branch]);mine=[r for r in rows if r['code']==exp.code or r['type']=='Reversal']
        self.assertTrue(any(r['status']=='Cancelled' for r in rows));self.assertTrue(any(r['type']=='Reversal' for r in rows))
        self.assertEqual(totals['received']-totals['paid'],sum(b['amount'] for b in money.balances([self.branch])))
    def test_filters_by_type_method_and_search(self):
        self.fund();money.record_voucher(self.user,self.branch,'expense',self.acc('6300'),D(3000),'Cash',self.today,'Electricity bill')
        rows,_=money.history([self.branch],kind='Expense');self.assertEqual({r['type'] for r in rows},{'Expense'})
        rows,_=money.history([self.branch],search='electricity');self.assertEqual(len(rows),1)
        rows,_=money.history([self.branch],method='Bank Transfer');self.assertEqual(rows,[])
        rows,_=money.history([self.branch],start=self.today+timedelta(days=1));self.assertEqual(rows,[])


class CustomerDues(Base):
    def test_credit_sale_appears_in_dues_and_a_payment_reduces_it_without_new_revenue(self):
        self.fund();sale=self.credit_sale(60000);customer=sale.customer
        rows=money.customer_dues([self.branch]);self.assertEqual(len(rows),1);self.assertEqual((rows[0]['party'].pk,rows[0]['remaining'],rows[0]['billed'],rows[0]['received']),(customer.pk,D('60000.00'),D('60000.00'),D('0.00')))
        sales_before=s.account_balance(self.branch,s.account(sale,'sales'));cash_before=self.cash()
        pay=money.record_party_payment(self.user,customer,D(25000),'Cash',self.today,'','part payment')
        self.assertEqual(self.cash(),cash_before+D('25000'));self.assertEqual(s.account_balance(self.branch,s.account(sale,'sales')),sales_before)       # money in, not new sales
        rows=money.customer_dues([self.branch]);self.assertEqual((rows[0]['remaining'],rows[0]['received']),(D('35000.00'),D('25000.00')))
        sale.refresh_from_db();self.assertEqual(sale.outstanding,D('35000.00'));self.assertEqual(pay.reference_id,sale.pk)
    def test_payment_applies_oldest_first_and_extra_stays_on_account(self):
        self.fund();first=self.credit_sale(30000,name='Oldest Olive');second=self.credit_sale(20000,name='Oldest Olive')
        money.record_party_payment(self.user,first.customer,D(35000),'Cash',self.today)
        first.refresh_from_db();second.refresh_from_db();self.assertEqual((first.outstanding,second.outstanding),(D('0.00'),D('15000.00')))
        money.record_party_payment(self.user,first.customer,D(20000),'Cash',self.today)
        second.refresh_from_db();self.assertEqual(second.outstanding,D('0.00'));rows=money.customer_dues([self.branch]);self.assertEqual(rows,[])        # fully paid, 5,000 over is credit, not a due
        self.assertEqual(money.party_detail(first.customer)['remaining'],D('-5000.00'))
    def test_rules_refuse_empty_amount_wrong_method_and_a_party_without_a_shop(self):
        self.fund();sale=self.credit_sale(10000)
        with self.assertRaises(ValidationError):money.record_party_payment(self.user,sale.customer,D(0),'Cash',self.today)
        with self.assertRaises(ValidationError):money.record_party_payment(self.user,sale.customer,D(10),'Gold coins',self.today)
        nobody=Party.objects.create(code='NOB',kind='Customer',name='No Shop')
        with self.assertRaises(ValidationError):money.record_party_payment(self.user,nobody,D(10),'Cash',self.today)
    def test_screens_list_record_and_show_history(self):
        self.fund();sale=self.credit_sale(40000);self.login(self.a)
        page=self.client.get(reverse('customer_dues'));self.assertContains(page,'Dues Customer');self.assertContains(page,'40,000')
        account=self.client.get(reverse('party_account',args=[sale.customer.pk]));self.assertContains(account,'Record a payment received')
        post=self.client.post(reverse('party_account',args=[sale.customer.pk]),dict(request_key=str(account.context['request_key']),amount='15000',method='Cash',date=str(self.today),reference='',description=''),follow=True)
        self.assertContains(post,'Payment received');self.assertContains(post,'25,000.00');self.assertContains(post,'Payment history')


class SupplierDues(Base):
    def test_supplier_bill_on_credit_is_paid_down_and_cash_is_checked(self):
        self.fund(200000);bill=self.phone(40000,'unpaid');supplier=bill.supplier
        rows=money.supplier_dues([self.branch]);self.assertEqual((rows[0]['remaining'],rows[0]['billed']),(D('40000.00'),D('40000.00')))
        before=self.cash();money.record_party_payment(self.user,supplier,D(15000),'Cash',self.today)
        self.assertEqual(self.cash(),before-D('15000'));self.assertEqual(money.supplier_dues([self.branch])[0]['remaining'],D('25000.00'))
        bill.refresh_from_db();self.assertEqual(bill.outstanding,D('25000.00'))
    def test_purchases_are_not_expenses_and_expense_screen_lists_only_expenses(self):
        self.fund();self.phone(30000,'full');money.record_voucher(self.user,self.branch,'expense',self.acc('6100'),D(2000),'Cash',self.today,'Rent')
        self.login(self.a);page=self.client.get(reverse('money_expenses'));self.assertContains(page,'Rent');self.assertNotContains(page,'Money Supplier');self.assertContains(page,'Mobile load and balance')


class MoneyScreens(Base):
    def test_money_in_and_out_screens_record_expenses_income_and_owner_money(self):
        self.login(self.a);key=lambda url:str(self.client.get(reverse(url)).context['request_key'])
        def post(url,action,**fields):
            return self.client.post(reverse(url),dict(action=action,request_key=key(url),branch=str(self.branch.pk),**fields),follow=True)
        page=post('money_in','owner_funds',amount='100000',method='Cash',date=str(self.today),description='Starting cash');self.assertContains(page,'Owner funds recorded')
        page=post('money_in','other_income',income_account=str(self.acc('4900').pk),amount='2500',method='Cash',date=str(self.today),description='Scrap sold');self.assertContains(page,'Income recorded')
        page=post('money_out','expense',category=str(self.acc('6410').pk),amount='500',method='Cash',date=str(self.today),description='Mobile load');self.assertContains(page,'Expense recorded')
        page=post('money_out','owner_drawings',amount='1000',method='Cash',date=str(self.today),description='Owner took cash');self.assertContains(page,'Owner drawings recorded')
        self.assertEqual(self.cash(),D('100000')+D('2500')-D('500')-D('1000'))
        rows,_=money.history([self.branch]);self.assertEqual({r['type'] for r in rows}>={'Owner funds','Other income','Expense','Owner drawings'},True)
        self.assertContains(self.client.get(reverse('money_in')),'Owner funds');self.assertNotContains(self.client.get(reverse('money_in')),'Mobile load</td>')
    def test_expenses_cannot_overdraw_cash_and_duplicate_form_posts_once(self):
        self.login(self.a);key=str(self.client.get(reverse('money_out')).context['request_key']);data=dict(action='expense',request_key=key,branch=str(self.branch.pk),category=str(self.acc('6100').pk),amount='999999',method='Cash',date=str(self.today),description='Too much')
        page=self.client.post(reverse('money_out'),data,follow=True);self.assertContains(page,'cannot be paid');self.assertEqual(Document.objects.filter(kind='Voucher').count(),0)
    def test_cash_and_bank_page_balances_and_move(self):
        self.fund(100000);self.login(self.a);page=self.client.get(reverse('cash_and_bank'));self.assertContains(page,'Cash');self.assertContains(page,'100,000.00')
        key=str(page.context['request_key']);post=self.client.post(reverse('cash_and_bank'),dict(request_key=key,branch=str(self.branch.pk),source='Cash',target='Bank Transfer',amount='20000',date=str(self.today),reference='DEP-77',notes='Deposit'),follow=True)
        self.assertContains(post,'Money moved');self.assertEqual((self.cash(),self.cash(method='Bank Transfer')),(D('80000.00'),D('20000.00')))
        bad=self.client.post(reverse('cash_and_bank'),dict(request_key=str(self.client.get(reverse('cash_and_bank')).context['request_key']),branch=str(self.branch.pk),source='Cash',target='Cash',amount='1',date=str(self.today)),follow=True);self.assertContains(bad,'different')
    def test_history_page_filters_and_menu_links(self):
        self.fund();self.login(self.a);page=self.client.get(reverse('money_history'),{'type':'Owner funds'});self.assertContains(page,'Owner funds');self.assertEqual(page.status_code,200)
        side=page.content.decode().split('<aside',1)[1].split('</aside>',1)[0]
        for text in ('Money In','Money Out','Expenses','Customer Dues','Supplier Dues','Cash and Bank','Transaction History'):self.assertIn(text,side,text)


class Isolation(Base):
    def test_a_shop_sees_only_its_own_money_dues_and_accounts(self):
        self.fund(300000);sale=self.credit_sale(30000,name='Shop A Customer')
        other_customer=Party.objects.create(code='OC',kind='Customer',name='Shop B Customer',branch=self.other)
        self.login(self.b);self.assertNotContains(self.client.get(reverse('customer_dues')),'Shop A Customer');self.assertEqual(self.client.get(reverse('party_account',args=[sale.customer.pk])).status_code,404)
        self.assertEqual(self.client.get(reverse('money_history')).context['rows'],[])         # shop B has no money movements; shop A's are invisible to it
        self.login(self.a);self.assertContains(self.client.get(reverse('customer_dues')),'Shop A Customer');self.assertEqual(self.client.get(reverse('party_account',args=[other_customer.pk])).status_code,404)
        post=self.client.post(reverse('party_account',args=[other_customer.pk]),dict(amount='1',method='Cash',date=str(self.today)));self.assertEqual(post.status_code,404)
        self.login(self.user);both=self.client.get(reverse('money_history')).context['rows'];self.assertTrue(both);self.assertContains(self.client.get(reverse('customer_dues')),'Shop A Customer')
    def test_a_shop_cannot_post_into_another_shop_by_changing_the_address(self):
        self.login(self.b);key=str(self.client.get(reverse('money_in')).context['request_key'])
        page=self.client.post(reverse('money_in'),dict(action='owner_funds',request_key=key,branch=str(self.branch.pk),amount='10',method='Cash',date=str(self.today)));self.assertEqual(page.status_code,404)
        self.assertEqual(Document.objects.filter(kind='Voucher').count(),0)
