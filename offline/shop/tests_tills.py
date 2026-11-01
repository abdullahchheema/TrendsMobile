from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.test import TestCase,TransactionTestCase
from django.db import close_old_connections,connections
from concurrent.futures import ThreadPoolExecutor
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from . import services as s

class TillReconciliation(TestCase):
    def setUp(self):self.__dict__.update(fixtures())
    def opening(self,amount='1000',**values):
        return s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':amount},**values),self.user)
    def payment(self,amount='2000',mode='Cash',direction='Receive',**values):
        return s.submit(s.new_doc('Payment Entry',self.branch,handler=self.handler,customer=self.customer,
            data=dict(amount=amount,mode=mode,direction=direction,reference='TEST-BANK'),**values),self.user)
    def closing(self,shift,counted='1000',note=''):
        return s.new_doc('POS Closing Entry',self.branch,reference=shift,handler=self.handler,data=dict(counted_cash=counted,difference_notes=note))
    def test_float_does_not_create_accounting_money(self):
        shift=self.opening();self.assertFalse(shift.ledger.exists())
        self.assertEqual(s.till_balance(shift)['expected'],'1000.00')
        self.assertFalse(StockMovement.objects.exists())
    def test_physical_cash_excludes_bank_and_unrelated_shop_money(self):
        self.payment('80000');shift=self.opening()
        received=self.payment('2500');self.payment('700','Cash','Pay');self.payment('3500','Bank Transfer')
        b=s.till_balance(shift)
        self.assertEqual(b['expected'],'2800.00');self.assertEqual(b['bank_received'],'3500.00')
        self.assertEqual(received.pos_shift_id,shift.pk);self.assertEqual(len(b['payments']),3)
        closing=s.submit(self.closing(shift,'2800'),self.user)
        self.assertEqual(closing.data['reconciliation']['difference'],'0.00')
    def test_shortage_requires_explanation_and_never_posts_adjustment(self):
        shift=self.opening();self.payment();closing=self.closing(shift,'2990')
        before=LedgerEntry.objects.count()
        with self.assertRaisesMessage(ValidationError,'Explain the cash'):s.submit(closing,self.user)
        shift.refresh_from_db();self.assertEqual(shift.state,'Open')
        closing.data['difference_notes']='Counted PKR 10 short';closing.save();closing=s.submit(closing,self.user)
        self.assertEqual(closing.data['reconciliation']['difference'],'-10.00');self.assertEqual(LedgerEntry.objects.count(),before)
    def test_excess_cash_is_preserved_as_difference(self):
        shift=self.opening();closing=s.submit(self.closing(shift,'1005','Five extra in count'),self.user)
        self.assertEqual(closing.data['reconciliation']['difference'],'5.00')
    def test_closing_recalculates_after_draft_was_saved(self):
        shift=self.opening();closing=self.closing(shift);self.payment('200')
        with self.assertRaises(ValidationError):s.submit(closing,self.user)
        closing.data['counted_cash']='1200';closing.save();closing=s.submit(closing,self.user)
        self.assertEqual(closing.data['reconciliation']['expected'],'1200.00')
    def test_cancellation_updates_open_shift_but_closed_snapshot_is_protected(self):
        shift=self.opening();pe=self.payment();s.cancel(pe,self.user)
        self.assertEqual(s.till_balance(shift)['expected'],'1000.00')
        pe=self.payment('300');closing=s.submit(self.closing(shift,'1300'),self.user)
        with self.assertRaisesMessage(ValidationError,'closed till shift'):s.cancel(pe,self.user)
        closing.refresh_from_db();self.assertEqual(closing.data['reconciliation']['expected'],'1300.00')
    def test_closed_shift_rejects_late_explicit_payment_and_second_close(self):
        shift=self.opening();s.submit(self.closing(shift),self.user)
        before=LedgerEntry.objects.count()
        with self.assertRaises(ValidationError):self.payment(pos_shift=shift)
        with self.assertRaises(ValidationError):s.submit(self.closing(shift),self.user)
        self.assertEqual(LedgerEntry.objects.count(),before)
    def test_next_shift_does_not_inherit_previous_cash(self):
        shift=self.opening();self.payment();s.submit(self.closing(shift,'3000'),self.user)
        next_shift=self.opening('500');self.assertEqual(s.till_balance(next_shift)['expected'],'500.00')
    def test_two_tills_are_independent_and_one_till_cannot_be_shared(self):
        first=self.opening();self.payment('200')
        other=get_user_model().objects.create_superuser('second',password='TestingOnly-1298')
        handler=Staff.objects.create(code='SECOND',name='Second cashier',branch=self.branch,can_handle_payments=True)
        second=s.new_doc('POS Opening Entry',self.branch,handler=handler,till=self.till,data={'opening_float':'600'})
        with self.assertRaises(ValidationError):s.submit(second,other)
        second.till=Till.objects.create(code='SECOND-TILL',name='Counter two',branch=self.branch);second.save();second=s.submit(second,other)
        pe=s.new_doc('Payment Entry',self.branch,customer=self.customer,handler=handler,data=dict(amount='900',mode='Cash',direction='Receive'));s.submit(pe,other)
        self.assertEqual(s.till_balance(first)['expected'],'1200.00');self.assertEqual(s.till_balance(second)['expected'],'1500.00')
    def test_wrong_cashier_or_shop_cannot_receive_into_till(self):
        shift=self.opening();other=Branch.objects.create(code='OTHER',name='Other',company='Other')
        shift.branch=other;shift.save(update_fields=['branch'])
        with self.assertRaises(ValidationError):self.payment(pos_shift=shift)
        shift.branch=self.branch;shift.save(update_fields=['branch'])
        shift.handler=self.buyer;shift.save(update_fields=['handler'])
        with self.assertRaisesMessage(ValidationError,'shift cashier'):self.payment(pos_shift=shift)
    def test_cashier_cannot_open_another_staff_shift_or_close_others(self):
        shift=self.opening();user=get_user_model().objects.create_user('cashier')
        self.handler.user=user;self.handler.save()
        with self.assertRaises(PermissionDenied):s.submit(self.closing(shift),user)
        self.buyer.can_handle_payments=True;self.buyer.save()
        doc=s.new_doc('POS Opening Entry',self.branch,handler=self.buyer,till=self.till,data={'opening_float':'0'})
        with self.assertRaises(PermissionDenied):s.submit(doc,user)
    def test_invalid_and_missing_counts_are_rejected(self):
        for value in (None,'','-1','NaN','Infinity'):
            with self.subTest(opening=value),self.assertRaises(ValidationError):self.opening(value)
        shift=self.opening('0')
        for value in (None,'','-1','NaN','Infinity'):
            with self.subTest(closing=value),self.assertRaises(ValidationError):s.submit(self.closing(shift,value),self.user)
        closing=s.submit(self.closing(shift,'0'),self.user);self.assertEqual(closing.data['reconciliation']['difference'],'0.00')
    def test_backdated_payment_or_closing_cannot_distort_shift(self):
        shift=self.opening()
        with self.assertRaises(ValidationError):self.payment(posting_date=timezone.localdate()-timedelta(days=1))
        closing=self.closing(shift);closing.posting_date-=timedelta(days=1);closing.save()
        with self.assertRaises(ValidationError):s.submit(closing,self.user)
    def test_legacy_closing_requires_manager_review_without_invented_balance(self):
        legacy=s.new_doc('POS Opening Entry',self.branch,posting_date=timezone.localdate()-timedelta(days=1),handler=self.handler,status='Submitted',state='Open',posted_by=self.user,source={'legacy':True})
        with self.assertRaisesMessage(ValidationError,'legacy shifts'):self.payment()
        with self.assertRaises(ValidationError):s.submit(self.closing(legacy,'500'),self.user)
        closing=s.submit(self.closing(legacy,'500','Legacy physical count; historical float unavailable'),self.user)
        self.assertIsNone(closing.data['reconciliation']['expected']);self.assertIsNone(closing.data['reconciliation']['difference'])
    def test_http_open_count_close_and_visible_snapshot(self):
        self.client.force_login(self.user)
        response=self.client.post(reverse('new',args=['POS Opening Entry']),dict(branch=self.branch.pk,handler=self.handler.pk,till=self.till.pk,posting_date=str(timezone.localdate()),opening_float='1000'))
        self.assertEqual(response.status_code,302)
        opening=Document.objects.get(kind='POS Opening Entry');opening=s.submit(opening,self.user);self.payment('2000')
        response=self.client.get(reverse('new',args=['POS Closing Entry']),{'shift':opening.pk})
        self.assertContains(response,'3000.00');self.assertContains(response,'Physically counted closing cash')
        response=self.client.post(reverse('new',args=['POS Closing Entry']),dict(branch=self.branch.pk,handler=self.handler.pk,reference=opening.pk,posting_date=str(timezone.localdate()),counted_cash='3000'))
        self.assertEqual(response.status_code,302)
        closing=Document.objects.get(kind='POS Closing Entry');s.submit(closing,self.user)
        response=self.client.get(reverse('detail',args=[closing.pk]));self.assertContains(response,'Till cash reconciliation');self.assertContains(response,'3000.00')
        self.assertEqual(response.context['reconciliation']['difference'],'0.00')
    def test_active_till_cannot_be_disabled_or_moved_to_another_shop(self):
        self.opening();self.till.enabled=False
        with self.assertRaises(ValidationError):self.till.full_clean()
        self.till.enabled=True;self.till.branch=Branch.objects.create(code='MOVE',name='Other shop',company='Other')
        with self.assertRaises(ValidationError):self.till.full_clean()

class TillConcurrency(TransactionTestCase):
    def test_two_database_sessions_cannot_open_the_same_till(self):
        values=fixtures();second_user=get_user_model().objects.create_superuser('second-till-user')
        second_handler=Staff.objects.create(code='SECOND-CASHIER',name='Second cashier',branch=values['branch'],can_handle_payments=True)
        def attempt(user_id,handler_id):
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=user_id)
                doc=s.new_doc('POS Opening Entry',values['branch'],handler_id=handler_id,till=values['till'],data={'opening_float':'1000'})
                try:s.submit(doc,user);return 'posted'
                except ValidationError:return 'blocked'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures=[executor.submit(attempt,values['user'].pk,values['handler'].pk),executor.submit(attempt,second_user.pk,second_handler.pk)]
            self.assertEqual(sorted(f.result() for f in futures),['blocked','posted'])
        self.assertEqual(Document.objects.filter(kind='POS Opening Entry',status='Submitted',state='Open').count(),1)
