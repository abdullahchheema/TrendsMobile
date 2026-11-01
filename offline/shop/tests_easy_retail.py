import io,uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import easy_retail,services as s,financial_reports


class EasyRetail(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def purchase_data(self,**changes):
        return dict(branch=self.branch,party_name='Counter Supplier',product=self.product,quantity=1,price=50000,asking_price=65000,pta_status='Non-PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash',payment_reference='',**changes)
    def buy(self):
        bill=easy_retail.purchase(self.purchase_data(),self.user);return bill,bill.reference.lines.get().device
    def sale_data(self,phone):return dict(branch=self.branch,party_name='Counter Customer',phone=phone,price=65000,pta_status='Non-PTA',staff=self.seller,handler=self.handler,posting_date=timezone.localdate(),payment_status='full',method='Cash',payment_reference='')
    def post(self,kind,data):
        payload={key:str(value.pk) if hasattr(value,'pk') else str(value) for key,value in data.items()};payload.setdefault('request_key',str(uuid.uuid4()))
        return self.client.post(reverse('easy_entry',args=[kind]),payload),payload
    def test_purchase_saves_stock_supplier_bill_and_partial_payment_together(self):
        data=self.purchase_data();data.update(quantity=2,payment_status='part',amount_paid=20000)
        bill=easy_retail.purchase(data,self.user);self.assertEqual(bill.total,100000);self.assertEqual(bill.outstanding,80000)
        self.assertEqual(Device.objects.count(),2);self.assertEqual(IMEI.objects.count(),0);self.assertTrue(all(phone.pta_status=='Non-PTA' for phone in Device.objects.all()))
        totals=LedgerEntry.objects.aggregate(dr=Sum('debit'),cr=Sum('credit'));self.assertEqual(totals['dr'],totals['cr'])
    def test_sale_without_imei_posts_exact_cost_customer_balance_and_profit(self):
        bill,phone=self.buy();data=self.sale_data(phone);data.update(payment_status='part',amount_paid=25000)
        sale=easy_retail.sale(data,self.user);phone.refresh_from_db();self.assertEqual(phone.status,'Sold');self.assertEqual(sale.outstanding,40000)
        self.assertEqual(sale.lines.get().cost,50000);self.assertEqual(sale.lines.get().snapshot['pta_status'],'Non-PTA');self.assertNotIn('imei_1',sale.lines.get().snapshot)
        columns,rows=financial_reports.run('Profit and Loss Statement',Branch.objects.filter(pk=self.branch.pk),timezone.localdate(),timezone.localdate())
        self.assertEqual(dict(financial_reports.summary('Profit and Loss Statement',rows))['Net profit / loss'],15000)
    def test_purchase_http_is_one_save_and_retry_does_not_duplicate_stock(self):
        response,data=self.post('purchase',self.purchase_data());self.assertEqual(response.status_code,302)
        replay=self.client.post(reverse('easy_entry',args=['purchase']),data);self.assertEqual(response.url,replay.url)
        self.assertEqual(Device.objects.count(),1);self.assertEqual(Document.objects.filter(kind='Purchase Invoice').count(),1)
        done=self.client.get(response.url);self.assertEqual(done.status_code,200);self.assertContains(done,'Still owed to supplier')
    def test_sale_http_retry_succeeds_even_when_phone_is_now_sold(self):
        bill,phone=self.buy();response,data=self.post('sale',self.sale_data(phone));self.assertEqual(response.status_code,302)
        replay=self.client.post(reverse('easy_entry',args=['sale']),data);self.assertEqual(response.url,replay.url)
        self.assertEqual(Document.objects.filter(kind='Sales Invoice').count(),1);self.assertContains(self.client.get(response.url),'Customer still owes')
        self.assertNotContains(self.client.get(reverse('receipt',args=[Document.objects.get(kind='Sales Invoice').pk])),'IMEI')
    def test_changed_retry_is_rejected(self):
        response,data=self.post('purchase',self.purchase_data());data['price']='51000'
        replay=self.client.post(reverse('easy_entry',args=['purchase']),data);self.assertEqual(replay.status_code,200);self.assertEqual(Device.objects.count(),1)
    def test_failed_payment_rolls_back_supplier_customer_stock_and_documents(self):
        data=self.purchase_data();data.update(payment_status='full',method='Bank Transfer',payment_reference='')
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):easy_retail.purchase(data,self.user)
        self.assertFalse(Device.objects.exists());self.assertFalse(Document.objects.exists());self.assertFalse(Party.objects.filter(name='Counter Supplier').exists())
    def test_invalid_part_payment_is_rejected_before_records_are_created(self):
        for amount in ('0','-1','50001'):
            data=self.purchase_data();data.update(payment_status='part',amount_paid=amount);response,payload=self.post('purchase',data)
            self.assertEqual(response.status_code,200);self.assertFalse(Device.objects.exists())
    def test_phone_cannot_be_sold_twice(self):
        bill,phone=self.buy();easy_retail.sale(self.sale_data(phone),self.user)
        from django.core.exceptions import ValidationError
        with self.assertRaises(ValidationError):easy_retail.sale(self.sale_data(phone),self.user)
        self.assertEqual(Document.objects.filter(kind='Sales Invoice').count(),1)
    def test_page_uses_plain_labels_and_has_no_imei_input(self):
        for kind,label in [('sale','New Sale'),('purchase','Buy Stock')]:
            response=self.client.get(reverse('easy_entry',args=[kind]));self.assertEqual(response.status_code,200);self.assertContains(response,label)
            self.assertContains(response,'Pay later / on credit');self.assertContains(response,'PTA');self.assertNotContains(response,'IMEI');self.assertNotContains(response,'imei_')
    def test_sale_prefill_selects_exact_phone_price_and_pta(self):
        bill,phone=self.buy();response=self.client.get(reverse('easy_entry',args=['sale']),{'phone':str(phone.pk)})
        form=response.context['form'];self.assertEqual(form['phone'].value(),phone.pk);self.assertEqual(form['price'].value(),65000);self.assertEqual(form['pta_status'].value(),'Non-PTA')
    def test_sales_staff_cannot_buy_stock_or_collect_without_payment_permission(self):
        user=get_user_model().objects.create_user('simple-seller');self.seller.user=user;self.seller.save();self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('easy_entry',args=['purchase'])).status_code,403)
        response=self.client.get(reverse('easy_entry',args=['sale']));self.assertEqual([value for value,label in response.context['form'].fields['payment_status'].choices],['unpaid'])
    def test_foreign_phone_and_warehouse_are_rejected(self):
        other=Branch.objects.create(code='OTHER-EASY',name='Other shop',company='Other');place=Warehouse.objects.create(code='OTHER-EASY-WH',name='Other stock',branch=other)
        user=get_user_model().objects.create_user('easy-local');self.buyer.user=user;self.buyer.save();self.client.force_login(user)
        data=self.purchase_data();data['warehouse']=place;response,payload=self.post('purchase',data);self.assertEqual(response.status_code,200);self.assertFalse(Device.objects.exists())
    def test_advanced_intake_and_device_forms_also_remove_imei(self):
        bill,phone=self.buy()
        for url in (reverse('new',args=['Mobile Intake']),reverse('device',args=[phone.pk])):
            response=self.client.get(url);self.assertNotContains(response,'imei_1');self.assertNotContains(response,'pta_status_1');self.assertContains(response,'name="pta_status"')
    def test_legacy_imei_rows_remain_private_history_but_are_not_printed(self):
        bill,phone=self.buy();IMEI.objects.create(number=imei(101),device=phone,slot=1)
        sale=easy_retail.sale(self.sale_data(phone),self.user);self.assertEqual(IMEI.objects.count(),1)
        for url in (reverse('dashboard'),reverse('device',args=[phone.pk]),reverse('receipt',args=[sale.pk]),reverse('detail',args=[sale.pk]),reverse('report',args=['Mobile Inventory'])):
            response=self.client.get(url);self.assertNotContains(response,imei(101));self.assertNotContains(response,'IMEI')
    def test_new_pta_choices_never_guess_legacy_unchecked_status(self):
        from importlib import import_module
        from django.apps import apps
        bill,phone=self.buy();phone.pta_status='PTA';phone.save();IMEI.objects.create(number=imei(102),device=phone,slot=1,pta_status='Not Checked')
        import_module('shop.migrations.0008_device_pta_status').preserve_known_pta(apps,None)
        phone.refresh_from_db();self.assertEqual(phone.pta_status,'Not recorded')
    def test_migration_derives_only_known_legacy_status_without_deleting_identifiers(self):
        from importlib import import_module
        from django.apps import apps
        bill,phone=self.buy();IMEI.objects.create(number=imei(103),device=phone,slot=1,pta_status='Compliant',verified_on=timezone.localdate())
        import_module('shop.migrations.0008_device_pta_status').preserve_known_pta(apps,None);phone.refresh_from_db();self.assertEqual(phone.pta_status,'PTA');self.assertEqual(IMEI.objects.count(),1)
    def test_pos_can_sell_a_phone_without_imei_using_pta_status(self):
        import json
        bill,phone=self.buy();s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'0'}),self.user)
        payload=dict(request_key=str(uuid.uuid4()),branch=str(self.branch.pk),customer_name='PTA Counter Customer',seller=str(self.seller.pk),handler=str(self.handler.pk),payments=[],items=[dict(product=str(self.product.pk),device=str(phone.pk),warehouse=str(self.warehouse.pk),quantity=1,rate='65000',pta_status='PTA')])
        response=self.client.post(reverse('pos_checkout'),json.dumps(payload),content_type='application/json');self.assertEqual(response.status_code,200,response.content)
        phone.refresh_from_db();self.assertEqual(phone.pta_status,'PTA');self.assertEqual(IMEI.objects.count(),0)
    def test_invalid_pos_pta_rolls_back_customer_and_phone_changes(self):
        import json
        bill,phone=self.buy();s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'0'}),self.user)
        payload=dict(request_key=str(uuid.uuid4()),branch=str(self.branch.pk),customer_name='Invalid PTA Customer',seller=str(self.seller.pk),items=[dict(product=str(self.product.pk),device=str(phone.pk),warehouse=str(self.warehouse.pk),quantity=1,rate='65000',pta_status='Approved maybe')])
        response=self.client.post(reverse('pos_checkout'),json.dumps(payload),content_type='application/json');self.assertEqual(response.status_code,400)
        self.assertFalse(Party.objects.filter(name='Invalid PTA Customer').exists());phone.refresh_from_db();self.assertEqual(phone.status,'Available')


