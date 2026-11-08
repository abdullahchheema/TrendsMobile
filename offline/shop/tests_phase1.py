"""Phase 1 correctness/security fixes: statement permissions, bad-input handling, login throttle, cancel dating, product guard."""
import json
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.test import TestCase,override_settings
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures
from .tests_sales_accounts import LiveSalesAccounts
from . import services as s,supabase_auth,supabase,easy_retail


class StatementPermissions(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures())
        self.buyer.user=get_user_model().objects.create_user('buyer-only',password='Unrelated-test-password-729!');self.buyer.save()
    def test_buyer_keeps_cost_reports_but_not_financial_statements(self):
        self.client.force_login(self.buyer.user)
        for name in ('General Ledger','Profit and Loss Statement','Balance Sheet','Trial Balance'):
            self.assertEqual(self.client.get(reverse('report',args=[name])).status_code,403,name)
        self.assertEqual(self.client.get(reverse('report',args=['Accounts Payable'])).status_code,200)
    def test_buyer_does_not_see_statements_or_profit_workspace(self):
        self.client.force_login(self.buyer.user)
        page=self.client.get(reverse('dashboard'))
        self.assertNotContains(page,reverse('sales_accounts'));self.assertNotContains(page,reverse('report',args=['Profit and Loss Statement']))
        self.assertRedirects(self.client.get(reverse('sales_accounts')),reverse('dashboard'),fetch_redirect_response=False)
    def test_owner_still_reads_statements(self):
        self.client.force_login(self.user)
        self.assertEqual(self.client.get(reverse('report',args=['Profit and Loss Statement'])).status_code,200)


class BadInput(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def test_unknown_entry_kind_is_a_404_not_a_crash(self):
        self.assertEqual(self.client.get('/entry/nonsense/').status_code,404)
    def test_pos_checkout_rejects_malformed_json_with_400(self):
        for body in ('not json','[]','"text"','{"branch":null}'):
            response=self.client.post(reverse('pos_checkout'),body,content_type='application/json')
            self.assertIn(response.status_code,(400,404),body)  # never a 500


@override_settings(LOGIN_MAX_FAILURES=3)
class LoginThrottle(TestCase):
    def setUp(self):cache.clear();get_user_model().objects.create_user('clerk',password='Right-password-4821!')
    def attempt(self,password):return self.client.post(reverse('login'),dict(username='clerk',password=password))
    def test_repeated_failures_lock_the_login_then_success_is_blocked_too(self):
        for _ in range(3):self.assertEqual(self.attempt('wrong').status_code,200)
        self.assertEqual(self.attempt('Right-password-4821!').status_code,429)
    def test_success_before_the_limit_resets_the_counter(self):
        self.attempt('wrong');self.attempt('wrong')
        self.assertEqual(self.attempt('Right-password-4821!').status_code,302);self.client.logout()
        for _ in range(3):self.assertEqual(self.attempt('wrong').status_code,200)


class CancelDating(LiveSalesAccounts):
    def second_phone(self):
        data=dict(branch=self.branch,party_name='Second Supplier',product=self.product,quantity=1,price=40000,asking_price=65000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash')
        return easy_retail.purchase(data,self.user).reference.lines.get().device
    def test_cancellation_reverses_ledger_and_stock_in_the_same_period(self):
        bill,_=self.transactions()
        phone=Device.objects.exclude(status='Sold').first() or bill.reference.lines.get().device
        sale=easy_retail.sale(dict(branch=self.branch,party_name='Credit Customer',phone=self.second_phone(),price=65000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash'),self.user)
        yesterday=timezone.localdate()-timedelta(days=1)
        Document.objects.filter(pk=sale.pk).update(posting_date=yesterday);sale.refresh_from_db()
        s.cancel(sale,self.user)
        reversal=Document.objects.get(kind='Journal Entry',reference=sale)
        self.assertTrue(reversal.stock.exists())
        self.assertEqual({m.posting_date for m in reversal.stock.all()}|{e.posting_date for e in reversal.ledger.all()},{reversal.posting_date})


class ProductGuard(TestCase):
    def setUp(self):self.__dict__.update(fixtures())
    def test_tracking_can_change_before_any_movement(self):
        p=Product.objects.create(code='NEW',name='Cable',stock_item=True);p.stock_item=False;p.full_clean()
    def test_tracking_is_locked_after_the_product_has_stock_history(self):
        StockMovement.objects.create(document=s.new_doc('Stock Entry',self.branch),product=self.product,warehouse=self.warehouse,quantity=1,value=1,posting_date=timezone.localdate())
        self.product.serialized=False;self.product.stock_item=True
        with self.assertRaisesMessage(ValidationError,'cannot be changed'):self.product.full_clean()


@override_settings(SUPABASE_AUTH_ENABLED=True)
class SessionVerificationCache(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures())
        import uuid;self.auth_id=uuid.uuid4();SupabaseIdentity.objects.create(user=self.user,auth_id=self.auth_id)
        self.client.force_login(self.user);session=self.client.session
        session['supabase']=dict(access_token='a',refresh_token='r',expires_at=int(timezone.now().timestamp())+3600);session.save()
    def get(self):return self.client.get(reverse('dashboard'))
    def test_provider_is_asked_once_per_window(self):
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id)}) as user:
            self.assertEqual(self.get().status_code,200);self.get();self.get()
        self.assertEqual(user.call_count,1)
    def test_short_provider_outage_does_not_sign_out_a_verified_session(self):
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id)}):self.get()
        session=self.client.session;session['supabase_verified_at']-=120;session.save()
        with patch('shop.supabase.user',side_effect=supabase.ProviderUnavailable('down')):self.assertEqual(self.get().status_code,200)
    def test_outage_without_prior_verification_fails_closed(self):
        with patch('shop.supabase.user',side_effect=supabase.ProviderUnavailable('down')):self.assertEqual(self.get().status_code,302)
    def test_explicit_revocation_signs_out_even_inside_the_cache_window(self):
        with patch('shop.supabase.user',return_value={'id':str(self.auth_id)}):self.get()
        session=self.client.session;session['supabase_verified_at']-=120;session.save()
        with patch('shop.supabase.user',side_effect=ValidationError('revoked')):self.assertEqual(self.get().status_code,302)
