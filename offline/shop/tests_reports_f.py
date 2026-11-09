"""Phase F: sales-by-period, expense, low-stock, IMEI, exchange, transfer and branch-comparison reports."""
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied
from django.urls import reverse
from . import reports
from .models import *
from .tests import imei
from .tests_credit import CreditBase
from . import easy_retail,exchange,report_extra
from .tests_exchange import ExchangeBase


class ExtraReports(CreditBase):
    def rep(self,name,**params):
        return reports.run(name,self.user,params)
    def test_every_new_report_is_listed_runs_on_an_empty_shop_and_has_columns(self):
        for name in report_extra.COLUMNS:
            columns,rows=self.rep(name);self.assertEqual(columns,report_extra.COLUMNS[name],name)
            if name!='Branch Comparison':self.assertEqual(rows,[],name)
            else:self.assertEqual([r['Net sales'] for r in rows],[0])
    def test_daily_monthly_annual_sales_group_and_split_collected_from_unpaid(self):
        self.sale(60000,paid='full');self.sale(40000,paid='part',amount=10000);self.sale(20000)
        _,daily=self.rep('Daily Sales');row=daily[0]
        self.assertEqual((row['Invoices'],row['Phones sold'],row['Sales total'],row['Still unpaid'],row['Collected']),(3,3,Decimal('120000.00'),Decimal('50000.00'),Decimal('70000.00')))
        _,monthly=self.rep('Monthly Sales');self.assertEqual(monthly[0]['Month'],f'{self.today:%Y-%m}');self.assertEqual(monthly[0]['Sales total'],Decimal('120000.00'))
        _,annual=self.rep('Annual Sales');self.assertEqual(annual[0]['Year'],str(self.today.year))
    def test_sales_outside_the_dates_are_left_out(self):
        self.sale(60000,paid='full',on=self.today-timedelta(days=40))
        _,rows=self.rep('Daily Sales',from_date=str(self.today-timedelta(days=5)),to_date=str(self.today));self.assertEqual(rows,[])
    def test_branch_comparison_matches_ledger_profit_and_ranks_by_net_profit(self):
        self.phone(50000);self.sale(60000,paid='full')
        _,rows=self.rep('Branch Comparison');row=next(r for r in rows if r['Shop']==self.branch.name)
        self.assertEqual(row['Net sales'],Decimal('60000.00'));self.assertEqual(row['Gross profit'],Decimal('10000.00'));self.assertEqual(row['Phones sold'],1)
        self.assertEqual(row['Gross margin %'],Decimal('16.7'));self.assertEqual(row['Cash and bank'],Decimal('-40000.00'))     # two phones bought for 50,000 cash, one sold for 60,000
        columns,pl=reports.run('Profit and Loss Statement',self.user,{});self.assertIn(Decimal('60000.00'),[r.get('Amount') for r in pl])
    def test_branch_comparison_shows_what_customers_owe(self):
        self.sale(60000);_,rows=self.rep('Branch Comparison');self.assertEqual(rows[0]['Customers owe'],Decimal('60000.00'))
    def test_imei_report_lists_numbers_and_filters_by_status(self):
        device=self.phone();_,rows=self.rep('IMEI Report');mine=next(r for r in rows if r['Device']==device.code)
        self.assertEqual(mine['IMEI 1'],device.imeis.get().number);self.assertEqual(mine['Status'],'Available')
        _,sold=self.rep('IMEI Report',status='Sold');self.assertNotIn(device.code,[r['Device'] for r in sold])
    def test_low_stock_report_lists_accessories_at_or_below_reorder_level(self):
        Product.objects.filter(pk=self.product.pk).update(serialized=False,stock_item=True,reorder_level=5)
        _,rows=self.rep('Low Stock');self.assertTrue(any(r['Item']==self.product.name for r in rows))
    def test_expense_report_is_limited_to_costs_staff_and_pages_render_and_export(self):
        for name in ('Daily Sales','Branch Comparison','Expense Report','IMEI Report'):
            page=self.client.get(reverse('report',args=[name]));self.assertEqual(page.status_code,200,name)
class ExchangeReport(ExchangeBase):
    def test_exchange_report_shows_trade_in_difference_and_margin_after_resale(self):
        exchange.post(self.form(150000),[self.incoming(65000)],self.user)
        _,rows=reports.run('Exchange Report',self.user,{});row=rows[0]
        self.assertEqual((row['New phone price'],row['Trade-in value'],row['Cash difference'],row['Old phones sold']),(Decimal('150000.00'),Decimal('65000.00'),Decimal('85000.00'),'0 of 1'))
        old=IMEI.objects.get(number=imei(402)).device;old.status='Available';old.save()
        easy_retail.sale(dict(branch=self.branch,party_name='Eager',phone=old,price=80000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today,payment_status='full',method='Cash'),self.user)
        _,rows=reports.run('Exchange Report',self.user,{});self.assertEqual((rows[0]['Old phones sold'],rows[0]['Old phone margin']),('1 of 1',Decimal('15000.00')))


class DashboardOverview(CreditBase):
    def get(self,**q):return self.client.get(reverse('dashboard'),q)
    def test_overview_figures_agree_with_the_branch_comparison_and_pl_report(self):
        self.sale(60000,paid='full');self.sale(40000,paid='part',amount=10000);self.sale(30000)
        o=self.get().context['overview'];self.assertEqual(o['income'],Decimal('130000.00'));self.assertEqual(o['phones'],3)
        self.assertEqual((o['mix']['cash_n'],o['mix']['part_n'],o['mix']['credit_n']),(1,1,1));self.assertEqual(o['receivable'],Decimal('60000.00'))
        from . import financial_reports
        _,pl=reports.run('Profit and Loss Statement',self.user,dict(from_date=str(o['start']),to_date=str(o['end'])));summary=dict(financial_reports.summary('Profit and Loss Statement',pl))
        self.assertEqual((o['income'],o['net']),(summary['Income'],summary['Net profit / loss']))
    def test_date_range_and_custom_dates_change_the_period_and_exclude_old_sales(self):
        self.sale(50000,paid='full',on=self.today-timedelta(days=45))
        self.assertEqual(self.get().context['overview']['income'],0)
        o=self.get(range='custom',from_date=str(self.today-timedelta(days=60)),to_date=str(self.today)).context['overview'];self.assertEqual(o['income'],Decimal('50000.00'))
        self.assertEqual(self.get(range='7d').context['overview']['start'],self.today-timedelta(days=6))
        self.assertEqual(self.get(range='custom',from_date='nonsense').context['overview']['range'],'month')       # bad input falls back
    def test_best_sellers_brands_and_profitable_phones_come_from_sold_devices(self):
        self.sale(60000,paid='full');o=self.get().context['overview']
        self.assertEqual((o['models'][0]['n'],o['models'][0]['revenue'],o['models'][0]['profit']),(1,Decimal('60000.00'),Decimal('10000.00')));self.assertEqual(o['profitable'][0]['profit'],Decimal('10000.00'))
    def test_page_renders_with_click_through_links_and_shop_selection(self):
        self.sale(60000,paid='full');page=self.get(branch=str(self.branch.pk))
        self.assertContains(page,'Total sales');self.assertContains(page,'/query-report/Daily%20Sales');self.assertContains(page,'Cash sales')