from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import services as s,reports

class ScreenParity(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
    def phone(self,number=21):
        intake=s.new_doc('Mobile Intake',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer,
            posting_date=timezone.localdate()-timedelta(days=5),data={'device':{'imeis':[{'number':imei(number)},{'number':imei(number+1000)}],'asking_price':'65000','minimum_price':'60000'}})
        Line.objects.create(document=intake,product=self.product,warehouse=self.warehouse,quantity=1,rate=50000)
        intake=s.submit(intake,self.user);return Device.objects.get(pk=intake.data['device'])
    def sale(self,phone):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse,posting_date=timezone.localdate()-timedelta(days=3))
        Line.objects.create(document=doc,product=self.product,device=phone,warehouse=self.warehouse,quantity=1,rate=65000)
        return s.checkout(doc,self.user,[],None)
    def payment(self,amount,invoice=None):
        return s.submit(s.new_doc('Payment Entry',self.branch,customer=self.customer,reference=invoice,handler=self.handler,data=dict(amount=amount,direction='Receive',mode='Cash')),self.user)
    def test_intake_defaults_and_inspection_choices_render_as_usable_inputs(self):
        response=self.client.get(reverse('new',args=['Mobile Intake']))
        self.assertContains(response,'value="'+str(timezone.localdate())+'"')
        self.assertEqual(response.context['form']['branch'].value(),self.branch.pk)
        self.assertEqual(response.context['form']['device_type'].value(),'Used');self.assertEqual(response.context['form']['condition'].value(),'Not Inspected')
        self.assertContains(response,'name="display_condition"');self.assertContains(response,'Original / Working')
        self.assertContains(response,'Condition and inspection');self.assertContains(response,'Phone variant and purchase price')
    def test_inventory_filters_status_model_and_pta_without_duplicates(self):
        sold=self.phone();available=self.phone(22);self.sale(sold)
        available.pta_status='Non-PTA';available.save()
        _,rows=reports.run('Mobile Inventory',self.user,{'status':'Available','model':str(self.product.model_id),'pta_status':'Non-PTA'})
        self.assertEqual([row['Device'] for row in rows],[available.code]);self.assertEqual(rows[0]['PTA status'],'Non-PTA');self.assertNotIn('IMEI',rows[0])
        response=self.client.get(reverse('report',args=['Mobile Inventory']),{'status':'Sold','model':str(self.product.model_id)})
        self.assertEqual(len(response.context['rows']),1);self.assertEqual(response.context['filter_form']['status'].value(),'Sold')
    def test_report_screen_and_csv_reject_foreign_or_malformed_filters(self):
        other=Branch.objects.create(code='FOREIGN',name='Other shop',company='Other')
        warehouse=Warehouse.objects.create(code='FOREIGN-WH',name='Other warehouse',branch=other)
        user=get_user_model().objects.create_user('local-sales');self.seller.user=user;self.seller.save();self.client.force_login(user)
        for data in ({'branch':'not-a-uuid'},{'status':'invented'}):
            for export in ('','csv'):
                response=self.client.get(reverse('report',args=['Mobile Inventory']),{**data,'format':export});self.assertEqual(response.status_code,400)
        response=self.client.get(reverse('report',args=['Sales Register']),{'from_date':'2026-12-01','to_date':'2026-01-01'});self.assertEqual(response.status_code,400)
    def test_receivables_as_of_date_does_not_use_current_paid_balance(self):
        sale=self.sale(self.phone());self.payment('25000',sale)
        _,past=reports.run('Accounts Receivable',self.user,{'to_date':str(timezone.localdate()-timedelta(days=1))})
        _,now=reports.run('Accounts Receivable',self.user,{})
        self.assertEqual(sum(r['Outstanding'] for r in past),Decimal('65000'))
        self.assertEqual(sum(r['Outstanding'] for r in now),Decimal('40000'))
        self.assertEqual(past[0]['Document'],sale.code)
    def test_advance_allocation_does_not_change_historical_receivables(self):
        sale=self.sale(self.phone());advance=self.payment('5000');s.allocate_advance(advance,sale,5000,self.user)
        _,past=reports.run('Accounts Receivable',self.user,{'to_date':str(timezone.localdate()-timedelta(days=1))})
        _,now=reports.run('Accounts Receivable',self.user,{})
        self.assertEqual(sum(r['Outstanding'] for r in past),Decimal('65000'));self.assertEqual(sum(r['Outstanding'] for r in now),Decimal('60000'))
        self.assertEqual(len(now),1)
    def test_payables_as_of_date_excludes_later_supplier_payment(self):
        phone=self.phone();receipt=Line.objects.get(device=phone,document__kind='Purchase Receipt').document
        bill=s.supplier_bill(receipt,self.user);bill.posting_date=receipt.posting_date;bill.save();bill=s.submit(bill,self.user)
        s.submit(s.new_doc('Payment Entry',self.branch,supplier=self.supplier,reference=bill,handler=self.handler,data=dict(amount='20000',direction='Pay',mode='Cash')),self.user)
        _,past=reports.run('Accounts Payable',self.user,{'to_date':str(timezone.localdate()-timedelta(days=1))})
        _,now=reports.run('Accounts Payable',self.user,{})
        self.assertEqual(sum(r['Outstanding'] for r in past),Decimal('50000'));self.assertEqual(sum(r['Outstanding'] for r in now),Decimal('30000'))
    def test_imported_pos_receipt_preserves_payment_change_and_collector(self):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,status='Submitted',total=1000,staff_names={'seller':'Ali Hassan','handler':'Ayesha Malik'},
            source={'is_pos':1,'base_change_amount':100,'payments':[{'mode_of_payment':'Cash','base_amount':1100}]})
        Line.objects.create(document=doc,product=self.product,quantity=1,rate=1000,snapshot={'imei_1':imei(21),'battery':'Not recorded'})
        response=self.client.get(reverse('receipt',args=[doc.pk]))
        self.assertContains(response,'1100.00');self.assertContains(response,'Change returned PKR 100.00');self.assertNotContains(response,'Ayesha Malik');self.assertContains(response,'receipt-thermal')
        self.assertNotContains(response,'Not recorded%')
        self.assertContains(self.client.get(reverse('receipt',args=[doc.pk]),{'paper':'a4'}),'receipt-a4')
        self.assertContains(self.client.get(reverse('receipt',args=[doc.pk]),{'paper':'58'}),'receipt-58')
    def test_settings_keep_selected_shop_after_save(self):
        other=Branch.objects.create(code='SETTINGS-OTHER',name='Other shop',company='Other')
        ShopSettings.objects.create(branch=other)
        response=self.client.post(reverse('settings')+'?branch='+str(other.pk),{'shop_phone':'03001234567','shop_address':'Second shop','receipt_terms':'Terms','urdu_receipt_note':'','ageing_days':'40'})
        self.assertEqual(response.status_code,302);self.assertIn(str(other.pk),response.url)
        self.assertEqual(ShopSettings.objects.get(branch=other).shop_address,'Second shop');self.assertEqual(ShopSettings.objects.get(branch=self.branch).shop_address,'')
    def test_shop_switchers_keep_scope_and_reject_invalid_urls(self):
        self.phone()
        other=Branch.objects.create(code='PICKER-OTHER',name='Second shop',company='Other')
        response=self.client.get(reverse('dashboard'),{'branch':str(other.pk)})
        self.assertEqual(response.status_code,200)
        for name in ('dashboard','pos','settings'):
            self.assertEqual(self.client.get(reverse(name),{'branch':'not-a-uuid'}).status_code,404)
        user=get_user_model().objects.create_user('shop-sales');self.seller.user=user;self.seller.save();self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('dashboard'),{'branch':str(other.pk)}).status_code,404)
