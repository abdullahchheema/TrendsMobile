"""Order confirmation, fulfilment rules, cancel reasons, and the online manager's delivery pricing and payment accounts."""
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from .models import *
from .tests_online_orders import OrderBase
from . import online_orders as oo,shop_accounts


class Statuses(OrderBase):
    def setUp(self):
        super().setUp();self.web=shop_accounts.create_online_login('Web Team','web-team','Strong-pass-for-WT-77!')
    def test_a_new_order_is_unconfirmed_cannot_ship_and_becomes_confirmed_after_the_call(self):
        self.phone(self.branch);o=self.order([self.item()],confirm=False);self.assertEqual((oo.order_status(o),o.status),('Unconfirmed','New'))
        with self.assertRaisesMessage(ValidationError,'confirm the order'):oo.dispatch(o,self.user,'TCS','T')
        self.client.force_login(self.web);url=reverse('online_order',args=[o.pk]);page=self.client.get(url);self.assertContains(page,'Customer confirmed the order');self.assertNotContains(page,'value="dispatch"')
        self.client.post(url,dict(action='dispatch',courier='TCS',tracking='T'));o.refresh_from_db();self.assertEqual(o.status,'New')      # not even by posting the form
        self.client.post(url,dict(action='confirm',note='Spoke to Ali'));o.refresh_from_db();self.assertEqual((oo.order_status(o),o.confirmed_by_id),('Confirmed',self.web.pk))
        self.assertContains(self.client.get(url),'value="dispatch"');self.assertIn('Confirmed by phone',[e.kind for e in o.events.all()])
    def test_cash_on_delivery_stays_unfulfilled_after_shipping_until_the_cash_is_received(self):
        self.phone(self.branch);o=self.order([self.item()]);oo.dispatch(o,self.user,'TCS','TRK-1');o.refresh_from_db()
        self.assertEqual((o.tracking,o.payment_status,oo.order_status(o)),('TRK-1','Unpaid','Confirmed'))          # tracking added, payment not received: not fulfilled
        oo.deliver(o,self.user);o.refresh_from_db();self.assertEqual((o.payment_status,oo.order_status(o)),('Paid','Fulfilled'))
    def test_a_paid_prepaid_order_is_fulfilled_as_soon_as_it_is_tracked_but_not_before(self):
        self.phone(self.branch);o=self.order([self.item()]);OnlineOrder.objects.filter(pk=o.pk).update(payment_method='Easypaisa',payment_status='Unpaid');o.refresh_from_db()
        with self.assertRaises(ValidationError):oo.dispatch(o,self.user,'TCS','T')                                  # payment first
        oo.mark_paid(o,self.user,'TX9');self.assertEqual(oo.order_status(OnlineOrder.objects.get(pk=o.pk)),'Confirmed')   # paid but not shipped
        oo.dispatch(o,self.user,'TCS','T-9');o.refresh_from_db();self.assertEqual(oo.order_status(o),'Fulfilled')
    def test_cancelling_needs_a_reason_from_the_list(self):
        self.phone(self.branch);o=self.order([self.item()],confirm=False);self.client.force_login(self.web);url=reverse('online_order',args=[o.pk])
        self.assertContains(self.client.get(url),'Customer did not answer the call')
        self.client.post(url,dict(action='cancel'));o.refresh_from_db();self.assertEqual(o.status,'New')                                   # no reason: refused
        self.client.post(url,dict(action='cancel',reason_choice='Made up reason'));o.refresh_from_db();self.assertEqual(o.status,'New')      # not from the list: refused
        self.client.post(url,dict(action='cancel',reason_choice='Other (write the reason)'));o.refresh_from_db();self.assertEqual(o.status,'New')   # "Other" needs words
        self.client.post(url,dict(action='cancel',reason_choice='Customer did not answer the call',reason_note='Called 3 times'));o.refresh_from_db()
        self.assertEqual((o.status,oo.order_status(o),o.cancel_reason),('Cancelled','Cancelled','Customer did not answer the call — Called 3 times'));self.assertEqual(Device.objects.filter(status='Reserved').count(),0)
    def test_the_list_counts_and_filters_follow_the_new_statuses(self):
        self.phone(self.branch);self.phone(self.branch);self.phone(self.branch)
        a=self.order([self.item()],confirm=False);b=self.order([self.item()]);c=self.order([self.item()]);oo.dispatch(c,self.user,'TCS','T');oo.deliver(c,self.user)
        self.client.force_login(self.web);get=lambda q='':self.client.get(reverse('online_orders')+q)
        self.assertContains(get('?status=Unconfirmed'),a.code);self.assertNotContains(get('?status=Unconfirmed'),b.code)
        self.assertContains(get('?status=Confirmed'),b.code);self.assertContains(get('?status=Fulfilled'),c.code);self.assertNotContains(get(),c.code)       # the working list leaves fulfilled orders out
        self.assertContains(get('?status=history'),c.code)


