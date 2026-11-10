"""Phase C: expense, owner capital/drawings and loan vouchers."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from . import coa,services as s,vouchers


class VoucherBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());coa.install_standard(self.user)
        self.rent=Account.objects.get(code='COA-6100');self.capital=Account.objects.get(code='COA-3100');self.drawings=Account.objects.get(code='COA-3300');self.loan=Account.objects.get(code='COA-2500')
    def data(self,kind,account,amount,mode='Cash',**extra):
        return dict(branch=self.branch,type=kind,posting_date=timezone.localdate(),amount=Decimal(amount),account=str(account.pk),mode=mode,description='Test voucher',**extra)
    def cash(self):return s.account_balance(self.branch,Account.objects.get(pk=ShopSettings.objects.get(branch=self.branch).payment_methods['Cash']))
    def balanced(self,doc):
        t=doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(t['d'],t['c']);self.assertTrue(t['d'])


class Postings(VoucherBase):
    def test_owner_capital_puts_cash_in_then_rent_takes_it_out(self):
        cap=vouchers.post(self.data('capital',self.capital,100000),self.user);self.balanced(cap);self.assertEqual(self.cash(),Decimal('100000'))
        rent=vouchers.post(self.data('expense',self.rent,30000),self.user);self.balanced(rent);self.assertEqual(self.cash(),Decimal('70000'))
        self.assertEqual(set(rent.ledger.values_list('account__code',flat=True))&{'COA-6100'},{'COA-6100'})
        self.assertEqual(rent.ledger.get(account=self.rent).debit,Decimal('30000.00'))
    def test_drawings_and_loan_flows_post_to_the_right_sides(self):
        vouchers.post(self.data('loan_in',self.loan,50000),self.user);self.assertEqual(self.cash(),Decimal('50000'))
        self.assertEqual(s.account_balance(self.branch,self.loan),Decimal('-50000'))        # liability: credit balance
        vouchers.post(self.data('drawings',self.drawings,10000),self.user);vouchers.post(self.data('loan_out',self.loan,20000),self.user)
        self.assertEqual(self.cash(),Decimal('20000'));self.assertEqual(s.account_balance(self.branch,self.loan),Decimal('-30000'))
    def test_bank_vouchers_need_a_reference_and_cash_never_goes_negative(self):
        vouchers.post(self.data('capital',self.capital,5000),self.user)
        with self.assertRaisesMessage(ValidationError,'cannot be paid from it'):vouchers.post(self.data('expense',self.rent,9000),self.user)
        self.assertEqual(self.cash(),Decimal('5000'));self.assertEqual(Document.objects.filter(kind='Voucher').count(),1)
        self.assertTrue(vouchers.post(self.data('capital',self.capital,1000,mode='Bank Transfer',reference='TX-1'),self.user).pk)
    def test_wrong_account_section_control_accounts_and_missing_description_are_rejected(self):
        with self.assertRaisesMessage(ValidationError,'expense account'):vouchers.post(self.data('expense',self.capital,100),self.user)
        control=Account.objects.create(code='CTRL-PAY',name='Supplier control',account_type='Payable',root_type='Liability')
        with self.assertRaisesMessage(ValidationError,'control account'):vouchers.post(self.data('loan_in',control,100),self.user)
        bad=self.data('capital',self.capital,100);bad['description']=' '
        with self.assertRaises(ValidationError):vouchers.post(bad,self.user)
    def test_cancelling_a_voucher_restores_cash(self):
        vouchers.post(self.data('capital',self.capital,40000),self.user);rent=vouchers.post(self.data('expense',self.rent,15000),self.user)
        s.cancel(rent,self.user);self.assertEqual(self.cash(),Decimal('40000'))


class Permissions(VoucherBase):
    def test_shop_login_records_expenses_and_owner_money_but_not_loans(self):
        from . import shop_accounts
        login=shop_accounts.create_login(self.branch,'voucher-shop','Strong-pass-for-V-77!');self.client.force_login(login)
        types=[k for k,_ in self.client.get(reverse('voucher_new')).context['form'].fields['type'].choices]
        self.assertEqual(sorted(types),['capital','drawings','expense','income'])
        self.client.force_login(self.user);self.assertIn('loan_in',[k for k,_ in self.client.get(reverse('voucher_new')).context['form'].fields['type'].choices])


class Screen(VoucherBase):
    def test_form_posts_once_even_if_resubmitted_and_lists_the_voucher(self):
        self.client.force_login(self.user)
        body=dict(request_key='3b1c9d5e-1111-4222-8333-555566667777',branch=str(self.branch.pk),type='capital',posting_date=str(timezone.localdate()),amount='25000',account=str(self.capital.pk),mode='Cash',description='Opening cash')
        for _ in range(2):self.client.post(reverse('voucher_new'),body)
        self.assertEqual(Document.objects.filter(kind='Voucher').count(),1)
        page=self.client.get(reverse('vouchers'));self.assertContains(page,'Owner capital introduced');self.assertContains(page,'25,000.00')
    def test_form_shows_the_account_groups(self):
        page=self.client.get(reverse('voucher_new')) if self.client.force_login(self.user) is None else None
        self.assertContains(page,'Expense accounts');self.assertContains(page,'Shop rent')
    def test_open_till_tracks_cash_vouchers(self):
        vouchers.post(self.data('capital',self.capital,5000),self.user)
        shift=s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'1000'}),self.user)
        rent=vouchers.post(self.data('expense',self.rent,200),self.user)
        rent.refresh_from_db();self.assertEqual(rent.pos_shift_id,shift.pk);self.assertEqual(rent.data['till_cash'],'-200.00')
        self.assertEqual(Decimal(s.till_balance(shift)['cash_paid']),Decimal('200.00'))
