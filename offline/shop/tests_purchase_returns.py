"""Phase C: purchase returns to suppliers and cash short/over at daily closing."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fund,fixtures,imei
from . import coa,vouchers,easy_retail,purchase_returns as pr,services as s


class ReturnBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());coa.install_standard(self.user);self.today=timezone.localdate();self.client.force_login(self.user)
    def bill(self,numbers,cost=50000,paid='unpaid',amount=None):
        data=dict(branch=self.branch,party_name='Return Supplier',product=self.product,quantity=len(numbers),price=cost,asking_price=90000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,
                  posting_date=self.today,payment_status=paid,method='Cash',imeis=[[imei(n)] for n in numbers])
        if amount:data['amount_paid']=amount
        if paid!='unpaid':fund(self.branch,self.user,cost*len(numbers) if paid=='full' else amount)      # money cannot leave an empty till (D8): the owner puts in what this purchase pays
        return easy_retail.purchase(data,self.user)
    def payable(self,supplier):
        control=ShopSettings.objects.get(branch=self.branch).accounts['payable']
        rows=LedgerEntry.objects.filter(branch=self.branch,account_id=control,party=supplier).aggregate(d=Sum('debit'),c=Sum('credit'))
        return (rows['c'] or 0)-(rows['d'] or 0)


class PhoneReturns(ReturnBase):
    def test_returning_a_phone_reduces_what_we_owe_and_removes_the_stock(self):
        bill=self.bill([101,102]);device=bill.reference.lines.first().device
        doc=pr.create(bill,self.user,[device.pk],{},'Faulty screen')
        bill.refresh_from_db();device.refresh_from_db()
        self.assertEqual(bill.outstanding,Decimal('50000.00'));self.assertEqual(self.payable(bill.supplier),Decimal('50000.00'))
        self.assertEqual((device.status,device.warehouse_id,device.imeis.count()),(pr.RETURNED,None,0))
        self.assertEqual(s.on_hand(self.product,self.warehouse),(Decimal(1),Decimal('50000.00')))
        t=doc.ledger.aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual((t['d'],t['c']),(Decimal('50000.00'),Decimal('50000.00')))
    def test_returning_a_paid_phone_leaves_a_supplier_credit(self):
        bill=self.bill([103],paid='full');device=bill.reference.lines.get().device
        pr.create(bill,self.user,[device.pk],{},'Wrong model');bill.refresh_from_db()
        self.assertEqual(bill.outstanding,Decimal('-50000.00'));self.assertEqual(self.payable(bill.supplier),Decimal('-50000.00'))
    def test_a_sold_phone_cannot_be_returned_and_the_imei_can_be_re_bought(self):
        bill=self.bill([104,105]);first,second=[l.device for l in bill.reference.lines.order_by('pk')]
        easy_retail.sale(dict(branch=self.branch,party_name='Buyer',phone=first,price=70000,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=self.today,payment_status='unpaid',method='Cash'),self.user)
        with self.assertRaisesMessage(ValidationError,'no longer available'):pr.create(bill,self.user,[first.pk],{},'Too late')
        pr.create(bill,self.user,[second.pk],{},'Faulty');self.assertEqual(easy_retail.parse_imeis(imei(105),1),[[imei(105)]])      # IMEI is free again
    def test_reason_is_required_and_a_return_cannot_repeat(self):
        bill=self.bill([106]);device=bill.reference.lines.get().device
        with self.assertRaisesMessage(ValidationError,'Explain why'):pr.create(bill,self.user,[device.pk],{},' ')
        pr.create(bill,self.user,[device.pk],{},'Defective')
        with self.assertRaisesMessage(ValidationError,'no longer available'):pr.create(bill,self.user,[device.pk],{},'Again')
    def test_screen_lists_returnable_phones_and_posts(self):
        bill=self.bill([108]);device=bill.reference.lines.get().device
        page=self.client.get(reverse('purchase_return',args=[bill.pk]));self.assertContains(page,device.code)
        self.assertContains(self.client.get(reverse('detail',args=[bill.pk])),'Return to supplier')
        self.client.post(reverse('purchase_return',args=[bill.pk]),dict(devices=[str(device.pk)],reason='Faulty'))
        device.refresh_from_db();self.assertEqual(device.status,pr.RETURNED)

    def test_supplier_can_refund_the_money_at_once_and_cannot_refund_more_than_the_credit(self):
        from . import money
        bill=self.bill([109],paid='full');device=bill.reference.lines.get().device
        before=sum((b['amount'] for b in money.balances([self.branch]) if b['kind']=='Cash'),Decimal(0))
        bad=self.client.post(reverse('purchase_return',args=[bill.pk]),dict(devices=[str(device.pk)],reason='Faulty',resolution='refund',method='Cash',refund_amount='99999999'))
        device.refresh_from_db();self.assertEqual(device.status,'Available')          # nothing saved when the refund is refused
        self.client.post(reverse('purchase_return',args=[bill.pk]),dict(devices=[str(device.pk)],reason='Faulty',resolution='refund',method='Cash',refund_amount=''))
        bill.refresh_from_db();after=sum((b['amount'] for b in money.balances([self.branch]) if b['kind']=='Cash'),Decimal(0))
        self.assertEqual((bill.outstanding,after-before),(Decimal('0.00'),Decimal('50000.00')))
        self.assertEqual(self.payable(bill.supplier),Decimal('0.00'))


class AccessoryReturns(ReturnBase):
    def test_accessory_return_by_quantity_is_limited_to_what_was_received_and_is_in_stock(self):
        case=Product.objects.create(code='CASE',name='Phone case',stock_item=True,serialized=False)
        doc=s.new_doc('Purchase Receipt',self.branch,supplier=Party.objects.create(code='ACC-S',kind='Supplier',name='Case Supplier'),warehouse=self.warehouse,buyer=self.buyer)
        Line.objects.create(document=doc,product=case,warehouse=self.warehouse,quantity=10,rate=Decimal('100'));receipt=s.submit(doc,self.user);bill=s.supplier_bill(receipt,self.user);bill=s.submit(bill,self.user)
        line=receipt.lines.get()
        with self.assertRaisesMessage(ValidationError,'Only 10'):pr.create(bill,self.user,[],{str(line.pk):'12'},'Too many')
        done=pr.create(bill,self.user,[],{str(line.pk):'4'},'Wrong colour');self.assertEqual(done.total,Decimal('400.00'));self.assertEqual(s.on_hand(case,self.warehouse),(Decimal(6),Decimal('600.00')))
        with self.assertRaisesMessage(ValidationError,'Only 6'):pr.create(bill,self.user,[],{str(line.pk):'7'},'More')


class CashShortOver(ReturnBase):
    def setUp(self):
        super().setUp();self.capital=Account.objects.get(code='COA-3100');self.cash_account=Account.objects.get(pk=ShopSettings.objects.get(branch=self.branch).payment_methods['Cash'])
        vouchers.post(dict(branch=self.branch,type='capital',posting_date=self.today,amount=Decimal('10000'),account=str(self.capital.pk),mode='Cash',description='Opening cash'),self.user)
    def close(self,counted,note=''):
        expected=s.balances(self.branch,self.today)
        counts={row['account']:{'counted':str(counted) if row['account']==str(self.cash_account.pk) else row['expected'],'notes':note} for row in expected}
        doc=s.new_doc('Mobile Daily Closing',self.branch,handler=self.handler,posting_date=self.today,data={'counts':counts});return s.submit(doc,self.user)
    def test_cash_short_is_booked_as_an_expense_and_the_ledger_matches_the_count(self):
        self.close(9700,'Counted PKR 300 short');self.assertEqual(s.account_balance(self.branch,self.cash_account),Decimal('9700'))
        self.assertEqual(s.account_balance(self.branch,Account.objects.get(code='COA-6950')),Decimal('300'))
    def test_cash_over_is_booked_as_a_gain(self):
        self.close(10150,'Found extra');self.assertEqual(s.account_balance(self.branch,self.cash_account),Decimal('10150'))
        self.assertEqual(s.account_balance(self.branch,Account.objects.get(code='COA-6950')),Decimal('-150'))
    def test_no_difference_posts_nothing(self):
        doc=self.close(10000);self.assertFalse(doc.ledger.exists())
    def test_a_difference_still_needs_an_explanation(self):
        with self.assertRaisesMessage(ValidationError,'Explain each closing difference'):self.close(9000,'')
