"""Phase C: cash flow statement, trial balance variants and year-end close."""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import coa,financial_reports as fr,vouchers,easy_retail,yearend,services as s


class Books(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());coa.install_standard(self.user);self.today=timezone.localdate();self.client.force_login(self.user)
        self.capital=Account.objects.get(code='COA-3100');self.rent=Account.objects.get(code='COA-6100');self.loan=Account.objects.get(code='COA-2500')
        # give the shop posting accounts their sections, as create_shop does for real shops
        sections=dict(receivable='Asset',payable='Liability',inventory='Asset',stock_received='Liability',sales='Income',cogs='Expense',expense='Expense',cash='Asset',bank='Asset')
        types=dict(receivable='Receivable',payable='Payable',inventory='Stock',stock_received='Stock Received But Not Billed',sales='Income Account',cogs='Cost of Goods Sold',expense='Expense Account')
        for code,root in sections.items():Account.objects.filter(code=code).update(root_type=root,**({'account_type':types[code]} if code in types else {}))
    def voucher(self,kind,account,amount,on=None,mode='Cash'):
        return vouchers.post(dict(branch=self.branch,type=kind,posting_date=on or self.today,amount=Decimal(amount),account=str(account.pk),mode=mode,description='Books test',reference='TX' if mode!='Cash' else ''),self.user)
    def phone(self,number,cost=50000):
        data=dict(branch=self.branch,party_name='Books Supplier',product=self.product,quantity=1,price=cost,asking_price=90000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,
                  posting_date=self.today,payment_status='full',method='Cash',imeis=[[imei(number)]])
        return easy_retail.purchase(data,self.user).reference.lines.get().device
    def sell(self,device,price,on=None):
        return easy_retail.sale(dict(branch=self.branch,party_name='Books Customer',phone=device,price=price,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=on or self.today,payment_status='full',method='Cash'),self.user)
    def report(self,name,start=None,end=None):
        return fr.run(name,Branch.objects.filter(pk=self.branch.pk),start or self.today-timedelta(days=365),end or self.today)
    def lines(self,name):return {r['Line']:r['Amount'] for r in self.report(name)[1]}


class CashFlow(Books):
    def test_statement_reconciles_to_the_actual_change_in_cash(self):
        self.voucher('capital',self.capital,300000);self.voucher('loan_in',self.loan,100000)
        device=self.phone(91,cost=50000);self.sell(device,70000);self.voucher('expense',self.rent,8000)
        lines=self.lines('Cash Flow Statement')
        self.assertEqual(lines['Unexplained difference (should be zero)'],Decimal('0'))
        self.assertEqual(lines['Net profit / loss for the period'],Decimal('12000'))          # 70,000 sale - 50,000 cost - 8,000 rent
        self.assertEqual(lines['Net cash from financing activities'],Decimal('400000'))
        self.assertEqual(lines['Net change in cash and bank'],Decimal('400000')-Decimal('50000')+Decimal('70000')-Decimal('8000'))
        self.assertEqual(lines['Cash and bank at end (calculated)'],lines['Cash and bank at end (ledger)'])
    def test_credit_sale_shows_up_as_receivables_not_cash(self):
        self.voucher('capital',self.capital,100000);device=self.phone(92,cost=40000)
        easy_retail.sale(dict(branch=self.branch,party_name='Credit Customer',phone=device,price=60000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today,payment_status='unpaid',method='Cash'),self.user)
        lines=self.lines('Cash Flow Statement');self.assertEqual(lines['Unexplained difference (should be zero)'],Decimal('0'))
        self.assertEqual(lines['Change in customer receivables'],Decimal('-60000'));self.assertEqual(lines['Net profit / loss for the period'],Decimal('20000'))
    def test_report_page_and_summary_render(self):
        self.voucher('capital',self.capital,5000)
        page=self.client.get(reverse('report',args=['Cash Flow Statement']));self.assertEqual(page.status_code,200);self.assertContains(page,'Net cash from financing activities')


class TrialBalances(Books):
    def test_adjusted_includes_owner_journals_and_unadjusted_does_not_and_both_balance(self):
        self.voucher('capital',self.capital,50000)
        doc=s.new_doc('Journal Entry',self.branch,status='Submitted',data={'manual_journal':{'version':1}})
        s.entry(doc,self.rent,debit=Decimal('700'));s.entry(doc,Account.objects.get(code='COA-2300'),credit=Decimal('700'))
        def totals(name):
            rows=self.report(name)[1];return sum(r['Closing debit'] for r in rows),sum(r['Closing credit'] for r in rows),{r['Account code']:r for r in rows}
        ud,uc,urows=totals('Unadjusted Trial Balance');ad,ac,arows=totals('Adjusted Trial Balance')
        self.assertEqual(ud,uc);self.assertEqual(ad,ac);self.assertNotIn('COA-6100',urows);self.assertEqual(arows['COA-6100']['Closing debit'],Decimal('700'))


