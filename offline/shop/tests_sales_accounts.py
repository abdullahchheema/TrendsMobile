from datetime import timedelta
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from . import services as s,easy_retail


class LiveSalesAccounts(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def transactions(self):
        data=dict(branch=self.branch,party_name='Working Supplier',product=self.product,quantity=1,price=50000,asking_price=65000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash')
        bill=easy_retail.purchase(data,self.user)
        sale=easy_retail.sale(dict(branch=self.branch,party_name='Working Customer',phone=bill.reference.lines.get().device,price=65000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=timezone.localdate(),payment_status='part',amount_paid=25000,method='Cash'),self.user)
        return bill,sale
    def test_workspace_uses_operating_shop_and_actual_posted_transactions(self):
        bill,sale=self.transactions();response=self.client.get(reverse('sales_accounts'))
        self.assertEqual(response.status_code,200);self.assertEqual(response.context['branch'],self.branch)
        self.assertEqual(response.context['totals'],dict(sales=65000,expenses=50000,profit=15000,customers=40000,suppliers=50000,customer_credit=0,supplier_credit=0))
        self.assertFalse(sale.demo_batch);self.assertFalse(bill.demo_batch);self.assertContains(response,'Sales &amp; Accounts');self.assertNotContains(response,'Demo shop practice')
        self.assertNotContains(response,'Record origin')
        for report in response.context['reports']:self.assertIn('branch='+str(self.branch.pk),report['url'])
    def test_workspace_exists_without_any_demo_shop(self):
        response=self.client.get(reverse('sales_accounts'));self.assertEqual(response.status_code,200);self.assertContains(response,'PKR 0.00')
        self.assertContains(self.client.get(reverse('dashboard')),'Sales &amp; Accounts');self.assertNotContains(self.client.get(reverse('dashboard')),'Demo dataset')
    def test_customer_and_supplier_advances_are_shown_separately_from_dues(self):
        for party,direction in ((self.customer,'Receive'),(self.supplier,'Pay')):
            s.submit(s.new_doc('Payment Entry',self.branch,handler=self.handler,customer=party if party.kind=='Customer' else None,supplier=party if party.kind=='Supplier' else None,data=dict(direction=direction,mode='Cash',amount='500')),self.user)
        response=self.client.get(reverse('sales_accounts'));totals=response.context['totals']
        self.assertEqual((totals['customers'],totals['suppliers']),(0,0));self.assertEqual((totals['customer_credit'],totals['supplier_credit']),(500,500));self.assertEqual(totals['sales'],0)
    def test_period_filters_sales_but_balances_remain_as_of_end_date(self):
        self.transactions();start=timezone.localdate()+timedelta(days=1)
        response=self.client.get(reverse('sales_accounts'),{'from_date':str(start),'to_date':str(start)})
        self.assertEqual(response.status_code,200);self.assertEqual(response.context['totals']['sales'],0);self.assertEqual(response.context['totals']['customers'],40000)
    def test_invalid_dates_and_unclassified_accounts_show_actionable_errors(self):
        response=self.client.get(reverse('sales_accounts'),{'from_date':'not-a-date'});self.assertEqual(response.status_code,400)
        unknown=Account.objects.create(code='LIVE-UNKNOWN',name='Unclassified account');doc=s.new_doc('Journal Entry',self.branch)
        LedgerEntry.objects.create(document=doc,branch=self.branch,posting_date=timezone.localdate(),account=unknown,debit=10)
        response=self.client.get(reverse('sales_accounts'));self.assertEqual(response.status_code,400);self.assertContains(response,'account classifications',status_code=400)
    def test_new_operating_transactions_do_not_inherit_demo_mode_from_stock_or_parties(self):
        self.product.demo_batch='INITIAL-STOCK';self.product.save()
        self.supplier.demo_batch='INITIAL-PARTIES';self.supplier.save()
        self.customer.demo_batch='INITIAL-PARTIES';self.customer.save()
        data=dict(branch=self.branch,party_name=self.supplier.name,product=self.product,quantity=1,price=50000,asking_price=65000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash')
        bill=easy_retail.purchase(data,self.user)
        sale=easy_retail.sale(dict(branch=self.branch,party_name=self.customer.name,phone=bill.reference.lines.get().device,price=65000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=timezone.localdate(),payment_status='full',method='Cash'),self.user)
        self.assertFalse(bill.demo_batch);self.assertFalse(sale.demo_batch)
        self.assertIn('INITIAL-PARTIES',sale.data['initial_record_batches']);self.assertIn('INITIAL-STOCK',sale.data['initial_record_batches'])
        self.assertNotContains(self.client.get(reverse('receipt',args=[sale.pk])),'DEMONSTRATION DATA')
        self.assertContains(self.client.get(reverse('detail',args=[sale.pk])),'Record history')
