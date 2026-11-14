import json,uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import close_old_connections,connections
from django.db.models import Sum
from django.test import TestCase,TransactionTestCase,Client
from django.urls import reverse
from django.utils import timezone
from .models import *
from . import services as s
from .access import REPORTS
from .reports import run

def fixtures():
    user=get_user_model().objects.create_superuser('manager',password='Unrelated-test-password-729!')
    branch=Branch.objects.create(code='TEST',name='Test Shop',company='Test Company')
    warehouse=Warehouse.objects.create(code='STORE',name='Stores',branch=branch)
    till=Till.objects.create(code='TEST-TILL',name='Main till',branch=branch)
    buyer=Staff.objects.create(code='BUYER',name='Usman Khan',branch=branch,can_buy=True)
    seller=Staff.objects.create(code='SELLER',name='Ali Hassan',branch=branch,can_sell=True)
    handler=Staff.objects.create(code='HANDLER',name='Ayesha Malik',branch=branch,can_handle_payments=True)
    accounts={}
    for name in ('receivable','payable','inventory','stock_received','sales','cogs','expense','cash','bank'):
        accounts[name]=str(Account.objects.create(code=name,name=name,account_type='Cash' if name=='cash' else 'Bank' if name=='bank' else '').pk)
    ShopSettings.objects.create(branch=branch,accounts=accounts,payment_methods={'Cash':accounts['cash'],'Bank Transfer':accounts['bank']})
    customer=Party.objects.create(code='CUSTOMER',kind='Customer',name='Demo Customer')
    supplier=Party.objects.create(code='SUPPLIER',kind='Supplier',name='Demo Supplier')
    model=PhoneModel.objects.create(code='IP13',name='iPhone 13',brand='Apple')
    product=Product.objects.create(code='IPHONE',name='iPhone 13 128GB',model=model,serialized=True)
    return dict(user=user,branch=branch,warehouse=warehouse,till=till,buyer=buyer,seller=seller,handler=handler,customer=customer,supplier=supplier,product=product)

def fund(branch,user,amount='1000000',mode='Cash'):
    """Owner money put into a test shop. A payment can only leave an account that holds the money (decision D8), so a test that pays a supplier
    from a shop starts here, as a real shop starts with the owner's capital."""
    capital,_=Account.objects.get_or_create(code='TEST-CAPITAL',defaults=dict(name='Owner capital (tests)',account_type='Equity',root_type='Equity'))
    doc=s.new_doc('Voucher',branch,notes='Owner capital for the test',data=dict(type='capital',amount=str(amount),account=str(capital.pk),mode=mode,description='Owner capital for the test'))
    return s.submit(doc,user)

def imei(n):
    body='99000019'+f'{n:06d}'
    return next(body+str(c) for c in range(10) if s.valid_imei(body+str(c)))

