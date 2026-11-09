"""Phase G: fixed assets and depreciation, prepaid expenses and accruals, loans."""
from datetime import date,timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from . import advanced,coa,vouchers,services as s

D=Decimal


class Base(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());coa.install_standard(self.user);self.client.force_login(self.user);self.today=timezone.localdate()
        vouchers.post(dict(branch=self.branch,type='capital',posting_date=self.today-timedelta(days=400),amount=D('1000000'),account=str(self.acc('3100').pk),mode='Cash',description='Float'),self.user)
    def acc(self,code):return Account.objects.get(code='COA-'+code)
    def bal(self,code):return s.account_balance(self.branch,self.acc(code))
    def cash(self):return s.account_balance(self.branch,Account.objects.get(pk=ShopSettings.objects.get(branch=self.branch).payment_methods['Cash']))
    def asset(self,cost=120000,salvage=0,life=12,on=date(2025,1,10),method='Straight line',rate=0,funding='Cash'):
        data=dict(name='Display counter',purchase_date=on,cost=D(cost),salvage=D(salvage),life_months=life,method=method,rate=D(rate),asset_account=str(self.acc('1510').pk),accumulated_account=str(self.acc('1590').pk),expense_account=str(self.acc('6800').pk),funding=funding)
        if funding=='existing':data['equity_account']=str(self.acc('3100').pk)
        advanced.add_asset(self.user,self.branch,data);return FixedAsset.objects.latest('created')
    def sales_user(self):
        u=get_user_model().objects.create_user('clerk-adv',password='Unrelated-test-password-729!')
        Staff.objects.create(code='CLK-ADV',name='Clerk ADV',branch=self.branch,user=u,role=Role.objects.get(code='sales'));return u


class Assets(Base):
    def test_buying_an_asset_pays_cash_and_records_the_asset(self):
        before=self.cash();a=self.asset(120000);self.assertEqual(self.cash(),before-D('120000'));self.assertEqual(self.bal('1510'),D('120000.00'));self.assertEqual((a.cost,a.status,a.book_value),(D('120000.00'),'Active',D('120000.00')))
    def test_existing_asset_credits_owner_equity_without_touching_cash(self):
        before=self.cash();self.asset(50000,funding='existing');self.assertEqual(self.cash(),before);self.assertEqual(self.bal('3100'),D('-1050000.00'))
    def test_straight_line_depreciation_is_exact_and_stops_at_scrap_value(self):
        a=self.asset(100000,salvage=10000,life=7,on=date(2025,1,10))
        doc=advanced.depreciate(self.user,a,date(2025,3,31));a.refresh_from_db()
        self.assertEqual(a.accumulated,D('38571.43'));self.assertEqual(a.depreciated_through,date(2025,3,31));self.assertEqual((doc.posting_date,doc.total),(date(2025,3,31),D('38571.43')))
        self.assertIsNone(advanced.depreciate(self.user,a,date(2025,3,31)));self.assertIsNone(advanced.depreciate(self.user,a,date(2025,3,15)))     # nothing twice
        advanced.depreciate(self.user,a,self.today);a.refresh_from_db()
        self.assertEqual(a.accumulated,D('90000.00'));self.assertEqual(a.book_value,D('10000.00'));self.assertEqual(self.bal('6800'),D('90000.00'));self.assertEqual(self.bal('1590'),D('-90000.00'))
    def test_mid_month_dates_only_cover_whole_months(self):
        a=self.asset(120000,life=12,on=date(2025,1,10));advanced.depreciate(self.user,a,date(2025,2,20));a.refresh_from_db()
        self.assertEqual((a.accumulated,a.depreciated_through),(D('10000.00'),date(2025,1,31)))
    def test_reducing_balance(self):
        a=self.asset(100000,life=60,on=date(2025,1,10),method='Reducing balance',rate=24);advanced.depreciate(self.user,a,date(2025,2,28));a.refresh_from_db()
        self.assertEqual(a.accumulated,D('3960.00'))          # 2% of 100,000 then 2% of 98,000
        advanced.depreciate(self.user,a,date(2025,3,31));a.refresh_from_db();self.assertEqual(a.accumulated,D('5880.80'))
    def test_future_date_and_locked_period_are_refused(self):
        a=self.asset(120000)
        with self.assertRaisesMessage(ValidationError,'future'):advanced.depreciate(self.user,a,self.today+timedelta(days=40))
        Branch.objects.filter(pk=self.branch.pk).update(lock_through=date(2025,6,30))
        with self.assertRaisesMessage(ValidationError,'locked'):advanced.depreciate(self.user,a,date(2025,3,31))
        a.refresh_from_db();self.assertEqual(a.accumulated,0)
    def test_disposal_at_a_loss_and_at_a_gain_clears_the_asset_and_balances(self):
        a=self.asset(120000,life=12,on=date(2025,1,10))
        with self.assertRaisesMessage(ValidationError,'depreciation'):advanced.dispose_asset(self.user,a,date(2025,6,20),50000,'Cash')
        advanced.depreciate(self.user,a,date(2025,5,31));before=self.cash()
        advanced.dispose_asset(self.user,a,date(2025,6,20),D('60000'),'Cash');a.refresh_from_db()
        self.assertEqual((a.status,a.disposal_amount),('Disposed',D('60000.00')));self.assertEqual(self.cash(),before+D('60000'))
        self.assertEqual((self.bal('1510'),self.bal('1590')),(D('0'),D('0')));self.assertEqual(self.bal('6900'),D('10000.00'))       # book 70,000 sold for 60,000
        b=self.asset(120000,life=12,on=date(2025,1,10));advanced.depreciate(self.user,b,date(2025,5,31))
        advanced.dispose_asset(self.user,b,date(2025,6,20),D('80000'),'Cash');self.assertEqual(self.bal('4900'),D('-10000.00'))       # gain of 10,000
        with self.assertRaisesMessage(ValidationError,'already'):advanced.dispose_asset(self.user,b,date(2025,6,20),0,'Cash')
        for d in Document.objects.filter(kind='Journal Entry'):
            t=d.ledger.aggregate(x=Sum('debit'),y=Sum('credit'));self.assertEqual(t['x'],t['y'])
    def test_balance_sheet_still_balances_after_depreciation(self):
        a=self.asset(120000);advanced.depreciate(self.user,a,self.today)
        from . import financial_reports
        _,rows=financial_reports.run('Trial Balance',Branch.objects.filter(pk=self.branch.pk),date(2000,1,1),self.today)
        self.assertEqual(sum((r['Closing debit'] for r in rows),D(0)),sum((r['Closing credit'] for r in rows),D(0)))