class Settings(OrderBase):
    def setUp(self):
        super().setUp();self.web=shop_accounts.create_online_login('Web Team','web-team','Strong-pass-for-WT-77!');self.shop=shop_accounts.create_login(self.branch,'shop-a','Strong-pass-for-SA-77!')
        self.phone(self.branch)
    def api(self,fee,method='Cash on Delivery',province='Punjab',ref='S-1'):
        data=dict(external_ref=ref,customer=dict(name='Ali Khan',phone='03001234567',province=province,city='Lahore'),payment_method=method,delivery_fee=fee,items=[self.item(price='100000')])
        return self.client.post(reverse('api_online_order'),json.dumps(data),content_type='application/json',HTTP_X_API_KEY='k')
    def test_only_the_online_side_can_open_the_settings_and_changes_are_saved(self):
        self.client.force_login(self.shop);self.assertEqual(self.client.get(reverse('online_settings')).status_code,403)
        self.client.force_login(self.web);self.assertEqual(self.client.get(reverse('online_settings')).status_code,200)
        self.client.post(reverse('online_settings'),dict(action='delivery',delivery_fee='400',free_from='80000',cod='on'));cfg=OnlineSettings.get();self.assertEqual((cfg.delivery_fee,cfg.free_delivery_from,cfg.cod_enabled),(Decimal('400'),Decimal('80000'),True))
        self.client.post(reverse('online_settings'),dict(action='delivery',delivery_fee='400',free_from=''));cfg=OnlineSettings.get();self.assertEqual((cfg.free_delivery_from,cfg.cod_enabled),(None,False))   # no free delivery, no COD
        self.client.post(reverse('online_settings'),dict(action='rule_add',province='Sindh',rule_fee='600'));self.client.post(reverse('online_settings'),dict(action='rule_add',province='sindh',rule_fee='650'))
        self.assertEqual(list(OnlineDeliveryRule.objects.values_list('province','fee')),[('Sindh',Decimal('650'))])
        self.client.post(reverse('online_settings'),dict(action='delivery',delivery_fee='999999'));self.assertEqual(OnlineSettings.get().delivery_fee,Decimal('400'))     # out of range: not saved
    def test_delivery_is_priced_by_the_settings_and_a_wrong_charge_is_refused(self):
        with override_settings(ONLINE_API_KEY='k'):
            OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('300'),free_delivery_from=Decimal('150000'),cod_enabled=True))
            r=self.api('250');self.assertEqual(r.status_code,409);self.assertIn('delivery charge is now',r.json()['error'])           # the website's old number is refused
            self.assertEqual(OnlineOrder.objects.count(),0);self.assertEqual(Device.objects.filter(status='Reserved').count(),0)
            self.assertEqual(self.api('300',ref='S-2').status_code,201)
    def test_a_province_fee_and_the_free_delivery_amount(self):
        with override_settings(ONLINE_API_KEY='k'):
            OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('300'),free_delivery_from=Decimal('200000'),cod_enabled=True));OnlineDeliveryRule.objects.create(province='Sindh',fee=Decimal('500'))
            self.assertEqual(oo.delivery_fee_for(Decimal('100000'),'Sindh'),Decimal('500'));self.assertEqual(oo.delivery_fee_for(Decimal('100000'),'Punjab'),Decimal('300'));self.assertEqual(oo.delivery_fee_for(Decimal('200000'),'Sindh'),0)
            self.assertEqual(self.api('500',province='Sindh',ref='S-3').status_code,201);self.assertEqual(OnlineOrder.objects.get(external_ref='S-3').delivery_fee,Decimal('500'))
    def test_payment_methods_follow_the_accounts_the_manager_adds(self):
        self.assertEqual([m['method'] for m in oo.payment_options()],['Cash on Delivery','Bank Transfer','Easypaisa','JazzCash','Card'])      # nothing configured yet: as before
        OnlinePaymentAccount.objects.create(method='JazzCash',title='Trends Mobile',account_number='03001234567');OnlinePaymentAccount.objects.create(method='Bank Transfer',title='Trends Mobile',bank_name='Meezan',account_number='0123456789',enabled=False)
        options=oo.payment_options();self.assertEqual([m['method'] for m in options],['Cash on Delivery','JazzCash']);self.assertEqual(options[1]['accounts'][0]['account_number'],'03001234567')
        with override_settings(ONLINE_API_KEY='k'):
            OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('250'),free_delivery_from=Decimal('30000'),cod_enabled=True))
            self.assertEqual(self.api('0',method='Bank Transfer',ref='S-4').status_code,409)                                              # hidden method: refused
            self.assertEqual(self.api('0',method='JazzCash',ref='S-5').status_code,201)
            OnlineSettings.objects.update_or_create(pk=1,defaults=dict(cod_enabled=False));self.assertEqual(self.api('0',ref='S-6').status_code,409)
    def test_the_website_reads_the_configuration_with_its_key_only(self):
        OnlineDeliveryRule.objects.create(province='Sindh',fee=Decimal('500'));OnlinePaymentAccount.objects.create(method='Easypaisa',title='Trends Mobile',account_number='03111234567',instructions='Send a screenshot')
        with override_settings(ONLINE_API_KEY='k'):
            self.assertEqual(self.client.get(reverse('api_online_config')).status_code,401)
            body=self.client.get(reverse('api_online_config'),HTTP_X_API_KEY='k').json()
            self.assertEqual((body['delivery']['fee'],body['delivery']['free_from'],body['delivery']['provinces']),('250.00','30000.00',[dict(province='Sindh',fee='500.00')]))
            self.assertEqual([m['method'] for m in body['payment_methods']],['Cash on Delivery','Easypaisa']);self.assertEqual(body['payment_methods'][1]['accounts'][0]['instructions'],'Send a screenshot')
