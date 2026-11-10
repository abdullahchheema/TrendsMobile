"""Internal transfers conserve account value without changing invoices or inventory."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import close_old_connections,connections
from django.db.models import Sum
from django.test import TestCase,TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from . import services as s,fund_transfers as f
from .reports import run


def seed(test):
    test.__dict__.update(fixtures())
    test.wallet=Account.objects.create(code='JAZZ',name='JazzCash wallet',account_type='Bank')
    settings=ShopSettings.objects.get(branch=test.branch)
    settings.payment_methods.update(JazzCash=str(test.wallet.pk),Card=settings.payment_methods['Bank Transfer']);settings.save()

def values(test,**extra):
    return dict(branch=test.branch,posting_date=timezone.localdate(),handler=test.handler,source_method='Cash',target_method='Bank Transfer',
        amount='500',cash_scope='till',outside_till_confirmed=False,transfer_reference='ACTUAL-BANK-REF',notes='Completed cash deposit',**extra)


class FundTransfers(TestCase):
    def setUp(self):seed(self)
    def data(self,**extra):
        result=values(self);result.update(extra);return result
    def opening(self,amount='1000'):
        return s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':amount}),self.user)
    def draft(self,**extra):return f.save(self.data(**extra),self.user)
    def post(self,**extra):return s.submit(self.draft(**extra),self.user)
    def assert_empty(self,doc):
        doc.refresh_from_db();self.assertEqual(doc.status,'Draft');self.assertFalse(doc.ledger.exists());self.assertFalse(doc.stock.exists())
    def test_cash_deposit_conserves_value_and_reduces_only_own_till(self):
        shift=self.opening();doc=self.post()
        self.assertIsNone(doc.customer_id);self.assertIsNone(doc.supplier_id);self.assertEqual(doc.outstanding,0)
        self.assertEqual(doc.data['unallocated'],'0.00');self.assertFalse(doc.stock.exists())
        totals=doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(totals,dict(d=Decimal(500),c=Decimal(500)))
        self.assertEqual(doc.ledger.get(account__code='cash').credit,500)
        self.assertEqual(doc.ledger.get(account__code='bank').debit,500)
        balance=s.till_balance(shift);self.assertEqual(balance['expected'],'500.00')
        self.assertEqual(balance['internal_cash_out'],'500.00');self.assertEqual(balance['internal_bank_in'],'500.00');self.assertEqual(balance['bank_received'],'0.00')
    def test_bank_withdrawal_adds_cash_to_till_without_sales_income(self):
        shift=self.opening();doc=self.post(source_method='Bank Transfer',target_method='Cash')
        balance=s.till_balance(shift);self.assertEqual(balance['expected'],'1500.00');self.assertEqual(balance['internal_bank_out'],'500.00')
        self.assertFalse(doc.ledger.filter(account__code__in=['sales','receivable','payable','inventory']).exists())
    def test_bank_to_wallet_has_no_till_effect(self):
        shift=self.opening();doc=self.post(source_method='Bank Transfer',target_method='JazzCash')
        self.assertIsNone(doc.pos_shift_id);self.assertEqual(doc.data['cash_scope'],'bank');self.assertEqual(doc.data['till_cash'],'0.00')
        self.assertEqual(s.till_balance(shift)['expected'],'1000.00')
    def test_same_account_alias_rejected(self):
        with self.assertRaisesMessage(ValidationError,'same account'):self.draft(source_method='Bank Transfer',target_method='Card')
    def test_cash_to_cash_requires_separate_review(self):
        account=Account.objects.create(code='CASH2',name='Other cash',account_type='Cash')
        config=ShopSettings.objects.get(branch=self.branch);config.payment_methods['Other cash']=str(account.pk);config.save()
        with self.assertRaises(ValidationError):self.draft(target_method='Other cash')
    def test_unconfigured_or_foreign_method_rejected(self):
        with self.assertRaises(ValidationError):self.draft(target_method='Other shop bank')
    def test_cash_transfer_requires_counted_shift(self):
        doc=self.draft()
        with self.assertRaisesMessage(ValidationError,'counted POS shift'):s.submit(doc,self.user)
        self.assert_empty(doc)
    def test_cash_out_cannot_exceed_till_cash(self):
        shift=self.opening();doc=self.draft(amount='1000.01')
        with self.assertRaisesMessage(ValidationError,'exceeds the cash'):s.submit(doc,self.user)
        self.assert_empty(doc);self.assertEqual(s.till_balance(shift)['expected'],'1000.00')
    def test_cashier_can_handle_bank_transfer_without_a_till(self):
        user=get_user_model().objects.create_user('cash-user');self.handler.user=user;self.handler.save()
        doc=f.save(self.data(source_method='Bank Transfer',target_method='JazzCash'),user)
        self.assertEqual(s.submit(doc,user).status,'Submitted')
    def test_wrong_till_cashier_rejected(self):
        self.opening();other=Staff.objects.create(code='OTHER-H',name='Other handler',branch=self.branch,can_handle_payments=True)
        doc=self.draft(handler=other)
        with self.assertRaisesMessage(ValidationError,'current till cashier'):s.submit(doc,self.user)
        self.assert_empty(doc)
    def test_stale_account_mapping_requires_resaving_draft(self):
        doc=self.draft(source_method='Bank Transfer',target_method='JazzCash')
        other=Account.objects.create(code='NEW-WALLET',name='Replacement wallet',account_type='Bank')
        config=ShopSettings.objects.get(branch=self.branch);config.payment_methods['JazzCash']=str(other.pk);config.save()
        with self.assertRaisesMessage(ValidationError,'mappings changed'):s.submit(doc,self.user)
        self.assert_empty(doc)
        f.save(self.data(source_method='Bank Transfer',target_method='JazzCash'),self.user,doc)
        result=s.submit(doc,self.user);self.assertEqual(result.ledger.get(debit=500).account,other)
    def test_disabled_account_rejected(self):
        doc=self.draft(source_method='Bank Transfer',target_method='JazzCash');self.wallet.enabled=False;self.wallet.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assert_empty(doc)
    def test_invalid_amounts_rejected_before_creating_document(self):
        before=Document.objects.count()
        for amount in ('0','-1','500.001','NaN','Infinity','1e9999999'):
            with self.subTest(amount=amount):
                with self.assertRaises(ValidationError):self.draft(amount=amount)
        self.assertEqual(Document.objects.count(),before)
    def test_closed_or_future_date_rejected(self):
        future=self.draft(source_method='Bank Transfer',target_method='JazzCash',posting_date=timezone.localdate()+timedelta(days=1))
        with self.assertRaises(ValidationError):s.submit(future,self.user)
        s.new_doc('Mobile Daily Closing',self.branch,status='Submitted')
        doc=self.draft(source_method='Bank Transfer',target_method='JazzCash')
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assert_empty(doc)
    def test_cannot_attach_party_invoice_items_or_advance(self):
        self.opening();doc=self.draft();doc.customer=self.customer;doc.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assert_empty(doc)
        doc.customer=None;doc.save();result=s.submit(doc,self.user)
        invoice=s.new_doc('Sales Invoice',self.branch,status='Submitted',customer=self.customer,outstanding=1000)
        with self.assertRaises(ValidationError):s.allocate_advance(result,invoice,100,self.user)
        invoice.refresh_from_db();self.assertEqual(invoice.outstanding,1000)
    def test_transfer_report_does_not_inflate_staff_collections(self):
        self.opening();doc=self.post()
        _,rows=run('Staff Summary',self.user,{})
        row=next(row for row in rows if row['Staff ID']==self.handler.code)
        self.assertEqual(row['Internal Transfers Handled'],500);self.assertEqual(row['Money Collected'],0);self.assertEqual(row['Money Paid / Refunded'],0)
        _,rows=run('Staff Activity',self.user,{})
        self.assertTrue(any(row['Document']==doc.code and row['Activity']=='Internal transfer' for row in rows))
        _,rows=run('Payment Ledger',self.user,{})
        self.assertFalse(any(row['Document']==doc.code for row in rows))
    def test_actual_handler_snapshot_is_retained(self):
        self.opening();doc=self.post();name=doc.staff_names['handler'];self.handler.name='Renamed later';self.handler.save()
        self.assertEqual(Document.objects.get(pk=doc.pk).staff_names['handler'],name)
    def test_cancel_blocked_and_reverse_requires_new_reference(self):
        shift=self.opening();original=self.post()
        with self.assertRaisesMessage(ValidationError,'reverse internal transfer'):s.cancel(original,self.user)
        draft=f.reverse(original,self.user);self.assertEqual(f.reverse(original,self.user),draft)
        f.save(self.data(source_method='Bank Transfer',target_method='Cash',transfer_reference='ACTUAL-REVERSE'),self.user,draft)
        reverse=s.submit(draft,self.user)
        self.assertEqual(s.till_balance(shift)['expected'],'1000.00');self.assertEqual(reverse.reference,original)
        original.refresh_from_db();self.assertEqual(original.status,'Submitted')
        with self.assertRaises(ValidationError):f.reverse(original,self.user)
    def test_reverse_after_closing_preserves_old_count(self):
        first=self.opening();original=self.post()
        closing=s.submit(s.new_doc('POS Closing Entry',self.branch,reference=first,handler=self.handler,data={'counted_cash':'500'}),self.user)
        second=self.opening('200');draft=f.reverse(original,self.user)
        f.save(self.data(source_method='Bank Transfer',target_method='Cash',transfer_reference='NEW-REF'),self.user,draft);s.submit(draft,self.user)
        closing.refresh_from_db();self.assertEqual(closing.data['reconciliation']['expected'],'500.00');self.assertEqual(s.till_balance(second)['expected'],'700.00')
    def test_http_save_and_submit_are_idempotent(self):
        self.opening();self.client.force_login(self.user)
        data=self.data();data.update(branch=str(self.branch.pk),handler=str(self.handler.pk),posting_date=str(timezone.localdate()),request_key=str(uuid.uuid4()))
        first=self.client.post(reverse('fund_transfer_new'),data);second=self.client.post(reverse('fund_transfer_new'),data)
        self.assertEqual(first.status_code,302,first.content);self.assertEqual(first.url,second.url)
        doc=Document.objects.get(data__direction='Internal Transfer');key=uuid.uuid4()
        for _ in (1,2):self.client.post(reverse('action',args=[doc.pk,'submit']),{'request_key':str(key)})
        self.assertEqual(doc.ledger.count(),2);self.assertEqual(s.till_balance(self.shift if hasattr(self,'shift') else s.active_shift(self.branch,self.user))['expected'],'500.00')
        self.assertContains(self.client.get(reverse('fund_transfers')),doc.code)
        self.assertContains(self.client.get(reverse('detail',args=[doc.pk])),'ACTUAL-BANK-REF')
    def test_posted_draft_cannot_be_resaved(self):
        self.opening();doc=self.post()
        with self.assertRaises(ValidationError):f.save(self.data(amount='900'),self.user,doc)
        self.assertEqual(doc.ledger.count(),2)
    def test_saved_transfer_cannot_silently_move_to_a_new_shift(self):
        first=self.opening();doc=self.draft()
        closing=s.submit(s.new_doc('POS Closing Entry',self.branch,reference=first,handler=self.handler,data={'counted_cash':'1000'}),self.user)
        second=self.opening()
        with self.assertRaisesMessage(ValidationError,'shift changed'):s.submit(doc,self.user)
        self.assert_empty(doc)
        f.save(self.data(),self.user,doc);s.submit(doc,self.user)
        closing.refresh_from_db();self.assertEqual(closing.data['reconciliation']['expected'],'1000.00')
        self.assertEqual(s.till_balance(second)['expected'],'500.00')
    def test_generic_invoice_edit_cannot_restore_a_posted_document_to_draft(self):
        from .forms import DocumentForm
        item=Product.objects.create(code='CASE-RACE',name='Case')
        seed_doc=s.new_doc('Purchase Receipt',self.branch,status='Submitted')
        StockMovement.objects.create(document=seed_doc,posting_date=timezone.localdate(),product=item,warehouse=self.warehouse,quantity=2,value=200)
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        line=Line.objects.create(document=doc,product=item,warehouse=self.warehouse,quantity=1,rate=500)
        data=dict(branch=str(self.branch.pk),posting_date=str(timezone.localdate()),customer=str(self.customer.pk),seller=str(self.seller.pk),warehouse=str(self.warehouse.pk),notes='stale edit',**{
            'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'1','lines-MIN_NUM_FORMS':'0','lines-MAX_NUM_FORMS':'1000','lines-0-id':str(line.pk),
            'lines-0-product':str(item.pk),'lines-0-device':'','lines-0-warehouse':str(self.warehouse.pk),'lines-0-quantity':'1','lines-0-rate':'999'})
        valid=DocumentForm.is_valid
        def post_during_validation(form):
            result=valid(form)
            if result:s.submit(Document.objects.get(pk=doc.pk),self.user)
            return result
        self.client.force_login(self.user)
        with patch.object(DocumentForm,'is_valid',post_during_validation):response=self.client.post(reverse('edit',args=['Sales Invoice',doc.pk]),data)
        self.assertEqual(response.status_code,200);doc.refresh_from_db();line.refresh_from_db()
        self.assertEqual(doc.status,'Submitted');self.assertEqual(doc.total,500);self.assertEqual(line.rate,500)


class FundConcurrency(TransactionTestCase):
    def test_simultaneous_cash_deposits_cannot_overdraw_one_till(self):
        seed(self)
        shift=s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'1000'}),self.user)
        data=values(self);data['amount']='700'
        docs=[f.save(data,self.user) for _ in (1,2)];ready=Barrier(2)
        def post(pk):
            close_old_connections()
            try:
                ready.wait(timeout=10);s.submit(Document.objects.get(pk=pk),get_user_model().objects.get(pk=self.user.pk));return 'posted'
            except ValidationError:return 'blocked'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(post,[doc.pk for doc in docs]))
        self.assertCountEqual(result,['posted','blocked']);self.assertEqual(s.till_balance(shift)['expected'],'300.00')
