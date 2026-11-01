"""Reconcile statements using capital, sales, returns, overhead and corrections."""
import csv,io
from datetime import date
from decimal import Decimal
from urllib.parse import parse_qs,urlsplit
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import Account,Branch,Document,LedgerEntry,ShopSettings
from .tests import fixtures
from . import services as s,financial_reports as financial


class FinancialReports(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
        self.config=ShopSettings.objects.get(branch=self.branch)
        self.accounts={key:Account.objects.get(pk=value) for key,value in self.config.accounts.items()}
        self.equity=Account.objects.create(code='CAPITAL',name='Owner capital',account_type='Equity')
        self.post('2026-09-30',self.accounts['cash'],self.equity,1000)
        self.post('2026-10-05',self.accounts['cash'],self.accounts['sales'],300)
        self.post('2026-10-06',self.accounts['expense'],self.accounts['cash'],100)
        self.post('2026-10-07',self.accounts['sales'],self.accounts['cash'],60)
        self.post('2026-10-08',self.accounts['cash'],self.accounts['expense'],20)
        self.post('2026-11-02',self.accounts['cash'],self.accounts['sales'],700)
    def post(self,day,debit,credit,amount,branch=None):
        branch=branch or self.branch;doc=s.new_doc('Journal Entry',branch,posting_date=date.fromisoformat(day));doc.status='Submitted';doc.save()
        LedgerEntry.objects.create(document=doc,branch=branch,posting_date=doc.posting_date,account=debit,debit=amount)
        LedgerEntry.objects.create(document=doc,branch=branch,posting_date=doc.posting_date,account=credit,credit=amount)
        return doc
    def page(self,name,**params):
        filters={'to_date':'2026-10-31'}
        if name!='Balance Sheet':filters['from_date']='2026-10-01'
        return self.client.get(reverse('report',args=[name]),{**filters,**params})
    def totals(self,response):return {item['label']:item['value'] for item in response.context['statement_summary']}
    def test_profit_includes_returns_and_expense_refunds_but_not_capital_or_future(self):
        response=self.page('Profit and Loss Statement');self.assertEqual(response.status_code,200)
        self.assertEqual(self.totals(response),{'Income':'240.00','Expenses':'80.00','Net profit / loss':'160.00'})
        self.assertFalse(response.context['report_totals']);self.assertNotContains(response,'Owner capital')
    def test_balance_sheet_reconciles_including_unclosed_earnings(self):
        response=self.page('Balance Sheet');totals=self.totals(response)
        self.assertEqual(totals,{'Assets':'1,160.00','Liabilities':'0.00','Equity incl. unclosed earnings':'1,160.00','Assets less liabilities and equity':'0.00'})
        self.assertContains(response,'Unclosed profit / loss');self.assertFalse(response.context['report_totals'])
    def test_trial_balance_opening_period_and_closing_reconcile(self):
        response=self.page('Trial Balance');columns=response.context['columns'];rows=[dict(zip(columns,row)) for row in response.context['rows']]
        sums={key:sum(row[key] for row in rows) for key in financial.columns('Trial Balance')[3:]}
        self.assertEqual(sums,{'Opening debit':1000,'Opening credit':1000,'Period debit':480,'Period credit':480,'Closing debit':1240,'Closing credit':1240})
        self.assertEqual(self.totals(response)['Debit less credit'],'0.00')
    def test_closed_profit_is_not_added_to_equity_twice(self):
        self.post('2026-10-31',self.accounts['sales'],self.equity,240)
        self.post('2026-10-31',self.equity,self.accounts['expense'],80)
        response=self.page('Balance Sheet');self.assertNotContains(response,'Unclosed profit / loss')
        self.assertEqual(self.totals(response)['Equity incl. unclosed earnings'],'1,160.00')
    def test_imbalance_is_visible_and_never_auto_corrected(self):
        doc=Document.objects.first();LedgerEntry.objects.create(document=doc,branch=self.branch,posting_date=date(2026,10,10),account=self.accounts['cash'],debit=5)
        count=LedgerEntry.objects.count()
        self.assertEqual(self.totals(self.page('Balance Sheet'))['Assets less liabilities and equity'],'5.00')
        self.assertEqual(self.totals(self.page('Trial Balance'))['Debit less credit'],'5.00')
        self.assertEqual(LedgerEntry.objects.count(),count)
    def test_unclassified_account_blocks_statements_and_stays_visible_in_trial_balance(self):
        unknown=Account.objects.create(code='UNKNOWN',name='Review classification')
        self.post('2026-10-09',unknown,self.accounts['cash'],25)
        for name in ('Balance Sheet','Profit and Loss Statement'):
            response=self.page(name);self.assertEqual(response.status_code,400);self.assertContains(response,'UNKNOWN',status_code=400)
        response=self.page('Trial Balance');self.assertEqual(response.status_code,200);self.assertContains(response,'Unclassified')
    def test_unclassified_old_account_does_not_block_a_later_profit_period(self):
        unknown=Account.objects.create(code='UNKNOWN',name='Review classification');self.post('2026-10-09',unknown,self.accounts['cash'],25)
        self.assertEqual(self.page('Profit and Loss Statement',from_date='2026-11-01',to_date='2026-11-30').status_code,200)
    def test_original_root_classification_precedes_generic_tax_type(self):
        asset=Account.objects.create(code='INPUT-TAX',name='Input tax',account_type='Tax',source={'root_type':'Asset'})
        self.post('2026-10-09',asset,self.accounts['cash'],25)
        self.assertEqual(self.page('Balance Sheet').status_code,200)
        self.assertEqual(financial.classifications()[asset.pk][1],'Asset')
    def test_ambiguous_fallback_classification_is_not_guessed(self):
        self.config.accounts['sales']=self.config.accounts['cash'];self.config.accounts['expense']=self.config.accounts['sales'];self.config.save()
        # Explicit Cash account type remains an asset despite conflicting mappings.
        self.assertEqual(financial.classifications()[self.accounts['cash'].pk][1],'Asset')
        self.accounts['cash'].account_type='';self.accounts['cash'].save()
        self.assertEqual(financial.classifications()[self.accounts['cash'].pk][1],'Unclassified')
    def test_shop_filter_excludes_other_branch_and_combined_statements_add_both(self):
        other=Branch.objects.create(code='OTHER',name='Other branch',company='Other company')
        self.post('2026-10-09',self.accounts['cash'],self.accounts['sales'],500,other)
        self.assertEqual(self.totals(self.page('Profit and Loss Statement'))['Net profit / loss'],'660.00')
        self.assertEqual(self.totals(self.page('Profit and Loss Statement',branch=str(self.branch.pk)))['Net profit / loss'],'160.00')
    def test_sales_role_cannot_open_or_export_statements(self):
        user=get_user_model().objects.create_user('sales-report');self.seller.user=user;self.seller.save();self.client.force_login(user)
        for name in financial.NAMES:
            self.assertEqual(self.page(name).status_code,403);self.assertEqual(self.page(name,format='csv').status_code,403)
        center=self.client.get(reverse('report_center'));self.assertNotContains(center,'Balance Sheet');self.assertNotContains(center,'Trial Balance')
    def test_purchasing_role_statements_are_scoped_to_assigned_shop(self):
        other=Branch.objects.create(code='OTHER',name='Other',company='Other');self.post('2026-10-09',self.accounts['cash'],self.accounts['sales'],500,other)
        user=get_user_model().objects.create_user('buyer-report');self.buyer.user=user;self.buyer.save();self.client.force_login(user)
        self.assertEqual(self.totals(self.page('Profit and Loss Statement'))['Net profit / loss'],'160.00')
        self.assertEqual(self.page('Profit and Loss Statement',branch=str(other.pk)).status_code,400)
    def test_row_search_keeps_statement_summary_and_export_matches_filtered_rows(self):
        response=self.page('Profit and Loss Statement',search='sales');self.assertEqual(len(response.context['rows']),1)
        self.assertEqual(self.totals(response)['Net profit / loss'],'160.00');self.assertContains(response,'Row search only filters the table')
        export=self.page('Profit and Loss Statement',search='sales',format='csv');rows=list(csv.DictReader(io.StringIO(export.content.decode())))
        self.assertEqual(len(rows),1);self.assertEqual(Decimal(rows[0]['Amount']),240)
    def test_account_drilldown_keeps_shop_account_and_period(self):
        response=self.page('Profit and Loss Statement',branch=str(self.branch.pk))
        index=response.context['columns'].index('Account')
        cell=next(row[index] for row in response.context['display_rows'] if row[index]['value']=='sales')
        self.assertTrue(cell['url']);params=parse_qs(urlsplit(cell['url']).query)
        self.assertEqual(params['account'],[str(self.accounts['sales'].pk)]);self.assertEqual(params['branch'],[str(self.branch.pk)])
        self.assertEqual(params['from_date'],['2026-10-01']);self.assertEqual(self.client.get(cell['url']).status_code,200)
    def test_reversal_entries_are_included_even_when_original_is_cancelled(self):
        original=self.post('2026-10-09',self.accounts['expense'],self.accounts['cash'],75);original.status='Cancelled';original.save()
        self.post('2026-10-10',self.accounts['cash'],self.accounts['expense'],75)
        self.assertEqual(self.totals(self.page('Profit and Loss Statement'))['Net profit / loss'],'160.00')
    def test_loss_reduces_balance_sheet_equity(self):
        self.post('2026-10-09',self.accounts['expense'],self.accounts['cash'],300)
        response=self.page('Balance Sheet');self.assertContains(response,'-140.00');self.assertEqual(self.totals(response)['Assets less liabilities and equity'],'0.00')
    def test_empty_period_and_invalid_dates(self):
        response=self.page('Profit and Loss Statement',from_date='2027-01-01',to_date='2027-01-31')
        self.assertEqual(response.status_code,200);self.assertEqual(self.totals(response)['Net profit / loss'],'0.00')
        self.assertEqual(self.page('Trial Balance',from_date='2026-12-01').status_code,400)