class YearEnd(Books):
    def setUp(self):
        super().setUp();self.start=self.today-timedelta(days=60);self.end=self.today-timedelta(days=30)
        self.voucher('capital',self.capital,200000,on=self.start-timedelta(days=1))
        device=self.phone(93,cost=50000);self.sell(device,90000,on=self.start+timedelta(days=5));self.voucher('expense',self.rent,10000,on=self.start+timedelta(days=6))
        self.retained=Account.objects.get(code='COA-3900')
    def test_closing_zeroes_income_and_expense_into_retained_earnings_and_locks(self):
        doc,profit=yearend.close_year(self.user,self.branch,self.start,self.end,self.retained)
        self.assertEqual(profit,Decimal('30000'));t=doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(t['d'],t['c'])
        self.assertEqual(s.account_balance(self.branch,self.retained),Decimal('-30000'))                  # credit balance
        self.branch.refresh_from_db();self.assertEqual(self.branch.lock_through,self.end)
        for root in ('Income','Expense'):
            total=LedgerEntry.objects.filter(branch=self.branch,account__root_type=root).aggregate(d=Sum('debit'),c=Sum('credit'))
            self.assertEqual((total['d'] or 0)-(total['c'] or 0),0)
    def test_profit_and_loss_still_shows_the_closed_year_and_balance_sheet_balances(self):
        yearend.close_year(self.user,self.branch,self.start,self.end,self.retained)
        rows=self.report('Profit and Loss Statement',self.start,self.end)[1]
        income=sum(r['Amount'] for r in rows if r['Section']=='Income');expense=sum(r['Amount'] for r in rows if r['Section']=='Expense')
        self.assertEqual((income,expense),(Decimal('90000'),Decimal('60000')))                                # sales 90k; cost 50k + rent 10k
        bs=self.report('Balance Sheet',end=self.today)[1];totals={root:sum(r['Amount'] for r in bs if r['Section']==root) for root in ('Asset','Liability','Equity')}
        self.assertEqual(totals['Asset'],totals['Liability']+totals['Equity']);self.assertNotIn('Unclosed profit / loss',[r['Account'] for r in bs])
    def test_cannot_close_twice_or_with_the_wrong_account_or_nothing_to_close(self):
        yearend.close_year(self.user,self.branch,self.start,self.end,self.retained)
        with self.assertRaisesMessage(ValidationError,'already been closed'):yearend.close_year(self.user,self.branch,self.start,self.end,self.retained)
        with self.assertRaisesMessage(ValidationError,'equity account'):yearend.close_year(self.user,self.branch,self.start,self.end,self.rent)
        with self.assertRaisesMessage(ValidationError,'already been closed'):yearend.close_year(self.user,self.branch,self.today-timedelta(days=400),self.today-timedelta(days=300),self.retained)   # older periods cannot be closed after a later close
    def test_a_period_with_no_income_or_expense_has_nothing_to_close(self):
        with self.assertRaisesMessage(ValidationError,'no income or expense'):yearend.close_year(self.user,self.branch,self.today-timedelta(days=400),self.today-timedelta(days=300),self.retained)
    def test_screen_previews_then_closes_and_requires_the_accounting_permission(self):
        params=dict(branch=str(self.branch.pk),from_date=str(self.start),to_date=str(self.end),retained=str(self.retained.pk))
        page=self.client.get(reverse('year_end'),params);self.assertContains(page,'Shop rent');self.assertContains(page,'Net profit moved to')
        self.client.post(reverse('year_end'),params);self.branch.refresh_from_db();self.assertEqual(self.branch.lock_through,self.end)
        from django.contrib.auth import get_user_model
        clerk=get_user_model().objects.create_user('clerk-ye',password='Unrelated-test-password-729!');Staff.objects.create(code='CLK-YE',name='Clerk',branch=self.branch,user=clerk,role=Role.objects.get(code='sales'))
        self.client.force_login(clerk);self.assertEqual(self.client.get(reverse('year_end')).status_code,403)