class Deferrals(Base):
    def prepaid(self,amount=12000,months=3,on=date(2025,1,5)):
        advanced.add_deferral(self.user,self.branch,dict(kind='Prepaid',description='Quarter rent',amount=D(amount),start_date=on,months=months,expense_account=str(self.acc('6100').pk),balance_account=str(self.acc('1320').pk),mode='Cash'))
        return Deferral.objects.latest('created')
    def test_prepaid_is_released_month_by_month_until_fully_used(self):
        before=self.cash();p=self.prepaid();self.assertEqual((self.cash(),self.bal('1320')),(before-D('12000'),D('12000.00')))
        advanced.release(self.user,p,date(2025,2,28));p.refresh_from_db();self.assertEqual((p.released,p.status,self.bal('6100')),(D('8000.00'),'Open',D('8000.00')))
        self.assertIsNone(advanced.release(self.user,p,date(2025,2,28)))
        advanced.release(self.user,p,self.today);p.refresh_from_db();self.assertEqual((p.released,p.status,self.bal('6100'),self.bal('1320')),(D('12000.00'),'Complete',D('12000.00'),D('0')))
    def test_uneven_prepaid_never_over_releases(self):
        p=self.prepaid(10000,3);advanced.release(self.user,p,self.today);p.refresh_from_db();self.assertEqual(p.released,D('10000.00'))
    def accrual(self,start,reverse):
        advanced.add_deferral(self.user,self.branch,dict(kind='Accrual',description='October electricity',amount=D('8000'),start_date=start,reverse_on=reverse,expense_account=str(self.acc('6300').pk),balance_account=str(self.acc('2300').pk)))
        return Deferral.objects.latest('created')
    def test_accrual_books_the_expense_now_and_reverses_on_its_date(self):
        a=self.accrual(self.today-timedelta(days=20),self.today-timedelta(days=5));self.assertEqual((self.bal('6300'),self.bal('2300')),(D('8000.00'),D('-8000.00')))
        with self.assertRaisesMessage(ValidationError,'reverses on'):advanced.reverse_accrual(self.user,a,self.today-timedelta(days=10))
        doc=advanced.reverse_accrual(self.user,a);a.refresh_from_db();self.assertEqual((a.status,doc.posting_date,self.bal('6300'),self.bal('2300')),('Complete',self.today-timedelta(days=5),D('0'),D('0')))
        self.assertIsNone(advanced.reverse_accrual(self.user,a))
    def test_accrual_needs_a_reversal_date_after_the_accrual(self):
        with self.assertRaisesMessage(ValidationError,'reversal date'):self.accrual(self.today-timedelta(days=5),self.today-timedelta(days=9))
    def test_process_due_handles_prepaids_and_accruals_together(self):
        self.prepaid();self.accrual(self.today-timedelta(days=20),self.today-timedelta(days=5))
        docs=advanced.process_deferrals(self.user,self.branch,self.today);self.assertEqual(len(docs),2)
        self.assertEqual(advanced.process_deferrals(self.user,self.branch,self.today),[])


