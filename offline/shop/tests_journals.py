"""Owner corrections must preserve source records and all retail subledgers."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from threading import Barrier
from unittest.mock import patch
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import close_old_connections,connections
from django.db.models import Sum
from django.test import TestCase,TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from . import journals as j,services as s,reports


def seed(test):
    test.__dict__.update(fixtures())
    test.first=Account.objects.create(code='RENT',name='Rent expense',account_type='Expense Account')
    test.second=Account.objects.create(code='REPAIR',name='Repair expense',account_type='Expense Account')


def values(test,amount='100'):
    return dict(branch=test.branch,posting_date=timezone.localdate(),supporting_reference='INTERNAL-RECLASS-REF',notes='Correct expense classification',
        rows=[dict(account=str(test.first.pk),debit=amount,credit='0',memo='Rent'),dict(account=str(test.second.pk),debit='0',credit=amount,memo='Repair')])


class OwnerJournals(TestCase):
    def setUp(self):seed(self);self.client.force_login(self.user)
    def draft(self):return j.save(values(self),self.user)
    def posted(self):return s.submit(self.draft(),self.user)
    def review(self,doc,amount=None):
        data=values(self);data['rows']=doc.data['manual_journal']['lines'];data['notes']='Reviewed linked correction';data['supporting_reference']='CORRECTION-REF'
        if amount:
            data['rows']=[{**row,'debit':amount if s.money(row['debit']) else '0','credit':amount if s.money(row['credit']) else '0'} for row in data['rows']]
        return j.save(data,self.user,doc)
    def posted_reversal(self,doc):return s.submit(self.review(j.prepare(doc,self.user)),self.user)
    def payload(self,doc=None,amount='100'):
        rows=doc.data['manual_journal']['lines'] if doc else values(self,amount)['rows']
        data=dict(request_key=str(uuid.uuid4()),branch=str(self.branch.pk),posting_date=str(timezone.localdate()),supporting_reference='BROWSER-REF',notes='Reviewed reclassification',**{
            'rows-TOTAL_FORMS':'2','rows-INITIAL_FORMS':'2' if doc else '0','rows-MIN_NUM_FORMS':'0','rows-MAX_NUM_FORMS':'30'})
        for index,row in enumerate(rows):
            for key in ('account','debit','credit','memo'):data[f'rows-{index}-{key}']=row.get(key,'')
        return data
    def test_balanced_reclassification_has_no_stock_party_or_till_effect(self):
        before=(list(StockMovement.objects.values()),list(Device.objects.values()),list(Document.objects.exclude(kind='Journal Entry').values()))
        doc=self.posted();self.assertEqual(doc.total,100);self.assertEqual(doc.outstanding,0);self.assertEqual(doc.ledger.count(),2)
        self.assertEqual(doc.ledger.get(account=self.first).debit,100);self.assertEqual(doc.ledger.get(account=self.second).credit,100)
        self.assertFalse(doc.stock.exists());self.assertIsNone(doc.till_id)
        self.assertEqual(before,(list(StockMovement.objects.values()),list(Device.objects.values()),list(Document.objects.exclude(kind='Journal Entry').values())))
        self.assertEqual(doc.data['manual_journal']['recorded_by'],self.user.username)
    def test_manager_and_cashier_cannot_create_submit_or_view_owner_journal(self):
        doc=self.draft();actor=get_user_model().objects.create_user('branchmanager');self.handler.user=actor;self.handler.manager=True;self.handler.save()
        with self.assertRaises(PermissionDenied):j.save(values(self),actor)
        with self.assertRaises(PermissionDenied):s.submit(doc,actor)
        self.client.force_login(actor)
        for route in (reverse('journals'),reverse('journal_new'),reverse('journal_edit',args=[doc.pk]),reverse('detail',args=[doc.pk])):
            self.assertEqual(self.client.get(route).status_code,403)
        response=self.client.get(reverse('collection',args=['Journal Entry']));self.assertNotContains(response,doc.code)
    def test_unbalanced_zero_negative_double_sided_and_precision_rows_rejected(self):
        for dr,cr in [('100','99'),('0','0'),('-1','-1'),('1.001','1.001')]:
            data=values(self);data['rows'][0]['debit']=dr;data['rows'][1]['credit']=cr
            with self.subTest(dr=dr):
                with self.assertRaises(ValidationError):j.save(data,self.user)
        data=values(self);data['rows'][0]['credit']='1'
        with self.assertRaises(ValidationError):j.save(data,self.user)
        self.assertEqual(Document.objects.filter(kind='Journal Entry').count(),0)
    def test_duplicate_and_invalid_row_shapes_rejected(self):
        for rows in ([],{},[{}],[{},{}],[values(self)['rows'][0]]*2,['bad','rows']):
            data=values(self);data['rows']=rows
            with self.assertRaises(ValidationError):j.save(data,self.user)
    def test_thirty_row_limit(self):
        data=values(self);data['rows']*=16
        with self.assertRaises(ValidationError):j.save(data,self.user)
    def test_supporting_reference_and_reason_required(self):
        for key in ('notes','supporting_reference'):
            data=values(self);data[key]=' '
            with self.assertRaises(ValidationError):j.save(data,self.user)
    def test_controls_and_other_shop_accounts_not_available(self):
        config=ShopSettings.objects.get(branch=self.branch)
        protected=[Account.objects.get(pk=value) for key,value in config.accounts.items() if key not in ('sales','expense')]
        protected+=list(Account.objects.filter(account_type__in=['Cash','Bank']))
        output=Account.objects.create(code='OUTPUT',name='Output tax',account_type='Current Liability')
        SalesTaxRule.objects.create(code='RULE',branch=self.branch,name='Synthetic rule',rate=5,account=output,reference='Test only');protected.append(output)
        other=Branch.objects.create(code='OTHER',name='Other shop',company='Test')
        ShopSettings.objects.create(branch=other,accounts={'expense':str(self.second.pk)})
        protected.append(self.second)
        for account in protected:
            data=values(self);data['rows'][1]['account']=str(account.pk)
            with self.subTest(account=account.code):
                with self.assertRaises(ValidationError):j.save(data,self.user)
    def test_changed_account_role_after_draft_blocks_post(self):
        doc=self.draft();config=ShopSettings.objects.get(branch=self.branch);config.accounts['inventory']=str(self.first.pk);config.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assertFalse(doc.ledger.exists());doc.refresh_from_db();self.assertEqual(doc.status,'Draft')
    def test_misconfigured_sales_mapping_cannot_expose_cash_control(self):
        unsafe=Account.objects.create(code='UNMAPPED-CASH',name='Cash control',account_type='Cash')
        config=ShopSettings.objects.get(branch=self.branch);config.accounts['sales']=str(unsafe.pk);config.save()
        data=values(self);data['rows'][0]['account']=str(unsafe.pk)
        with self.assertRaises(ValidationError):j.save(data,self.user)
    def test_disabled_or_group_accounts_rejected_at_post(self):
        doc=self.draft();self.first.enabled=False;self.first.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assertFalse(doc.ledger.exists())
    def test_changed_permitted_account_type_requires_resaving_draft(self):
        doc=self.draft();self.first.account_type='Current Asset';self.first.save()
        with self.assertRaisesMessage(ValidationError,'code or type changed'):s.submit(doc,self.user)
        self.assertFalse(doc.ledger.exists());doc.refresh_from_db()
        doc=j.save(values(self),self.user,doc);self.assertEqual(s.submit(doc,self.user).status,'Submitted')
    def test_cash_or_party_injection_rejected(self):
        for field,value in [('customer',self.customer),('handler',self.handler),('till',self.till),('is_return',True)]:
            doc=self.draft();setattr(doc,field,value);doc.save()
            with self.assertRaises(ValidationError):s.submit(doc,self.user)
            self.assertFalse(doc.ledger.exists())
    def test_stock_line_injection_rejected(self):
        doc=self.draft();Line.objects.create(document=doc,product=self.product,quantity=1,rate=100)
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assertFalse(doc.ledger.exists())
    def test_foreign_currency_future_and_closed_dates_rejected(self):
        doc=self.draft();doc.currency='USD';doc.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        doc.currency='PKR';doc.posting_date=timezone.localdate()+timedelta(days=1);doc.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        doc.posting_date=timezone.localdate();doc.save();s.new_doc('Mobile Daily Closing',self.branch,status='Submitted')
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assertFalse(doc.ledger.exists())
    def test_imported_and_system_journals_are_read_only_in_this_workflow(self):
        doc=self.draft();doc.source={'name':'Imported'};doc.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        doc=s.new_doc('Journal Entry',self.branch)
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assertEqual(self.client.get(reverse('journal_edit',args=[doc.pk])).status_code,403)
    def test_posted_journal_cannot_be_edited_or_cancelled(self):
        doc=self.posted()
        with self.assertRaises(ValidationError):j.save(values(self),self.user,doc)
        with self.assertRaises(ValidationError):s.cancel(doc,self.user)
        self.assertRedirects(self.client.get(reverse('journal_edit',args=[doc.pk])),reverse('detail',args=[doc.pk]))
    def test_reversal_and_replacement_preserve_original_and_links(self):
        original=self.posted();before=Document.objects.values().get(pk=original.pk);ledger=list(original.ledger.values())
        reversal=self.posted_reversal(original);self.assertEqual(reversal.ledger.get(account=self.first).credit,100)
        self.assertEqual(reversal.reference_id,original.pk);self.assertEqual(reversal.state,'Reversal')
        replacement=s.submit(self.review(j.prepare(original,self.user,True),amount='125'),self.user)
        self.assertEqual(replacement.reference_id,original.pk);self.assertEqual(replacement.total,125);self.assertEqual(replacement.state,'Replacement')
        self.assertEqual(before,Document.objects.values().get(pk=original.pk));self.assertEqual(ledger,list(original.ledger.values()))
        totals=LedgerEntry.objects.filter(account=self.first).aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(totals['d']-totals['c'],125)
    def test_replacement_can_be_corrected_again_as_linked_chain(self):
        original=self.posted();self.posted_reversal(original)
        replacement=s.submit(self.review(j.prepare(original,self.user,True)),self.user)
        reversal=self.posted_reversal(replacement);self.assertEqual(reversal.reference_id,replacement.pk)
    def test_replacement_requires_posted_reversal(self):
        original=self.posted()
        with self.assertRaises(ValidationError):j.prepare(original,self.user,True)
        j.prepare(original,self.user)
        with self.assertRaises(ValidationError):j.prepare(original,self.user,True)
    def test_prepare_reuses_pending_draft_and_posted_reverse_is_unique(self):
        original=self.posted();one=j.prepare(original,self.user);two=j.prepare(original,self.user)
        self.assertEqual(one.pk,two.pk);s.submit(self.review(one),self.user)
        with self.assertRaises(ValidationError):j.prepare(original,self.user)
    def test_reversal_needs_explicit_supporting_reference_and_exact_original_rows(self):
        original=self.posted();reverse=j.prepare(original,self.user)
        with self.assertRaises(ValidationError):s.submit(reverse,self.user)
        data=values(self);data['rows']=reverse.data['manual_journal']['lines'];data['rows'][0]['credit']='99';data['rows'][1]['debit']='99'
        with self.assertRaises(ValidationError):j.save(data,self.user,reverse)
        self.assertFalse(reverse.ledger.exists())
    def test_tampered_ledger_evidence_blocks_correction(self):
        doc=self.posted();doc.ledger.filter(account=self.first).update(debit=99)
        with self.assertRaises(ValidationError):j.prepare(doc,self.user)
    def test_closed_original_period_can_reverse_in_current_open_period(self):
        yesterday=timezone.localdate()-timedelta(days=1);data=values(self);data['posting_date']=yesterday
        original=s.submit(j.save(data,self.user),self.user)
        s.new_doc('Mobile Daily Closing',self.branch,status='Submitted',posting_date=yesterday)
        reverse=self.posted_reversal(original);self.assertEqual(reverse.posting_date,timezone.localdate());self.assertEqual(original.posting_date,yesterday)
    def test_in_place_reverse_of_a_reversal_rejected(self):
        reverse=self.posted_reversal(self.posted())
        with self.assertRaises(ValidationError):j.prepare(reverse,self.user)
    def test_cannot_move_draft_between_shops(self):
        doc=self.draft();data=values(self);data['branch']=Branch.objects.create(code='OTHER',name='Other',company='Test')
        with self.assertRaises(ValidationError):j.save(data,self.user,doc)
    def test_http_save_and_submit_retries_are_idempotent(self):
        data=self.payload();one=self.client.post(reverse('journal_new'),data);two=self.client.post(reverse('journal_new'),data)
        self.assertEqual(one.status_code,302);self.assertEqual(one.url,two.url);self.assertEqual(Document.objects.filter(kind='Journal Entry').count(),1)
        doc=Document.objects.get(kind='Journal Entry');key=str(uuid.uuid4())
        for _ in (1,2):self.client.post(reverse('action',args=[doc.pk,'submit']),dict(request_key=key))
        self.assertEqual(doc.ledger.count(),2);doc.refresh_from_db();self.assertEqual(doc.status,'Submitted')
    def test_http_reversal_fields_protected_against_tampering(self):
        original=self.posted();reverse=j.prepare(original,self.user);data=self.payload(reverse)
        data.update({'rows-0-credit':'999','rows-1-debit':'999','rows-0-DELETE':'on'})
        response=self.client.post(reverse_path(reverse),data);self.assertEqual(response.status_code,302)
        result=s.submit(Document.objects.get(pk=reverse.pk),self.user);self.assertEqual(result.total,100);self.assertEqual(result.ledger.count(),2)
    def test_detail_and_general_ledger_show_owner_adjustment(self):
        doc=self.posted();response=self.client.get(reverse('detail',args=[doc.pk]));self.assertContains(response,'Owner accounting adjustment');self.assertContains(response,'INTERNAL-RECLASS-REF')
        _,rows=reports.run('General Ledger',self.user,{});self.assertTrue(any(row['Document']==doc.code for row in rows))
        self.assertTrue(Audit.objects.filter(document=doc,action='Submit').exists())
    def test_stale_http_edit_cannot_reset_posted_journal(self):
        from .journal_forms import JournalForm
        doc=self.draft();payload=self.payload(doc);valid=JournalForm.is_valid
        def posting(form):
            result=valid(form)
            if result:s.submit(doc,self.user)
            return result
        with patch.object(JournalForm,'is_valid',posting):response=self.client.post(reverse('journal_edit',args=[doc.pk]),payload)
        self.assertEqual(response.status_code,200);doc.refresh_from_db();self.assertEqual(doc.status,'Submitted');self.assertEqual(doc.ledger.count(),2)


def reverse_path(doc):return reverse('journal_edit',args=[doc.pk])


class JournalConcurrency(TransactionTestCase):
    def test_only_one_of_two_prepared_reversals_can_post(self):
        seed(self);original=s.submit(j.save(values(self),self.user),self.user)
        first=j.prepare(original,self.user);second=s.new_doc('Journal Entry',self.branch,reference=original,data=first.data,total=first.total,notes='Other reviewed reversal')
        for doc in (first,second):doc.data['manual_journal']['supporting_reference']='Concurrency reference';doc.save()
        ready=Barrier(2)
        def post(pk):
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=self.user.pk);ready.wait(timeout=10)
                s.submit(Document.objects.get(pk=pk),user);return True
            except ValidationError:return False
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(post,[first.pk,second.pk]))
        self.assertEqual(sorted(results),[False,True]);self.assertEqual(original.linked.filter(status='Submitted').count(),1)
