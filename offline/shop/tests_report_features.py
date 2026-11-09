"""Reporting controls must preserve dates, permissions, money and exported rows."""
import csv,io
from datetime import date
from decimal import Decimal
from unittest.mock import patch
from urllib.parse import parse_qs,urlsplit
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from .models import *
from .tests import fixtures,imei
from . import services as s,report_catalog as catalog,report_presentation as presentation


class ReportFeatures(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
        self.item=Product.objects.create(code='SERVICE',name='Service',stock_item=False)
    def sale(self,amount='100',customer=None):
        doc=s.new_doc('Sales Invoice',self.branch,customer=customer or self.customer,seller=self.seller,posting_date=date(2026,10,28))
        Line.objects.create(document=doc,product=self.item,quantity=1,rate=amount);return s.submit(doc,self.user)
    def page(self,name,**params):return self.client.get(reverse('report',args=[name]),params)
    def sales_user(self,buy=False):
        user=get_user_model().objects.create_user('report-user');self.seller.user=user;self.seller.can_buy=buy;self.seller.save();self.client.force_login(user);return user
    def test_catalog_exposes_original_labels_all_reports_and_stock_ledger(self):
        response=self.client.get(reverse('report_center'));self.assertEqual(response.status_code,200);self.assertEqual(response.context['report_count'],27)
        for label in ('Phone Stock','Stock Age &amp; Prices','Device Costs &amp; Margin','Customer Balances','Supplier Balances','Stock Ledger'):
            self.assertContains(response,label)
    def test_catalog_search_accepts_original_report_name(self):
        response=self.client.get(reverse('report_center'),{'search':'phone stock'});self.assertEqual(response.context['report_count'],1)
        self.assertEqual(response.context['report_groups'][0]['reports'][0]['name'],'Mobile Inventory')
    def test_staff_reports_are_not_in_the_menu_because_each_shop_has_one_account(self):
        sidebar=self.client.get(reverse('dashboard')).content.decode().split('<aside',1)[1].split('</aside>',1)[0]
        for name in ('Staff Activity','Staff Summary'):self.assertNotIn(reverse('report',args=[name]),sidebar)
    @patch('django.utils.timezone.localdate',return_value=date(2026,11,1))
    def test_month_boundary_empty_report_offers_all_history_and_restores_october(self,clock):
        sale=self.sale();response=self.page('Sales Register');self.assertEqual(response.context['rows'],[])
        self.assertContains(response,'Show all history, including earlier months')
        all_history=next(item['url'] for item in response.context['date_presets'] if item['label']=='All history')
        response=self.client.get(reverse('report',args=['Sales Register'])+all_history);self.assertEqual(len(response.context['rows']),1);self.assertContains(response,sale.code)
    @patch('django.utils.timezone.localdate',return_value=date(2026,11,1))
    def test_last_month_shortcut_uses_complete_previous_month(self,clock):
        response=self.page('Sales Register');preset=next(item['url'] for item in response.context['date_presets'] if item['label']=='Last month');values=parse_qs(urlsplit(preset).query)
        self.assertEqual(values['from_date'],['2026-10-01']);self.assertEqual(values['to_date'],['2026-10-31'])
    def test_date_shortcuts_remain_available_when_dates_cleared(self):
        response=self.page('Sales Register',search='no match');self.assertTrue(response.context['all_history_url'])
    def test_numeric_sort_search_and_csv_use_identical_filtered_rows(self):
        one=self.sale('100');two=self.sale('20');params=dict(from_date='2000-01-01',sort_by='Total',sort_dir='desc')
        response=self.page('Sales Register',**params);columns=response.context['columns'];index=columns.index('Invoice')
        self.assertEqual([row[index] for row in response.context['rows']],[one.code,two.code])
        export=self.page('Sales Register',format='csv',**params);rows=list(csv.DictReader(io.StringIO(export.content.decode())))
        self.assertEqual([row['Invoice'] for row in rows],[one.code,two.code])
        filtered=self.page('Sales Register',search=two.code,**params);self.assertEqual(len(filtered.context['rows']),1)
        footer=dict(zip(filtered.context['columns'],[cell['value'] for cell in filtered.context['report_totals']]));self.assertEqual(footer['Total'],'20.00')
    def test_numeric_sort_does_not_sort_money_as_text(self):
        result=presentation.select_rows(['Amount'],[{'Amount':Decimal(2)},{'Amount':Decimal(10)}],{'sort_by':'Amount','sort_dir':'desc'})
        self.assertEqual(result[0]['Amount'],10)
    def test_sort_link_retains_applied_filters(self):
        response=self.page('Sales Register',from_date='2026-10-01',to_date='2026-10-31',search='Customer')
        header=next(item for item in response.context['report_headers'] if item['label']=='Total');query=parse_qs(urlsplit(header['url']).query)
        self.assertEqual(query['search'],['Customer']);self.assertEqual(query['from_date'],['2026-10-01']);self.assertEqual(query['sort_by'],['Total'])
    def test_source_invoice_links_work_for_sales_users(self):
        sale=self.sale();self.sales_user();response=self.page('Sales Register',from_date='2000-01-01')
        self.assertContains(response,reverse('detail',args=[sale.pk]));self.assertEqual(self.client.get(reverse('detail',args=[sale.pk])).status_code,200)
    def test_device_report_links_to_the_scoped_device(self):
        phone=Device.objects.create(code='REPORT-PHONE',branch=self.branch,product=self.product,warehouse=self.warehouse,purchase_cost=50000,asking_price=65000)
        IMEI.objects.create(number=imei(99),device=phone,slot=1)
        response=self.page('Mobile Inventory');self.assertContains(response,reverse('device',args=[phone.pk]))
    def test_staff_activity_never_adds_sales_and_collections_into_one_total(self):
        sale=self.sale();s.submit(s.new_doc('Payment Entry',self.branch,customer=self.customer,handler=self.handler,reference=sale,data={'direction':'Receive','mode':'Cash','amount':'100'}),self.user)
        response=self.page('Staff Activity',from_date='2000-01-01');self.assertFalse(response.context['report_totals']);self.assertContains(response,'must not be added together')
    def test_print_control_and_applied_period_are_present(self):
        response=self.page('Sales Register',from_date='2026-10-01',to_date='2026-10-31')
        self.assertContains(response,'Print / Save PDF');self.assertContains(response,'2026-10-01');self.assertContains(response,'Dates use Pakistan time')
    def test_csv_retains_formula_injection_protection(self):
        customer=Party.objects.create(code='FORMULA',name='=unsafe',kind='Customer');self.sale(customer=customer)
        response=self.page('Sales Register',from_date='2000-01-01',format='csv');self.assertIn("'=unsafe",response.content.decode())
    def test_negative_credit_totals_keep_their_sign(self):
        s.submit(s.new_doc('Payment Entry',self.branch,customer=self.customer,handler=self.handler,data={'direction':'Receive','mode':'Cash','amount':'50'}),self.user)
        response=self.page('Accounts Receivable');footer=dict(zip(response.context['columns'],[cell['value'] for cell in response.context['report_totals']]));self.assertEqual(footer['Outstanding'],'-50.00')
    def test_as_of_reports_have_as_of_shortcuts_without_fake_from_date(self):
        response=self.page('Stock Balance');self.assertEqual([item['label'] for item in response.context['date_presets']],['As of today','End of last month'])
    def test_payment_ledger_empty_headers_match_nonempty_schema(self):
        response=self.page('Payment Ledger',format='csv');self.assertIn('Date,Shop,Document',response.content.decode())
    def test_unassigned_historical_staff_activity_is_retained_and_filterable(self):
        sale=self.sale();sale.seller=None;sale.staff_names={};sale.source={'owner':'historical-operator'};sale.posted_by=None;sale.save()
        response=self.page('Staff Activity',from_date='2000-01-01');self.assertContains(response,'Unassigned / historical');self.assertContains(response,'historical-operator')
        summary=self.page('Staff Summary',from_date='2000-01-01');self.assertEqual(summary.context['rows'][0][summary.context['columns'].index('Net Sales')],100)
        filtered=self.page('Staff Activity',from_date='2000-01-01',staff=str(self.seller.pk));self.assertEqual(filtered.context['rows'],[])
    def test_sales_returns_are_identified_without_changing_net_staff_sales(self):
        sale=self.sale();s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,reference=sale,status='Submitted',is_return=True,total=-100)
        response=self.page('Staff Activity',from_date='2000-01-01');self.assertContains(response,'Sales Return')
        summary=self.page('Staff Summary',from_date='2000-01-01');self.assertEqual(summary.context['rows'][0][summary.context['columns'].index('Net Sales')],0)