class EasyDemo(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def seed(self,**kwargs):
        output=io.StringIO();call_command('seed_easy_demo',actor=self.user.username,stdout=output,**kwargs);return output.getvalue()
    def test_demo_seed_reconciles_sales_credit_stock_and_profit_without_imei(self):
        self.seed();branch=Branch.objects.get(code='COV-DEMO');docs=Document.objects.filter(branch=branch)
        self.assertEqual(docs.count(),23);self.assertEqual(Device.objects.filter(branch=branch).count(),6);self.assertFalse(IMEI.objects.filter(device__branch=branch).exists())
        self.assertEqual(docs.filter(kind='Sales Invoice').aggregate(total=Sum('total'))['total'],295000)
        self.assertEqual(docs.filter(kind='Sales Invoice').aggregate(total=Sum('outstanding'))['total'],110000)
        self.assertEqual(docs.filter(kind='Purchase Invoice').aggregate(total=Sum('outstanding'))['total'],380000)
        response=self.client.get(reverse('sales_accounts'),{'branch':str(branch.pk)});self.assertContains(response,'40,000.00');self.assertContains(response,'110,000.00');self.assertContains(response,'380,000.00')
        self.assertFalse(Document.objects.filter(branch=self.branch).exists())
    def test_demo_seed_is_idempotent_and_dry_run_leaves_no_records(self):
        self.seed(dry_run=True);self.assertFalse(Branch.objects.filter(code='COV-DEMO').exists())
        self.seed();count=Document.objects.count();self.assertIn('already_seeded',self.seed());self.assertEqual(Document.objects.count(),count)
    def test_further_training_entries_keep_demo_labels(self):
        self.seed();branch=Branch.objects.get(code='COV-DEMO');phone=Device.objects.filter(branch=branch,status='Available').first();staff=Staff.objects.get(branch=branch,can_sell=True)
        sale=easy_retail.sale(dict(branch=branch,party_name='Practice Customer',phone=phone,price=phone.asking_price,pta_status=phone.pta_status,staff=staff,handler=staff,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash'),self.user)
        self.assertEqual(sale.demo_batch,branch.demo_batch);self.assertEqual(sale.customer.demo_batch,branch.demo_batch)
    def test_counter_navigation_keeps_the_selected_training_shop(self):
        self.seed();branch=Branch.objects.get(code='COV-DEMO')
        for kind in ('sale','purchase'):
            response=self.client.get(reverse('easy_entry',args=[kind]),{'branch':str(branch.pk)})
            self.assertContains(response,'Record origin');self.assertNotContains(response,'Practice entries')
            for target in ('sale','purchase'):self.assertContains(response,reverse('easy_entry',args=[target])+'?branch='+str(branch.pk))
    def test_reports_label_training_data_and_real_shop_filter_excludes_it(self):
        self.seed();url=reverse('report',args=['Profit and Loss Statement'])
        self.assertContains(self.client.get(url),'generated as sample entries')
        self.assertNotContains(self.client.get(url,{'branch':str(self.branch.pk)}),'generated as sample entries')
