"""Discount/tax allocations must reconcile through inventory, payment and return ledgers."""
from decimal import Decimal
import json,uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import services as s,pricing as p,reports


class InvoicePricing(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures())
        self.item=Product.objects.create(code='CASE',name='Case',selling_price=100)
        receipt=s.new_doc('Purchase Receipt',self.branch,status='Submitted')
        StockMovement.objects.create(document=receipt,posting_date=timezone.localdate(),product=self.item,warehouse=self.warehouse,quantity=100,value=1000)
        self.tax=Account.objects.create(code='OUTPUT',name='Synthetic output tax',account_type='Tax')
        self.rule=SalesTaxRule.objects.create(code='SYNTHETIC',branch=self.branch,name='Test rate',rate=Decimal('5'),account=self.tax,reference='Synthetic test only')
        self.client.force_login(self.user)
    def draft(self,rows=((2,'100'),),kind='amount',value='0',rule=True):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        for qty,rate in rows:Line.objects.create(document=doc,product=self.item,warehouse=self.warehouse,quantity=qty,rate=rate)
        p.inputs(doc,kind,value,self.rule.pk if rule else '',capture=True);doc.save();return doc
    def post(self,**kwargs):return s.submit(self.draft(**kwargs),self.user)
    def credit(self,sale,qty,source=None):
        source=source or sale.lines.order_by('pk').first()
        return s.submit(s.return_sale(sale,self.user,[],'Good','Original item returned',{str(source.pk):str(qty)},self.warehouse),self.user)
    def phone(self):
        doc=s.new_doc('Mobile Intake',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer,data={'device':{'imeis':[{'number':imei(900)}],'asking_price':'65000','minimum_price':'60000'}})
        Line.objects.create(document=doc,product=self.product,quantity=1,rate=50000)
        return Device.objects.get(pk=s.submit(doc,self.user).data['device'])
    def phone_draft(self,phone,discount):
        doc=self.draft(rows=(),value=discount)
        Line.objects.create(document=doc,product=self.product,device=phone,warehouse=self.warehouse,quantity=1,rate=65000);return doc
    def assert_balanced(self,doc):
        self.assertEqual(doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'))['d'],doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'))['c'])
    def test_amount_discount_before_exclusive_tax_and_stock_cost_unchanged(self):
        doc=self.post(value='20')
        self.assertEqual(doc.total,189);self.assertEqual(doc.outstanding,189)
        self.assertEqual(doc.ledger.get(account__code='sales').credit,180);self.assertEqual(doc.ledger.get(account=self.tax).credit,9)
        self.assertEqual(doc.stock.get().value,-20);self.assertEqual(doc.lines.get().cost,10)
        self.assertEqual(s.on_hand(self.item,self.warehouse),(98,980));self.assert_balanced(doc)
    def test_percentage_discount_and_default_no_tax(self):
        doc=self.post(kind='percent',value='12.50',rule=False)
        self.assertEqual(doc.total,175);self.assertEqual(doc.data['pricing']['discount'],'25.00');self.assertFalse(doc.ledger.filter(account=self.tax).exists())
    def test_no_adjustments_retains_previous_total(self):
        doc=self.draft(rule=False);doc.data={};doc.save();doc=s.submit(doc,self.user)
        self.assertEqual(doc.total,200);self.assertEqual(doc.data['pricing']['net_total'],'200.00')
    def test_paisa_allocation_stable_and_reconciled(self):
        doc=self.post(rows=((1,'.03'),(1,'.03'),(1,'.03')),value='.01')
        lines=list(doc.lines.order_by('pk'));parts=[line.snapshot['pricing'] for line in lines]
        self.assertEqual([s.money(part['discount']) for part in parts],[Decimal('.01'),0,0])
        self.assertEqual(sum(s.money(part['total']) for part in parts),doc.total)
        self.assertTrue(p.frozen_lines(doc,lines));self.assert_balanced(doc)
    def test_half_even_tax_rounding(self):
        one=self.post(rows=((1,'.10'),));two=self.post(rows=((1,'.30'),))
        self.assertEqual(one.data['pricing']['tax_total'],'0.00');self.assertEqual(two.data['pricing']['tax_total'],'0.02')
    def test_full_discount_creates_zero_ar_and_tax_but_keeps_cost(self):
        doc=self.post(kind='percent',value='100')
        self.assertEqual(doc.total,0);self.assertEqual(doc.ledger.get(account__code='cogs').debit,20);self.assertFalse(doc.ledger.filter(account=self.tax).exists());self.assert_balanced(doc)
    def test_invalid_discount_values_roll_back(self):
        for kind,value in [('amount','201'),('percent','100.01'),('amount','-1'),('amount','1.001'),('amount','NaN'),('invalid','1')]:
            doc=self.draft(kind=kind,value=value)
            with self.subTest(kind=kind,value=value):
                with self.assertRaises(ValidationError):s.submit(doc,self.user)
                self.assertFalse(doc.ledger.exists());self.assertFalse(doc.stock.exists())
    def test_invalid_adjustment_shape_rejected(self):
        doc=self.draft();doc.data['adjustments']=[];doc.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
    def test_stale_rule_version_blocks_post_until_resaved(self):
        doc=self.draft();self.rule.rate=Decimal('7');self.rule.save()
        with self.assertRaisesMessage(ValidationError,'changed'):s.submit(doc,self.user)
        self.assertFalse(doc.stock.exists());doc.refresh_from_db();p.inputs(doc,rule_id=self.rule.pk,capture=True);doc.save()
        self.assertEqual(s.submit(doc,self.user).total,214)
    def test_missing_version_disabled_and_foreign_rules_rejected(self):
        doc=self.draft();doc.data['adjustments']['tax_version']='';doc.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        other=Branch.objects.create(code='OTHER',name='Other',company='Other')
        self.rule.branch=other;self.rule.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.rule.branch=self.branch;self.rule.enabled=False;self.rule.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
    def test_invoice_discount_cannot_bypass_phone_minimum_using_tax(self):
        phone=self.phone();doc=self.phone_draft(phone,'6000')
        actor=get_user_model().objects.create_user('sales');self.seller.user=actor;self.seller.save()
        with self.assertRaises(PermissionDenied):s.submit(doc,actor)
        phone.refresh_from_db();self.assertEqual(phone.status,'Available');self.assertFalse(doc.ledger.exists())
        doc=s.submit(doc,self.user);phone.refresh_from_db();self.assertEqual(phone.sold_price,59000);self.assertEqual(doc.total,61950)
    def test_phone_return_uses_original_discount_tax_and_cost(self):
        phone=self.phone();sale=s.submit(self.phone_draft(phone,'5000'),self.user)
        self.rule.rate=Decimal('20');self.rule.enabled=False;self.rule.save()
        credit=s.return_sale(sale,self.user,[str(phone.pk)],'Good','Returned')
        self.assertEqual(credit.total,-63000);credit=s.submit(credit,self.user)
        self.assertEqual(credit.ledger.get(account=self.tax).debit,3000);self.assertEqual(credit.ledger.get(account__code='sales').debit,60000)
        self.assertEqual(credit.stock.get().value,50000);self.assertEqual(credit.total,-sale.total);self.assert_balanced(credit)
    def test_partial_accessory_returns_reverse_all_paisa_at_original_rate(self):
        sale=self.post(rows=((3,'100.01'),),value='10.01');credits=[]
        self.rule.rate=Decimal('18');self.rule.account=Account.objects.create(code='NEW-TAX',name='New output tax',account_type='Current Liability');self.rule.save()
        for quantity in ('.333','.667','2'):credits.append(self.credit(sale,quantity))
        self.assertEqual(sum(credit.total for credit in credits),-sale.total)
        self.assertEqual(sum(credit.ledger.get(account=self.tax).debit for credit in credits),s.money(sale.data['pricing']['tax_total']))
        self.assertEqual(s.on_hand(self.item,self.warehouse),(100,1000))
        for credit in credits:self.assert_balanced(credit)
    def test_tiny_fractional_returns_never_produce_negative_ledger_amount(self):
        sale=self.post(rows=((1,'.03'),),value='.02');credits=[self.credit(sale,'.2') for _ in range(5)]
        self.assertEqual(sum(c.total for c in credits),-sale.total)
        self.assertFalse(LedgerEntry.objects.filter(debit__lt=0).exists());self.assertFalse(LedgerEntry.objects.filter(credit__lt=0).exists())
    def test_return_credit_applies_gross_outstanding_without_extra_tax(self):
        sale=self.post(value='20');credit=self.credit(sale,1)
        s.apply_return_credit(credit,credit.total.copy_abs(),self.user);sale.refresh_from_db();credit.refresh_from_db()
        self.assertEqual(sale.outstanding,Decimal('94.50'));self.assertEqual(credit.outstanding,0)
        self.assertEqual(LedgerEntry.objects.filter(account=self.tax).aggregate(d=Sum('debit'),c=Sum('credit')),{'d':Decimal('4.50'),'c':Decimal('9')})
    def test_snapshots_must_reconcile_before_return(self):
        sale=self.post();line=sale.lines.get();line.snapshot['pricing']['net']='999';line.save()
        with self.assertRaises(ValidationError):self.credit(sale,1)
    def test_receipts_show_subtotal_discount_net_tax_all_paper_sizes(self):
        sale=self.post(value='20')
        for paper in ('58','thermal','a4'):
            response=self.client.get(reverse('receipt',args=[sale.pk]),{'paper':paper})
            self.assertContains(response,'Item subtotal PKR 200.00');self.assertContains(response,'Invoice discount PKR 20.00');self.assertContains(response,'Test rate (5%)');self.assertContains(response,'Total PKR 189.00')
        credit=self.credit(sale,1);response=self.client.get(reverse('receipt',args=[credit.pk]))
        self.assertEqual(response.context['lines'][0].receipt_amount,-100);self.assertContains(response,'Invoice discount PKR -10.00')
    def test_register_and_staff_reports_exclude_tax_from_sales(self):
        sale=self.post(value='20');_,rows=reports.run('Sales Register',self.user,{})
        self.assertEqual(rows[0]['Net sales before tax'],180);self.assertEqual(rows[0]['Sales tax'],9);self.assertEqual(rows[0]['Total'],189)
        _,rows=reports.run('Staff Summary',self.user,{});self.assertEqual(rows[0]['Net Sales'],180)
        self.credit(sale,1);_,rows=reports.run('Staff Summary',self.user,{});self.assertEqual(rows[0]['Net Sales'],90)
    def test_device_profit_and_economics_use_discounted_net_revenue(self):
        phone=self.phone();s.submit(self.phone_draft(phone,'5000'),self.user)
        _,rows=reports.run('Device Profit',self.user,{});self.assertEqual(rows[0]['Sale'],60000);self.assertEqual(rows[0]['Margin'],10000)
        _,rows=reports.run('Mobile Device Economics',self.user,{});self.assertEqual(rows[0]['Lifetime Net Sales'],60000);self.assertEqual(rows[0]['Posted Margin After Repairs'],10000)
    def test_sales_draft_form_captures_current_rule_and_total(self):
        data=dict(branch=self.branch.pk,posting_date=str(timezone.localdate()),customer=self.customer.pk,seller=self.seller.pk,warehouse=self.warehouse.pk,
            invoice_discount_type='percent',invoice_discount_value='10',sales_tax_rule=self.rule.pk,
            **{'lines-TOTAL_FORMS':1,'lines-INITIAL_FORMS':0,'lines-MIN_NUM_FORMS':0,'lines-MAX_NUM_FORMS':1000,'lines-0-product':self.item.pk,'lines-0-warehouse':self.warehouse.pk,'lines-0-quantity':2,'lines-0-rate':100})
        response=self.client.post(reverse('new',args=['Sales Invoice']),data);self.assertEqual(response.status_code,302)
        doc=Document.objects.get(kind='Sales Invoice');self.assertEqual(doc.total,189);self.assertEqual(doc.data['adjustments']['tax_version'],p.tax_version(self.rule));self.assertEqual(s.submit(doc,self.user).total,189)
    def test_pos_exact_retry_preserves_discount_tax_payment_and_till(self):
        shift=s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'1000'}),self.user)
        data=dict(request_key=str(uuid.uuid4()),branch=str(self.branch.pk),customer=str(self.customer.pk),seller=str(self.seller.pk),handler=str(self.handler.pk),
            items=[dict(product=str(self.item.pk),warehouse=str(self.warehouse.pk),quantity=2,rate='100')],adjustments=dict(discount_type='amount',discount_value='20',tax_rule=str(self.rule.pk),tax_version=p.tax_version(self.rule)),payments=[dict(mode='Cash',amount='189',cash_tendered='200')])
        one=self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json');two=self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json')
        self.assertEqual(one.status_code,200,one.content);self.assertEqual(one.json(),two.json())
        sale=Document.objects.get(pk=one.json()['id']);self.assertEqual(sale.total,189);self.assertEqual(sale.outstanding,0);self.assertEqual(s.cash_summary(sale)['change_returned'],11);self.assertEqual(s.till_balance(shift)['expected'],'1189.00');self.assertEqual(sale.linked.count(),1)
        data['adjustments']['discount_value']='21';self.assertEqual(self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json').status_code,400)
    def test_tax_setup_is_owner_only_and_does_not_preset_rates(self):
        actor=get_user_model().objects.create_user('sales');self.seller.user=actor;self.seller.save();self.client.force_login(actor)
        for route in ('tax_rules','tax_rule_new'):
            self.assertEqual(self.client.get(reverse(route)).status_code,403)
        self.client.force_login(self.user);self.assertEqual(self.client.get(reverse('tax_rules')).status_code,200)
        self.assertEqual(SalesTaxRule.objects.count(),1)
    def test_owner_tax_setup_and_open_shift_guard(self):
        data=dict(name='Client instructed test',rate='7.1250',account=self.tax.pk,reference='Synthetic instruction',enabled='on')
        response=self.client.post(reverse('tax_rule_new')+'?branch='+str(self.branch.pk),data);self.assertEqual(response.status_code,302)
        rule=SalesTaxRule.objects.get(name=data['name']);self.assertEqual(rule.rate,Decimal('7.125'))
        self.assertTrue(Audit.objects.filter(action='Save sales tax rule').exists())
        s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'0'}),self.user)
        response=self.client.post(reverse('tax_rule_edit',args=[rule.pk]),{**data,'rate':'8'});self.assertContains(response,'Close this shop');rule.refresh_from_db();self.assertEqual(rule.rate,Decimal('7.125'))
    def test_bad_tax_rates_references_and_accounts_rejected(self):
        for rate,reference,account in [('100.0001','Test',self.tax),('-1','Test',self.tax),('1.12345','Test',self.tax),('5','',self.tax),('5','Test',Account.objects.get(code='cash'))]:
            self.rule.rate=rate;self.rule.reference=reference;self.rule.account=account
            with self.subTest(rate=rate,reference=reference):
                with self.assertRaises(ValidationError):self.rule.full_clean()
    def test_rule_output_account_cannot_be_disabled_or_repurposed(self):
        self.tax.enabled=False
        with self.assertRaises(ValidationError):self.tax.full_clean()
        self.tax.enabled=True;self.tax.is_group=True
        with self.assertRaises(ValidationError):self.tax.full_clean()