class Workflows(TestCase):
    @classmethod
    def setUpTestData(cls):
        for k,v in fixtures().items():setattr(cls,k,v)
    def intake(self,n=1,cost=50000):
        doc=s.new_doc('Mobile Intake',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer,
            demo_batch='TEST-DEMO',data={'device':dict(imeis=[dict(number=imei(n)),dict(number=imei(n+1000))],asking_price='65000',minimum_price='60000',inspection_status='Passed',warranty_days=7,warranty_terms='Original warranty')})
        Line.objects.create(document=doc,product=self.product,warehouse=self.warehouse,quantity=1,rate=cost)
        result=s.submit(doc,self.user)
        return result,Document.objects.get(pk=result.data['purchase_receipt']),Device.objects.get(pk=result.data['device'])
    def sale(self,device,rate=65000):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=self.warehouse,quantity=1,rate=rate)
        return doc
    def assert_balanced(self):
        totals=LedgerEntry.objects.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(totals['d'],totals['c'])
    def test_receive_bill_and_payment(self):
        intake,receipt,device=self.intake()
        self.assertEqual(device.purchase_cost,50000);self.assertEqual(device.imeis.count(),2)
        bill=s.supplier_bill(receipt,self.user);self.assertEqual(s.supplier_bill(receipt,self.user),bill)
        s.submit(bill,self.user)
        pe=s.new_doc('Payment Entry',self.branch,supplier=self.supplier,handler=self.handler,reference=bill,data=dict(amount='20000',direction='Pay',mode='Cash'))
        with self.assertRaisesMessage(ValidationError,'has only'):s.submit(pe,self.user)          # nothing in the till yet: refused (D8)
        fund(self.branch,self.user,'20000');pe.refresh_from_db()
        s.submit(pe,self.user);bill.refresh_from_db();self.assertEqual(bill.outstanding,30000);self.assert_balanced()
    def test_duplicate_imei_rolls_back_receiving(self):
        self.intake();before=(Device.objects.count(),Document.objects.count(),StockMovement.objects.count())
        with self.assertRaises(ValidationError):self.intake()
        self.assertEqual(Device.objects.count(),before[0]);self.assertEqual(StockMovement.objects.count(),before[2])
    def test_invalid_imei(self):
        d=Device.objects.create(code='INVALID',product=self.product,warehouse=self.warehouse)
        with self.assertRaises(ValidationError):s.update_imeis(d,[{'number':'123456789012345'},{}],self.user)
    def test_cross_slot_imei_uniqueness(self):
        _,_,device=self.intake();other=Device.objects.create(code='OTHER',product=self.product,warehouse=self.warehouse)
        with self.assertRaises(ValidationError):s.update_imeis(other,[{'number':imei(2)},{'number':imei(1)}],self.user)
    def test_partial_and_split_checkout(self):
        _,_,device=self.intake();doc=s.checkout(self.sale(device),self.user,[dict(mode='Cash',amount='20000'),dict(mode='Bank Transfer',amount='15000',reference='BANK-REF')],self.handler)
        self.assertEqual(doc.outstanding,30000);device.refresh_from_db();self.assertEqual(device.status,'Sold')
        self.assertEqual(doc.linked.filter(kind='Payment Entry').count(),2);self.assertFalse(doc.demo_batch);self.assertIn('TEST-DEMO',doc.data['initial_record_batches']);self.assert_balanced()
    def test_failed_second_payment_rolls_back_whole_sale(self):
        _,_,device=self.intake();doc=self.sale(device)
        with self.assertRaises(ValidationError):s.checkout(doc,self.user,[dict(mode='Cash',amount='30000'),dict(mode='Gold coins',amount='20000')],self.handler)
        device.refresh_from_db();doc.refresh_from_db();self.assertEqual(device.status,'Available');self.assertEqual(doc.status,'Draft');self.assertFalse(doc.ledger.exists())
    def test_double_selling_rejected(self):
        _,_,device=self.intake();s.checkout(self.sale(device),self.user,[],None)
        with self.assertRaises(ValidationError):s.checkout(self.sale(device),self.user,[],None)
    def test_cross_shop_denied(self):
        other=Branch.objects.create(code='OTHER',name='Other',company='Test')
        user=get_user_model().objects.create_user('cashier',password='Some-test-password-729!');self.handler.user=user;self.handler.save()
        doc=s.new_doc('Payment Entry',other)
        with self.assertRaises(PermissionDenied):s.submit(doc,user)
    def test_invalid_staff_capability(self):
        _,_,device=self.intake();doc=self.sale(device);doc.seller=self.handler;doc.save()
        with self.assertRaises(ValidationError):s.checkout(doc,self.user,[],None)
    def test_return_preserves_snapshots_and_requires_reinspection(self):
        _,_,device=self.intake();doc=s.checkout(self.sale(device),self.user,[],None)
        device.refresh_from_db();device.warranty_days=90;device.warranty_terms='Changed after sale';device.save(update_fields=['warranty_days','warranty_terms'])
        returned=s.return_sale(doc,self.user,[str(device.pk)],'Cable received','Customer return');returned=s.submit(returned,self.user)
        device.refresh_from_db();self.assertEqual(device.status,'Holding');self.assertEqual(device.inspection_status,'Pending')   # returned phones wait for inspection before resale
        self.assertEqual(returned.lines.get().snapshot['warranty_days'],7);self.assertEqual(returned.outstanding,-65000);self.assert_balanced()
    def test_duplicate_return_rejected(self):
        _,_,device=self.intake();doc=s.checkout(self.sale(device),self.user,[],None)
        first=s.return_sale(doc,self.user,[str(device.pk)],'Same','Return');s.submit(first,self.user)
        second=s.return_sale(doc,self.user,[str(device.pk)],'Same','Again')
        with self.assertRaises(ValidationError):s.submit(second,self.user)
    def test_customer_advance_allocation_without_second_cash_entry(self):
        _,_,device=self.intake();sale=s.checkout(self.sale(device),self.user,[],None)
        advance=s.new_doc('Payment Entry',self.branch,customer=self.customer,handler=self.handler,data=dict(amount='10000',direction='Receive',mode='Cash'))
        advance=s.submit(advance,self.user);cash_before=LedgerEntry.objects.filter(account__code='cash').aggregate(d=Sum('debit'))['d']
        s.allocate_advance(advance,sale,7000,self.user);sale.refresh_from_db();advance.refresh_from_db()
        self.assertEqual(sale.outstanding,58000);self.assertEqual(s.money(advance.data['unallocated']),3000)
        self.assertEqual(LedgerEntry.objects.filter(account__code='cash').aggregate(d=Sum('debit'))['d'],cash_before);self.assert_balanced()
    def test_no_overpayment(self):
        _,_,device=self.intake();doc=self.sale(device)
        with self.assertRaises(ValidationError):s.checkout(doc,self.user,[dict(mode='Cash',amount='65001')],self.handler)
    def test_transfer_changes_location_without_value_change(self):
        _,_,device=self.intake();target=Warehouse.objects.create(code='COUNTER',name='Counter',branch=self.branch)
        doc=s.new_doc('Stock Entry',self.branch,warehouse=self.warehouse,data=dict(target_warehouse=str(target.pk)))
        Line.objects.create(document=doc,product=self.product,device=device,quantity=1,rate=0)
        before=StockMovement.objects.aggregate(v=Sum('value'))['v'];s.submit(doc,self.user);device.refresh_from_db()
        self.assertEqual(device.warehouse,target);self.assertEqual(StockMovement.objects.aggregate(v=Sum('value'))['v'],before)
    def test_payment_cancellation_restores_invoice_balance(self):
        _,_,device=self.intake();doc=s.checkout(self.sale(device),self.user,[dict(mode='Cash',amount='10000')],self.handler)
        pe=doc.linked.get(kind='Payment Entry');s.cancel(pe,self.user);doc.refresh_from_db();self.assertEqual(doc.outstanding,65000);self.assert_balanced()
    def test_sale_cancel_reverses_stock_and_gl(self):
        _,_,device=self.intake();doc=s.checkout(self.sale(device),self.user,[],None)
        s.cancel(doc,self.user);device.refresh_from_db();self.assertEqual(device.status,'Available');self.assertEqual(s.on_hand(self.product,self.warehouse)[0],1);self.assert_balanced()
    def test_cannot_cancel_paid_invoice(self):
        _,_,device=self.intake();doc=s.checkout(self.sale(device),self.user,[dict(mode='Cash',amount='10000')],self.handler)
        with self.assertRaises(ValidationError):s.cancel(doc,self.user)
    def test_post_is_idempotent(self):
        _,_,device=self.intake();doc=self.sale(device);key=uuid.uuid4();payload={'sale':str(doc.pk)}
        one=s.once(key,self.user,payload,lambda:s.checkout(doc,self.user,[],None))
        two=s.once(key,self.user,payload,lambda:self.fail('Duplicate operation was executed'))
        self.assertEqual(one,two)
        with self.assertRaises(ValidationError):s.once(key,self.user,{'different':True},lambda:doc)
    def test_closing_blocks_open_pos_shift(self):
        opening=s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'0'});s.submit(opening,self.user)
        closing=s.new_doc('Mobile Daily Closing',self.branch,handler=self.handler,data={'counts':{}})
        with self.assertRaises(ValidationError):s.submit(closing,self.user)
    def test_closing_records_real_difference_without_balancing_journal(self):
        pe=s.new_doc('Payment Entry',self.branch,customer=self.customer,handler=self.handler,data=dict(amount='10000',direction='Receive',mode='Cash'));s.submit(pe,self.user)
        rows=s.balances(self.branch,timezone.localdate());counts={r['account']:dict(counted='9990',notes='Counted short by ten') for r in rows}
        closing=s.new_doc('Mobile Daily Closing',self.branch,handler=self.handler,data=dict(counts=counts));before=LedgerEntry.objects.count();s.submit(closing,self.user)
        self.assertEqual(LedgerEntry.objects.count(),before)
        with self.assertRaises(ValidationError):s.submit(s.new_doc('Payment Entry',self.branch),self.user)
    def test_imei_change_resets_verification(self):
        _,_,device=self.intake();old=device.imeis.get(slot=1);old.pta_status='Compliant';old.verified_on=timezone.localdate();old.save()
        s.update_imeis(device,[dict(number=imei(5),pta_status='Compliant',verified_on=timezone.localdate()),dict(number=imei(1001))],self.user)
        self.assertEqual(device.imeis.get(slot=1).pta_status,'Not Checked')
    def test_pos_requires_open_shift_and_closes(self):
        _,_,device=self.intake();doc=self.sale(device);doc.kind='POS Invoice';doc.save()
        with self.assertRaises(ValidationError):s.checkout(doc,self.user,[],None)
        opening=s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'1000'}),self.user)
        doc=s.checkout(doc,self.user,[dict(mode='Cash',amount='65000')],self.handler)
        closing=s.submit(s.new_doc('POS Closing Entry',self.branch,reference=opening,handler=self.handler,data={'counted_cash':'66000'}),self.user)
        opening.refresh_from_db();self.assertEqual(opening.state,'Closed');self.assertEqual(doc.outstanding,0)
    def test_exchange_both_directions_and_equal_value(self):
        for index,price in enumerate((60000,40000,50000),1):
            with self.subTest(price=price):
                _,incoming_receipt,_=self.intake(index*10,50000)
                incoming_receipt.data['trade_in_customer']=str(self.customer.pk);incoming_receipt.save()
                bill=s.submit(s.supplier_bill(incoming_receipt,self.user),self.user)
                _,_,outgoing=self.intake(index*10+1,30000)
                sale=s.checkout(self.sale(outgoing,price),self.user,[],None)
                exchange=s.new_doc('Mobile Exchange',self.branch,customer=self.customer,handler=self.handler,data=dict(sales_invoice=str(sale.pk),purchase_invoice=str(bill.pk),mode='Cash'))
                result=s.settle_exchange(exchange,self.user);sale.refresh_from_db();bill.refresh_from_db()
                self.assertEqual(result.state,'Settled');self.assertEqual(sale.outstanding,0);self.assertEqual(bill.outstanding,0);self.assert_balanced()
    def test_accessory_stock_and_sale(self):
        item=Product.objects.create(code='CASE',name='Case',selling_price=1200)
        receipt=s.new_doc('Purchase Receipt',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer)
        Line.objects.create(document=receipt,product=item,quantity=10,rate=500);s.submit(receipt,self.user)
        sale=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=sale,product=item,quantity=2,rate=1200);s.checkout(sale,self.user,[],None)
        self.assertEqual(s.on_hand(item,self.warehouse),(Decimal(8),Decimal(4000)));self.assert_balanced()
    def test_all_13_report_functions(self):
        _,_,device=self.intake();s.checkout(self.sale(device),self.user,[],None)
        for name in REPORTS:
            columns,rows=run(name,self.user,{})
            self.assertIsInstance(columns,list);self.assertIsInstance(rows,list)
    def test_authenticated_views_and_forms_render(self):
        self.client.force_login(self.user)
        _,_,device=self.intake()
        urls=[reverse('dashboard'),reverse('pos'),reverse('device',args=[device.pk]),reverse('settings')]
        urls += [reverse('new',args=[kind]) for kind in KINDS if kind!='Journal Entry']
        urls += [reverse('collection',args=[kind]) for kind in KINDS+['Mobile Device','Mobile Model','Item','Customer','Supplier','Mobile Staff']]
        urls += [reverse('report',args=[name]) for name in REPORTS]
        for url in urls:
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,200)
    def test_login_required_and_csrf_enforced(self):
        client=Client(enforce_csrf_checks=True)
        self.assertEqual(client.get(reverse('dashboard')).status_code,302)
        client.force_login(self.user)
        self.assertEqual(client.post(reverse('pos_checkout'),data='{}',content_type='application/json').status_code,403)
    def test_sale_prefill_and_browser_form_submission(self):
        _,_,device=self.intake();self.client.force_login(self.user)
        response=self.client.get(reverse('new',args=['Sales Invoice']),{'device':device.pk})
        self.assertEqual(str(response.context['lines'].forms[0].initial['device'].pk),str(device.pk))
        payload=dict(branch=str(self.branch.pk),posting_date=str(timezone.localdate()),customer=str(self.customer.pk),seller=str(self.seller.pk),warehouse=str(self.warehouse.pk),
            **{'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-MIN_NUM_FORMS':'0','lines-MAX_NUM_FORMS':'1000',
            'lines-0-product':str(self.product.pk),'lines-0-device':str(device.pk),'lines-0-warehouse':str(self.warehouse.pk),'lines-0-quantity':'1','lines-0-rate':'65000'})
        result=self.client.post(reverse('new',args=['Sales Invoice']),payload)
        self.assertEqual(result.status_code,302)
        doc=Document.objects.get(kind='Sales Invoice')
        response=self.client.post(reverse('action',args=[doc.pk,'checkout']),dict(request_key=str(uuid.uuid4()),handler=str(self.handler.pk),payment_mode=['Cash'],payment_amount=['65000'],payment_reference=['']))
        self.assertEqual(response.status_code,302);doc.refresh_from_db();self.assertEqual(doc.outstanding,0);self.assertEqual(doc.status,'Submitted')
    def test_cashier_can_complete_sale_with_separate_seller(self):
        _,_,device=self.intake();user=get_user_model().objects.create_user('cashier',password='Cashier-test-password-872!');self.handler.user=user;self.handler.save()
        doc=s.checkout(self.sale(device),user,[dict(mode='Cash',amount='65000')],self.handler)
        self.assertEqual(doc.outstanding,0);self.assertEqual(doc.staff_names['seller'],'Ali Hassan')
    def test_http_repair_expense_on_sold_phone_preserves_stock_and_shop_scope(self):
        _,_,device=self.intake();s.checkout(self.sale(device),self.user,[],None)
        service=Product.objects.create(code='REPAIR',name='Device repair',serialized=False,stock_item=False)
        before=list(StockMovement.objects.values_list('id','value'))
        self.client.force_login(self.user)
        payload=dict(branch=str(self.branch.pk),posting_date=str(timezone.localdate()),supplier=str(self.supplier.pk),buyer=str(self.buyer.pk),warehouse=str(self.warehouse.pk),
            **{'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-MIN_NUM_FORMS':'0','lines-MAX_NUM_FORMS':'1000',
            'lines-0-product':str(service.pk),'lines-0-device':str(device.pk),'lines-0-warehouse':str(self.warehouse.pk),'lines-0-quantity':'1','lines-0-rate':'2000'})
        response=self.client.post(reverse('new',args=['Purchase Invoice']),payload)
        self.assertEqual(response.status_code,302)
        bill=Document.objects.get(kind='Purchase Invoice');s.submit(bill,self.user)
        device.refresh_from_db();self.assertEqual(device.status,'Sold');self.assertEqual(bill.total,2000)
        self.assertEqual(list(StockMovement.objects.values_list('id','value')),before)
        self.assert_balanced()
        other=Branch.objects.create(code='OTHER-REPAIR',name='Other shop',company='Test')
        device.branch=other;device.save(update_fields=['branch'])
        second=s.new_doc('Purchase Invoice',self.branch,supplier=self.supplier,buyer=self.buyer)
        Line.objects.create(document=second,product=service,device=device,quantity=1,rate=2000)
        with self.assertRaisesMessage(ValidationError,'phone from this shop'):s.submit(second,self.user)
        self.assertFalse(second.ledger.exists())
    def test_pos_api_is_idempotent(self):
        _,_,device=self.intake();s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'0'}),self.user)
        self.client.force_login(self.user)
        data=dict(request_key=str(uuid.uuid4()),branch=str(self.branch.pk),customer=str(self.customer.pk),seller=str(self.seller.pk),handler=str(self.handler.pk),
            items=[dict(product=str(self.product.pk),device=str(device.pk),warehouse=str(self.warehouse.pk),quantity=1,rate='65000')],payments=[dict(mode='Cash',amount='65000')])
        first=self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json')
        self.assertEqual(first.status_code,200,first.content)
        second=self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json')
        self.assertEqual(first.json()['id'],second.json()['id']);self.assertEqual(Document.objects.filter(kind='POS Invoice',status='Submitted').count(),1)
    def test_receipt_is_frozen_and_demo_labelled(self):
        _,_,device=self.intake();doc=s.checkout(self.sale(device),self.user,[],None)
        self.client.force_login(self.user);response=self.client.get(reverse('receipt',args=[doc.pk]))
        self.assertNotContains(response,'DEMONSTRATION DATA');self.assertContains(response,'IMEI '+imei(1));self.assertContains(response,'Warranty: 7 days (until');self.assertNotContains(response,'Stock ref')

class Concurrency(TransactionTestCase):
    def test_two_database_sessions_cannot_sell_same_phone(self):
        values=fixtures();self.__dict__.update(values)
        device=Device.objects.create(code='CONCURRENT',product=self.product,warehouse=self.warehouse,purchase_cost=50000,asking_price=65000)
        IMEI.objects.create(number=imei(500),device=device,slot=1)
        StockMovement.objects.create(document=s.new_doc('Purchase Receipt',self.branch,status='Submitted'),posting_date=timezone.localdate(),product=self.product,warehouse=self.warehouse,quantity=1,value=50000)
        docs=[]
        for n in (1,2):
            doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
            Line.objects.create(document=doc,product=self.product,device=device,warehouse=self.warehouse,quantity=1,rate=65000);docs.append(doc.pk)
        def sell(pk):
            close_old_connections()
            try:
                s.checkout(Document.objects.get(pk=pk),get_user_model().objects.get(pk=self.user.pk),[],None);return 'sold'
            except ValidationError:return 'blocked'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(sell,docs))
        self.assertCountEqual(results,['sold','blocked'])
