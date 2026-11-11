"""Line-level returns, valuation, credit application and refund protection."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import close_old_connections,connections
from django.db.models import Sum
from django.test import TestCase,TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,Workflows
from . import services as s
from .returns import options,used_quantities


def seed(test):
    test.__dict__.update(fixtures())
    test.item=Product.objects.create(code='RETURN-CASE',name='Case',selling_price=500)
    receipt=s.new_doc('Purchase Receipt',test.branch,status='Submitted')
    StockMovement.objects.create(document=receipt,posting_date=timezone.localdate(),product=test.item,warehouse=test.warehouse,quantity=10,value=1000)
    test.shift=s.submit(s.new_doc('POS Opening Entry',test.branch,handler=test.handler,till=test.till,data={'opening_float':'1000'}),test.user)


class AccessoryReturns(TestCase):
    def setUp(self):seed(self)
    def sale(self,rows=((2,500),),paid=0):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        for qty,rate in rows:Line.objects.create(document=doc,product=self.item,warehouse=self.warehouse,quantity=qty,rate=rate)
        return s.checkout(doc,self.user,[dict(mode='Cash',amount=str(paid))] if paid else [],self.handler)
    def credit(self,sale,qty=1,line=None,post=True):
        line=line or sale.lines.order_by('pk').first()
        doc=s.return_sale(sale,self.user,[],'Received in original packaging','Customer accessory return',{str(line.pk):str(qty)},self.warehouse)
        return s.submit(doc,self.user) if post else doc
    def refund(self,credit,amount):
        return s.submit(s.new_doc('Payment Entry',self.branch,customer=self.customer,handler=self.handler,reference=credit,data=dict(direction='Pay',mode='Cash',amount=str(amount))),self.user)
    def test_partial_quantity_restock_credits_original_price_and_cost(self):
        sale=self.sale();credit=self.credit(sale)
        self.assertEqual(credit.total,-500);self.assertEqual(credit.outstanding,-500)
        line=credit.lines.get();self.assertEqual(line.rate,500);self.assertEqual(line.cost,100)
        self.assertEqual(line.snapshot['return_line'],str(sale.lines.get().pk));self.assertEqual(s.on_hand(self.item,self.warehouse),(9,900))
        self.assertEqual(credit.staff_names['returned_by'],self.user.username)
    def test_same_sku_different_prices_return_exact_selected_line(self):
        sale=self.sale(((2,500),(3,700)));source=sale.lines.order_by('pk').last()
        source.cost=120;source.save()
        credit=self.credit(sale,line=source)
        self.assertEqual(credit.total,-700);self.assertEqual(credit.lines.get().cost,120)
        self.assertEqual(credit.stock.get().value,120)
        self.assertEqual([line.remaining_quantity for line in options(sale)],[2,2])
    def test_cumulative_returns_cannot_exceed_original_line(self):
        sale=self.sale();self.credit(sale)
        with self.assertRaises(ValidationError):self.credit(sale,2)
        self.credit(sale);self.assertEqual(options(sale)[0].remaining_quantity,0)
        with self.assertRaises(ValidationError):self.credit(sale)
    def test_stale_draft_is_revalidated_on_submission(self):
        sale=self.sale(((1,500),));one=self.credit(sale,post=False);two=self.credit(sale,post=False)
        s.submit(one,self.user);before=(StockMovement.objects.count(),LedgerEntry.objects.count())
        with self.assertRaises(ValidationError):s.submit(two,self.user)
        self.assertEqual((StockMovement.objects.count(),LedgerEntry.objects.count()),before)
        two.refresh_from_db();self.assertEqual(two.status,'Draft')
    def test_duplicate_draft_lines_cannot_bypass_quantity_cap(self):
        sale=self.sale(((1,500),));credit=self.credit(sale,post=False);line=credit.lines.get()
        Line.objects.create(document=credit,product=self.item,warehouse=self.warehouse,quantity=1,rate=line.rate,snapshot=line.snapshot)
        with self.assertRaises(ValidationError):s.submit(credit,self.user)
        self.assertFalse(credit.stock.exists())
    def test_changed_return_rate_rolls_back(self):
        sale=self.sale();credit=self.credit(sale,post=False);credit.lines.update(rate=999)
        with self.assertRaises(ValidationError):s.submit(credit,self.user)
        self.assertFalse(credit.stock.exists());self.assertFalse(credit.ledger.exists())
    def test_foreign_invoice_line_rejected(self):
        sale=self.sale();other=self.sale();credit=self.credit(sale,post=False);line=credit.lines.get()
        line.snapshot['return_line']=str(other.lines.get().pk);line.save()
        with self.assertRaises(ValidationError):s.submit(credit,self.user)
    def test_empty_selection_or_foreign_line_creates_no_credit(self):
        sale=self.sale();before=Document.objects.count()
        for quantities in ({},{'999999':'1'}):
            with self.assertRaises(ValidationError):s.return_sale(sale,self.user,[],'Good','Return',quantities,self.warehouse)
        self.assertEqual(Document.objects.count(),before)
    def test_invalid_quantities_rejected(self):
        sale=self.sale()
        for qty in ('0','-1','0.0001','NaN','Infinity'):
            with self.subTest(qty=qty):
                with self.assertRaises(ValidationError):self.credit(sale,qty)
    def test_final_fractional_return_preserves_sale_and_cost_paisa(self):
        sale=self.sale(((1,Decimal('.01')),));first=self.credit(sale,Decimal('.5'));second=self.credit(sale,Decimal('.5'))
        self.assertEqual(first.total,0);self.assertEqual(second.total,Decimal('-.01'))
        self.assertEqual(s.on_hand(self.item,self.warehouse),(10,1000))
        self.client.force_login(self.user);response=self.client.get(reverse('receipt',args=[second.pk]))
        self.assertEqual(response.context['lines'][0].receipt_amount,Decimal('-.01'))
    def test_legacy_return_without_marker_matches_price_and_cost(self):
        sale=self.sale(((2,500),(3,700)));source=sale.lines.order_by('pk').last()
        old=s.new_doc('Sales Invoice',self.branch,customer=self.customer,reference=sale,is_return=True,status='Submitted',total=-700)
        Line.objects.create(document=old,product=self.item,quantity=1,rate=700,cost=source.cost)
        self.assertEqual(used_quantities(sale)[str(source.pk)],1)
        credit=self.credit(sale,line=source);self.assertEqual(credit.total,-700)
    def test_foreign_or_disabled_receiving_warehouse_rejected(self):
        sale=self.sale();branch=Branch.objects.create(code='FOREIGN',name='Foreign',company='Other')
        warehouse=Warehouse.objects.create(code='FOREIGN-STORE',name='Foreign stock',branch=branch)
        with self.assertRaises(ValidationError):s.return_sale(sale,self.user,[],'Good','Return',{str(sale.lines.get().pk):'1'},warehouse)
        self.warehouse.enabled=False;self.warehouse.save()
        with self.assertRaises(ValidationError):self.credit(sale)
    def test_closed_return_date_blocks_stock_and_credit(self):
        sale=self.sale();credit=self.credit(sale,post=False)
        s.new_doc('Mobile Daily Closing',self.branch,status='Submitted')
        with self.assertRaises(ValidationError):s.submit(credit,self.user)
        self.assertFalse(credit.stock.exists())
    def test_disabled_original_seller_does_not_block_return_attribution(self):
        sale=self.sale();self.seller.enabled=False;self.seller.save()
        credit=self.credit(sale);self.assertEqual(credit.staff_names['seller'],'Ali Hassan')
        self.assertEqual(credit.staff_names['returned_by'],self.user.username)
    def test_extra_invoice_taxes_or_header_adjustment_requires_review(self):
        sale=self.sale();sale.total+=10;sale.save()
        with self.assertRaisesMessage(ValidationError,'reviewed credit'):self.credit(sale)
    def test_refund_on_paid_sale_updates_till_only_for_actual_refund(self):
        sale=self.sale(paid=1000);credit=self.credit(sale)
        self.assertEqual(s.till_balance(self.shift)['expected'],'2000.00')
        payment=self.refund(credit,500);credit.refresh_from_db()
        self.assertEqual(credit.outstanding,0);self.assertEqual(payment.data['till_cash'],'-500.00')
        self.assertEqual(s.till_balance(self.shift)['expected'],'1500.00')
    def test_unpaid_credit_application_moves_no_cash_or_stock(self):
        sale=self.sale();credit=self.credit(sale);before=(StockMovement.objects.count(),s.till_balance(self.shift)['expected'])
        journal=s.apply_return_credit(credit,500,self.user);sale.refresh_from_db();credit.refresh_from_db()
        self.assertEqual(sale.outstanding,500);self.assertEqual(credit.outstanding,0)
        self.assertEqual((StockMovement.objects.count(),s.till_balance(self.shift)['expected']),before)
        self.assertEqual(journal.ledger.count(),2)
        totals=journal.ledger.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(totals['d'],totals['c'])
    def test_part_paid_invoice_applies_debt_then_refunds_remaining_credit(self):
        sale=self.sale(paid=700);credit=self.credit(sale)
        with self.assertRaisesMessage(ValidationError,'Apply return credit'):self.refund(credit,500)
        s.apply_return_credit(credit,300,self.user);self.refund(credit,200)
        sale.refresh_from_db();credit.refresh_from_db();self.assertEqual((sale.outstanding,credit.outstanding),(0,0))
        self.assertEqual(s.till_balance(self.shift)['expected'],'1500.00')
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('receipt',args=[credit.pk])),'Credit applied PKR 300.00')
    def test_over_refund_and_over_credit_application_rejected(self):
        sale=self.sale();credit=self.credit(sale)
        with self.assertRaises(ValidationError):s.apply_return_credit(credit,501,self.user)
        with self.assertRaises(ValidationError):s.apply_return_credit(credit,-1,self.user)
        s.apply_return_credit(credit,500,self.user)
        with self.assertRaises(ValidationError):s.apply_return_credit(credit,1,self.user)
    def test_posted_return_cannot_be_cancelled_in_place(self):
        credit=self.credit(self.sale())
        with self.assertRaisesMessage(ValidationError,'review'):s.cancel(credit,self.user)
    def test_cross_shop_user_cannot_return_or_apply_credit(self):
        sale=self.sale();credit=self.credit(sale)
        branch=Branch.objects.create(code='OTHER',name='Other',company=self.branch.company)
        user=get_user_model().objects.create_user('other-user');Staff.objects.create(code='OTHER-U',name='Other',branch=branch,user=user,manager=True)
        with self.assertRaises(PermissionDenied):s.return_sale(sale,user,[],'Good','Return',{str(sale.lines.get().pk):'1'})
        with self.assertRaises(PermissionDenied):s.apply_return_credit(credit,500,user)
    def test_return_form_and_http_draft_retry_preserve_line_selection(self):
        sale=self.sale();self.client.force_login(self.user);line=sale.lines.get()
        self.assertContains(self.client.get(reverse('detail',args=[sale.pk])),f'return_qty_{line.pk}')
        payload=dict(request_key=str(uuid.uuid4()),return_warehouse=str(self.warehouse.pk),condition='Sealed',reason='Wrong accessory',**{f'return_qty_{line.pk}':'1'})
        first=self.client.post(reverse('action',args=[sale.pk,'return']),payload)
        second=self.client.post(reverse('action',args=[sale.pk,'return']),payload)
        self.assertEqual(first.status_code,302);self.assertEqual(first.url,second.url)
        self.assertEqual(sale.linked.filter(is_return=True).count(),1)
    def test_refund_prefill_blocks_unpaid_then_sets_pay_and_original_credit(self):
        sale=self.sale(paid=700);credit=self.credit(sale);self.client.force_login(self.user)
        url=reverse('new',args=['Payment Entry'])+'?invoice='+str(credit.pk)
        self.assertEqual(self.client.get(url).status_code,400)
        s.apply_return_credit(credit,300,self.user)
        response=self.client.get(url);self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['form']['direction'].value(),'Pay');self.assertEqual(response.context['form']['amount'].value(),'200.00')
    def test_credit_application_http_retry_does_not_apply_twice(self):
        sale=self.sale();credit=self.credit(sale);self.client.force_login(self.user)
        payload=dict(request_key=str(uuid.uuid4()),amount='500')
        url=reverse('action',args=[credit.pk,'apply-return-credit'])
        first=self.client.post(url,payload);second=self.client.post(url,payload)
        self.assertEqual(first.status_code,302);self.assertEqual(first.url,second.url)
        sale.refresh_from_db();self.assertEqual(sale.outstanding,500)
    def test_return_edit_keeps_price_identity_and_negative_total(self):
        sale=self.sale();credit=self.credit(sale,post=False);self.client.force_login(self.user)
        response=self.client.get(reverse('edit',args=['Sales Invoice',credit.pk]));form=response.context['form'];lines=response.context['lines']
        self.assertTrue(form.fields['customer'].disabled);self.assertTrue(lines.forms[0].fields['rate'].disabled)
        self.assertEqual(len(lines.forms),1);self.assertNotContains(response,'data-add-line')
        line=credit.lines.get()
        payload=dict(posting_date=str(timezone.localdate()),warehouse=str(self.warehouse.pk),notes='Half a unit received',**{
            'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'1','lines-MIN_NUM_FORMS':'0','lines-MAX_NUM_FORMS':'1000',
            'lines-0-id':str(line.pk),'lines-0-product':str(self.item.pk),'lines-0-device':'','lines-0-warehouse':str(self.warehouse.pk),
            'lines-0-quantity':'0.5','lines-0-rate':'999'})
        saved=self.client.post(reverse('edit',args=['Sales Invoice',credit.pk]),payload)
        self.assertEqual(saved.status_code,302,saved.content)
        credit.refresh_from_db();line.refresh_from_db();self.assertEqual(credit.total,-250);self.assertEqual(line.rate,500)
        credit=s.submit(credit,self.user);self.assertEqual(s.on_hand(self.item,self.warehouse),(Decimal('8.5'),850))
    def test_pos_sale_is_in_sales_returns_history_and_can_return_accessory(self):
        sale=s.new_doc('POS Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=sale,product=self.item,warehouse=self.warehouse,quantity=2,rate=500)
        sale=s.checkout(sale,self.user,[dict(mode='Cash',amount='1000')],self.handler)
        self.client.force_login(self.user)
        self.assertContains(self.client.get(reverse('collection',args=['Sales Invoice'])),self.customer.name)
        credit=self.credit(sale);self.assertEqual(credit.reference,sale);self.assertEqual(credit.total,-500)


class ReturnConcurrency(TransactionTestCase):
    def test_two_drafts_cannot_both_return_the_last_unit(self):
        seed(self)
        sale=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        line=Line.objects.create(document=sale,product=self.item,warehouse=self.warehouse,quantity=1,rate=500)
        sale=s.checkout(sale,self.user,[],None)
        docs=[s.return_sale(sale,self.user,[],'Good','Concurrent return',{str(line.pk):'1'},self.warehouse) for _ in (1,2)]
        ready=Barrier(2)
        def submit(pk):
            close_old_connections()
            try:
                ready.wait(timeout=10);s.submit(Document.objects.get(pk=pk),get_user_model().objects.get(pk=self.user.pk));return 'posted'
            except ValidationError:return 'blocked'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(submit,[doc.pk for doc in docs]))
        self.assertCountEqual(results,['posted','blocked']);self.assertEqual(s.on_hand(self.item,self.warehouse),(10,1000))
