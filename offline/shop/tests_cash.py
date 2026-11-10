"""Cash handed/change is evidence; stock, AR, ledger and tills use net applied money."""
import json
import uuid
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from . import services as s


class CashSettlement(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures())
        self.item=Product.objects.create(code='CASH-CASE',name='Phone case',selling_price=4500)
        receipt=s.new_doc('Purchase Receipt',self.branch,status='Submitted')
        StockMovement.objects.create(document=receipt,product=self.item,warehouse=self.warehouse,quantity=6,value=600,posting_date=timezone.localdate())
        self.shift=s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'1000'}),self.user)
    def sale(self,rate=4500,kind='POS Invoice'):
        doc=s.new_doc(kind,self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=doc,product=self.item,warehouse=self.warehouse,quantity=1,rate=rate)
        return doc
    def post(self,rows,rate=4500):return s.checkout(self.sale(rate),self.user,rows,self.handler)
    def cash(self,amount='4500',tendered='5000'):
        result=dict(mode='Cash',amount=amount)
        if tendered is not None:result['cash_tendered']=tendered
        return result
    def assert_failed(self,rows):
        doc=self.sale();before=(StockMovement.objects.count(),LedgerEntry.objects.count(),Document.objects.count())
        with self.assertRaises(ValidationError):s.checkout(doc,self.user,rows,self.handler)
        doc.refresh_from_db();self.assertEqual(doc.status,'Draft');self.assertFalse(doc.linked.exists())
        self.assertEqual((StockMovement.objects.count(),LedgerEntry.objects.count(),Document.objects.count()),before)
        self.assertEqual(s.till_balance(self.shift)['expected'],'1000.00')
    def test_cash_change_posts_only_retained_amount(self):
        doc=self.post([self.cash()]);payment=doc.linked.get(kind='Payment Entry')
        self.assertEqual(doc.outstanding,0);self.assertEqual(payment.total,4500)
        self.assertEqual(payment.data['cash_tendered'],'5000.00');self.assertEqual(payment.data['change_returned'],'500.00')
        self.assertEqual(payment.data['till_cash'],'4500.00');self.assertEqual(s.till_balance(self.shift)['expected'],'5500.00')
        cash=LedgerEntry.objects.filter(account__code='cash').aggregate(d=Sum('debit'),c=Sum('credit'))
        self.assertEqual(cash['d'],4500);self.assertEqual(cash['c'],0)
        self.assertEqual(s.on_hand(self.item,self.warehouse),(5,500))
        self.assertEqual(s.cash_summary(doc),dict(recorded=True,cash_tendered=Decimal(5000),change_returned=Decimal(500),cash_applied=Decimal(4500)))
    def test_split_payment_keeps_bank_out_of_physical_cash(self):
        doc=self.post([self.cash('3500','4000'),dict(mode='Bank Transfer',amount='1000',reference='BANK-TEST')])
        self.assertEqual(doc.outstanding,0)
        balance=s.till_balance(self.shift);self.assertEqual(balance['expected'],'4500.00');self.assertEqual(balance['bank_received'],'1000.00')
        self.assertEqual(s.cash_summary(doc)['change_returned'],500)
    def test_partial_payment_and_change_leave_remaining_credit(self):
        doc=self.post([self.cash('1000','2000')])
        self.assertEqual(doc.outstanding,3500);self.assertEqual(s.till_balance(self.shift)['expected'],'2000.00')
    def test_credit_sale_has_no_cash_settlement(self):
        doc=self.post([]);self.assertEqual(doc.outstanding,4500)
        self.assertFalse(s.cash_summary(doc)['recorded']);self.assertEqual(s.till_balance(self.shift)['expected'],'1000.00')
    def test_omitted_cash_handed_preserves_existing_exact_payment_clients(self):
        doc=self.post([self.cash(tendered=None)]);payment=doc.linked.get()
        self.assertEqual(payment.data['cash_tendered'],'4500.00');self.assertEqual(payment.data['change_returned'],'0.00')
    def test_multiple_cash_rows_sum_change_without_extra_money_entry(self):
        doc=self.post([self.cash('2500','3000'),self.cash('2000','2500')])
        summary=s.cash_summary(doc)
        self.assertEqual((summary['cash_tendered'],summary['change_returned'],summary['cash_applied']),(5500,1000,4500))
        self.assertEqual(doc.linked.count(),2);self.assertEqual(s.till_balance(self.shift)['expected'],'5500.00')
    def test_paisa_values_are_exact(self):
        doc=self.post([self.cash('4500.25','5000.50')],rate='4500.25')
        self.assertEqual(s.cash_summary(doc)['change_returned'],Decimal('500.25'))
        self.assertEqual(s.till_balance(self.shift)['expected'],'5500.25')
    def test_under_tendered_cash_rolls_back_sale_and_stock(self):self.assert_failed([self.cash(tendered='4000')])
    def test_negative_cash_handed_rejected(self):self.assert_failed([self.cash(tendered='-1')])
    def test_nonfinite_cash_handed_rejected(self):
        for value in ('NaN','Infinity','1e999','1e9999999'):
            with self.subTest(value=value):self.assert_failed([self.cash(tendered=value)])
    def test_third_decimal_in_tender_or_applied_amount_rejected(self):
        self.assert_failed([self.cash(tendered='5000.001')]);self.assert_failed([self.cash(amount='4500.001')])
    def test_bank_row_cannot_request_cash_change(self):
        self.assert_failed([self.cash('1000','2000'),dict(mode='Bank Transfer',amount='3500',cash_tendered='4000',reference='BANK')])
    def test_gross_cash_is_not_used_as_invoice_overpayment(self):self.assert_failed([self.cash('5000','5000')])
    def test_forged_change_value_is_ignored_and_recomputed(self):
        row=self.cash();row['change_returned']='999999'
        doc=self.post([row]);self.assertEqual(s.cash_summary(doc)['change_returned'],500)
    def test_custom_cash_method_uses_account_type(self):
        config=ShopSettings.objects.get(branch=self.branch);config.payment_methods['Counter notes']=config.payment_methods['Cash'];config.save()
        self.assertIn('Counter notes',s.cash_modes(self.branch))
        row=self.cash();row['mode']='Counter notes'
        doc=self.post([row]);self.assertEqual(s.cash_summary(doc)['change_returned'],500)
    def test_invalid_payment_shapes_roll_back(self):
        for rows in ({'mode':'Cash'},[None],[dict(mode=['Cash'],amount=4500)],[self.cash()]*31):
            with self.subTest(rows=type(rows).__name__):self.assert_failed(rows)
    def test_receipts_all_layouts_and_detail_show_cash_evidence(self):
        doc=self.post([self.cash()]);self.client.force_login(self.user)
        for paper in ('58','thermal','a4'):
            response=self.client.get(reverse('receipt',args=[doc.pk]),{'paper':paper})
            self.assertContains(response,'Change returned: PKR 500.00');self.assertContains(response,'Received by Cash')
        self.assertContains(self.client.get(reverse('detail',args=[doc.pk])),'Recorded cash settlement')
        payment=doc.linked.get();self.assertContains(self.client.get(reverse('detail',args=[payment.pk])),'Cash handed PKR 5000.00')
    def test_cancelled_payment_removed_from_receipt_and_till(self):
        doc=self.post([self.cash()]);s.cancel(doc.linked.get(),self.user)
        self.assertFalse(s.cash_summary(doc)['recorded']);self.assertEqual(s.till_balance(self.shift)['expected'],'1000.00')
        self.client.force_login(self.user);self.assertNotContains(self.client.get(reverse('receipt',args=[doc.pk])),'Change returned')
    def test_counted_till_closes_using_net_cash(self):
        self.post([self.cash()])
        closing=s.new_doc('POS Closing Entry',self.branch,reference=self.shift,handler=self.handler,data={'counted_cash':'5500'})
        result=s.submit(closing,self.user)
        self.assertEqual(result.data['reconciliation']['difference'],'0.00')
        row=result.data['reconciliation']['payments'][0];self.assertEqual(row['cash_tendered'],'5000.00');self.assertEqual(row['change_returned'],'500.00')
    def test_counter_form_accepts_cash_handed(self):
        doc=self.sale(kind='Sales Invoice');self.client.force_login(self.user)
        response=self.client.post(reverse('action',args=[doc.pk,'checkout']),dict(request_key=str(uuid.uuid4()),handler=str(self.handler.pk),payment_mode=['Cash'],payment_amount=['4500'],payment_reference=[''],payment_tendered=['5000']))
        self.assertEqual(response.status_code,302);doc.refresh_from_db();self.assertEqual(doc.status,'Submitted')
        self.assertEqual(s.cash_summary(doc)['change_returned'],500)
    def test_api_cash_retry_returns_same_sale_and_payment(self):
        self.client.force_login(self.user)
        data=dict(request_key=str(uuid.uuid4()),branch=str(self.branch.pk),customer=str(self.customer.pk),seller=str(self.seller.pk),handler=str(self.handler.pk),
            items=[dict(product=str(self.item.pk),warehouse=str(self.warehouse.pk),quantity=1,rate='4500')],payments=[self.cash()])
        one=self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json')
        two=self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json')
        self.assertEqual(one.status_code,200,one.content);self.assertEqual(one.json()['id'],two.json()['id'])
        doc=Document.objects.get(pk=one.json()['id']);self.assertEqual(doc.linked.count(),1);self.assertEqual(s.till_balance(self.shift)['expected'],'5500.00')
    def test_counter_bank_first_cash_second_keeps_tender_aligned(self):
        doc=self.sale(kind='Sales Invoice');self.client.force_login(self.user)
        self.client.post(reverse('action',args=[doc.pk,'checkout']),dict(request_key=str(uuid.uuid4()),handler=str(self.handler.pk),
            payment_mode=['Bank Transfer','Cash'],payment_amount=['1000','3500'],payment_reference=['BANK',''],payment_tendered=['4000'],payment_cash_value=['','4000']))
        doc.refresh_from_db();self.assertEqual(doc.status,'Submitted');self.assertEqual(s.cash_summary(doc)['change_returned'],500)
        self.assertEqual(s.till_balance(self.shift)['expected'],'4500.00')