class Loans(Base):
    def loan(self,principal=100000,rate=12):
        advanced.add_loan(self.user,self.branch,dict(lender='Friend',principal=D(principal),annual_rate=D(rate),start_date=self.today-timedelta(days=60),term_months=12,liability_account=str(self.acc('2500').pk),interest_account=str(self.acc('6900').pk),mode='Cash'))
        return Loan.objects.latest('created')
    def test_loan_adds_cash_and_a_liability(self):
        before=self.cash();l=self.loan();self.assertEqual((self.cash(),self.bal('2500')),(before+D('100000'),D('-100000.00')));self.assertEqual(l.outstanding,D('100000.00'))
    def test_repayment_splits_principal_and_interest(self):
        l=self.loan();self.assertEqual(advanced.suggested_interest(l),D('1000.00'));before=self.cash()
        advanced.repay_loan(self.user,l,self.today-timedelta(days=30),D('30000'),D('1000'),'Cash');l.refresh_from_db()
        self.assertEqual((l.outstanding,l.interest_paid,self.cash(),self.bal('2500'),self.bal('6900')),(D('70000.00'),D('1000.00'),before-D('31000'),D('-70000.00'),D('1000.00')))
        self.assertEqual(advanced.suggested_interest(l),D('700.00'))
    def test_cannot_repay_more_than_is_owed_or_nothing_at_all(self):
        l=self.loan()
        with self.assertRaisesMessage(ValidationError,'still owed'):advanced.repay_loan(self.user,l,self.today,D('100000.01'),0,'Cash')
        with self.assertRaisesMessage(ValidationError,'principal and/or interest'):advanced.repay_loan(self.user,l,self.today,0,0,'Cash')
        l.refresh_from_db();self.assertEqual(l.principal_paid,0)


class Access(Base):
    def test_only_accounting_roles_can_use_these_pages_and_functions(self):
        u=self.sales_user();self.client.force_login(u)
        for name in ('assets','deferrals','loans'):self.assertEqual(self.client.get(reverse(name)).status_code,403,name)
        a=self.asset(120000)
        with self.assertRaises(PermissionDenied):advanced.depreciate(u,a,self.today)
        with self.assertRaises(PermissionDenied):advanced.add_loan(u,self.branch,dict(lender='X',principal=D(1),start_date=self.today,liability_account=str(self.acc('2500').pk),interest_account=str(self.acc('6900').pk),mode='Cash'))
    def test_pages_render_and_the_forms_post_once_even_if_submitted_twice(self):
        for name in ('assets','deferrals','loans'):self.assertContains(self.client.get(reverse(name)),'request_key')
        page=self.client.get(reverse('loans'));key=page.context['request_key']
        data=dict(branch=str(self.branch.pk),request_key=str(key),action='add',lender='Bank',principal='50000',annual_rate='10',start_date=str(self.today),term_months='12',liability_account=str(self.acc('2500').pk),interest_account=str(self.acc('6900').pk),mode='Cash',reference='')
        self.client.post(reverse('loans'),data);self.client.post(reverse('loans'),data);self.assertEqual(Loan.objects.count(),1)
    def test_asset_screen_runs_depreciation_and_reports_nothing_due(self):
        a=self.asset(120000,on=date(2025,1,10));key=str(self.client.get(reverse('assets')).context['request_key'])
        self.client.post(reverse('assets'),dict(branch=str(self.branch.pk),request_key=key,action='depreciate',through='2025-03-31'));a.refresh_from_db();self.assertEqual(a.accumulated,D('30000.00'))
        key=str(self.client.get(reverse('assets')).context['request_key'])
        page=self.client.post(reverse('assets'),dict(branch=str(self.branch.pk),request_key=key,action='depreciate',through='2025-03-31'),follow=True);self.assertContains(page,'Nothing is due')
    def test_manual_voucher_form_does_not_offer_the_system_voucher_types(self):
        types=[k for k,_ in self.client.get(reverse('voucher_new')).context['form'].fields['type'].choices]
        self.assertFalse(set(types)&s.SYSTEM_VOUCHERS);self.assertIn('expense',types)
