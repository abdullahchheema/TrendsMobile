"""Inter-shop stock ownership, accounting, access and concurrency safeguards."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import uuid
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError, PermissionDenied
from django.db import close_old_connections, connections
from django.db.models import Sum
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from . import services as s, setup
from .forms import DocumentForm
from .setup_forms import PostingAccountsForm, AccountSetupForm
from .tests import fixtures, Workflows, imei


def transfer_setup(test):
    test.__dict__.update(fixtures())
    test.other=setup.create_shop(dict(code='SECOND',name='Second shop',company=test.branch.company,warehouse_name='Stock room',till_name='Till',payment_methods=['Cash']),test.user)
    test.destination=Warehouse.objects.get(branch=test.other)
    mappings=ShopSettings.objects.get(branch=test.branch).accounts
    for key,(label,kind) in setup.POSTING.items():
        account=Account.objects.get(pk=mappings[key]);account.account_type=kind;account.save()
    for branch in (test.branch,test.other):
        settings=ShopSettings.objects.get(branch=branch)
        account=Account.objects.create(code=branch.code+'-CLEARING',name=branch.name+' clearing',account_type='Current Asset')
        settings.accounts['inter_shop']=str(account.pk);settings.save()


class Transfers(TestCase):
    def setUp(self):transfer_setup(self)
    def phone(self,n=1):return Workflows.intake(self,n)[2]
    def transfer(self,device=None,product=None,quantity=1,source=None,target=None):
        source=source or self.warehouse;target=target or self.destination
        doc=s.new_doc('Stock Entry',source.branch,warehouse=source,notes='Physical stock handed to the second shop',data={'target_warehouse':str(target.pk)})
        Line.objects.create(document=doc,product=product or device.product,device=device,quantity=quantity,rate=0)
        return doc
    def manager(self,branch,name='manager-two'):
        user=get_user_model().objects.create_user(name,password='Transfer-test-password-77!')
        Staff.objects.create(code=name,name=name,branch=branch,user=user,manager=True)
        return user
    def assert_unmoved(self,device,doc):
        device.refresh_from_db();doc.refresh_from_db()
        self.assertEqual(device.branch,self.branch);self.assertEqual(device.warehouse,self.warehouse)
        self.assertEqual(doc.status,'Draft');self.assertFalse(doc.stock.exists());self.assertFalse(doc.ledger.exists())
    def test_phone_moves_with_imei_cost_and_balanced_shop_ledgers(self):
        phone=self.phone();imeis=list(phone.imeis.values_list('number',flat=True))
        total=StockMovement.objects.aggregate(value=Sum('value'))['value'];doc=s.submit(self.transfer(phone),self.user)
        phone.refresh_from_db()
        self.assertEqual((phone.branch,phone.warehouse,phone.purchase_cost),(self.other,self.destination,50000))
        self.assertEqual(list(phone.imeis.values_list('number',flat=True)),imeis)
        self.assertEqual(StockMovement.objects.aggregate(value=Sum('value'))['value'],total)
        self.assertEqual(s.on_hand(phone.product,self.warehouse),(0,0));self.assertEqual(s.on_hand(phone.product,self.destination),(1,50000))
        for branch in (self.branch,self.other):
            totals=doc.ledger.filter(branch=branch).aggregate(d=Sum('debit'),c=Sum('credit'))
            self.assertEqual(totals,dict(d=Decimal(50000),c=Decimal(50000)))
        self.assertEqual(doc.total,50000);self.assertEqual(doc.staff_names['transferred_by'],self.user.username)
        self.assertEqual(doc.lines.get().snapshot['pta_status'],'Not recorded');self.assertNotIn('imei_1',doc.lines.get().snapshot);self.assertEqual(doc.state,'Completed')
        from .reports import run
        columns,rows=run('General Ledger',self.user,{'branch':str(self.other.pk)})
        self.assertIn('Shop',columns)
        received=[row for row in rows if row['Document']==doc.code]
        self.assertEqual(len(received),2);self.assertTrue(all('SECOND' in row['Shop'] for row in received))
    def test_accessory_partial_then_last_transfer_preserves_rounding_residual(self):
        item=Product.objects.create(code='CASE',name='Case')
        seed=s.new_doc('Purchase Receipt',self.branch,status='Submitted')
        StockMovement.objects.create(document=seed,product=item,warehouse=self.warehouse,posting_date=timezone.localdate(),quantity=3,value=100)
        first=s.submit(self.transfer(product=item),self.user)
        last=s.submit(self.transfer(product=item,quantity=2),self.user)
        self.assertEqual(first.total,Decimal('33.33'));self.assertEqual(last.total,Decimal('66.67'))
        self.assertEqual(s.on_hand(item,self.warehouse),(0,0));self.assertEqual(s.on_hand(item,self.destination),(3,100))
    def test_a_shop_account_can_send_its_own_stock_to_another_shop_but_not_other_shops_stock(self):
        phone=self.phone();doc=self.transfer(phone)
        done=s.submit(doc,self.manager(self.branch));phone.refresh_from_db();self.assertEqual((phone.branch_id,phone.warehouse_id,done.status),(self.destination.branch_id,self.destination.pk,'Submitted'))
        stranger=Branch.objects.create(code='STRANGER',name='Stranger shop',company=self.branch.company)
        with self.assertRaises(PermissionDenied):s.submit(self.transfer(self.phone(2)),self.manager(stranger,'stranger-manager'))
    def test_same_shop_manager_transfer_still_has_no_ledger(self):
        phone=self.phone();target=Warehouse.objects.create(code='COUNTER',name='Counter',branch=self.branch)
        doc=s.submit(self.transfer(phone,target=target),self.manager(self.branch))
        self.assertFalse(doc.ledger.exists());phone.refresh_from_db();self.assertEqual(phone.warehouse,target)
    def test_different_business_rejected(self):
        phone=self.phone();doc=self.transfer(phone);self.other.company='Unrelated business';self.other.save()
        with self.assertRaisesMessage(ValidationError,'same business'):s.submit(doc,self.user)
        self.assert_unmoved(phone,doc)
    def test_closed_destination_date_rejected(self):
        phone=self.phone();doc=self.transfer(phone)
        s.new_doc('Mobile Daily Closing',self.other,status='Submitted')
        with self.assertRaisesMessage(ValidationError,'closed'):s.submit(doc,self.user)
        self.assert_unmoved(phone,doc)
    def test_missing_clearing_account_is_created_automatically_so_shops_need_no_setup(self):
        phone=self.phone();doc=self.transfer(phone);settings=ShopSettings.objects.get(branch=self.other)
        settings.accounts.pop('inter_shop');settings.save()
        done=s.submit(doc,self.user);phone.refresh_from_db();self.assertEqual(done.status,'Submitted');self.assertEqual(phone.branch_id,self.other.pk)
        settings.refresh_from_db();self.assertTrue(settings.accounts.get('inter_shop'))
    def test_wrong_line_warehouse_rejected(self):
        phone=self.phone();doc=self.transfer(phone);line=doc.lines.get();line.warehouse=self.destination;line.save()
        with self.assertRaisesMessage(ValidationError,'source warehouse'):s.submit(doc,self.user)
        self.assert_unmoved(phone,doc)
    def test_duplicate_phone_rolls_back_all_lines(self):
        phone=self.phone();doc=self.transfer(phone)
        Line.objects.create(document=doc,product=phone.product,device=phone,quantity=1,rate=0)
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assert_unmoved(phone,doc)
    def test_nonstock_items_cannot_transfer(self):
        item=Product.objects.create(code='REPAIR',name='Repair',stock_item=False)
        with self.assertRaisesMessage(ValidationError,'stock items'):s.submit(self.transfer(product=item),self.user)
    def test_disabled_destination_rejected(self):
        phone=self.phone();doc=self.transfer(phone);self.destination.enabled=False;self.destination.save()
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        self.assert_unmoved(phone,doc)
    def test_no_silent_repeat_and_cancel_requires_reverse_transfer(self):
        doc=s.submit(self.transfer(self.phone()),self.user)
        with self.assertRaises(ValidationError):s.submit(doc,self.user)
        with self.assertRaisesMessage(ValidationError,'reverse stock transfer'):s.cancel(doc,self.user)
        self.assertEqual(doc.stock.count(),2)
    def test_owner_form_labels_destination_shop_and_manager_cannot_choose_it(self):
        form=DocumentForm(user=self.user,kind='Stock Entry')
        self.assertIn(self.destination,list(form.fields['target_warehouse'].queryset))
        self.assertIn('SECOND',form.fields['target_warehouse'].label_from_instance(self.destination))
        form=DocumentForm(user=self.manager(self.branch),kind='Stock Entry')
        self.assertNotIn(self.destination,list(form.fields['target_warehouse'].queryset))
    def test_clearing_cannot_be_remapped_after_posting(self):
        s.submit(self.transfer(self.phone()),self.user)
        settings=ShopSettings.objects.get(branch=self.other)
        accounts=settings.accounts.copy();accounts.pop('inter_shop')
        with self.assertRaisesMessage(ValidationError,'locked'):setup.configure(self.other,accounts,settings.payment_methods,self.user)
    def test_optional_clearing_can_be_added_after_trading_without_changing_existing_accounts(self):
        self.phone();settings=ShopSettings.objects.get(branch=self.branch)
        mapping={k:settings.accounts[k] for k in setup.POSTING};clearing=settings.accounts.pop('inter_shop');settings.save()
        mapping['inter_shop']=clearing
        setup.configure(self.branch,mapping,settings.payment_methods,self.user)
        settings.refresh_from_db();self.assertEqual(settings.accounts['inter_shop'],clearing)
        self.assertIn('inter_shop',PostingAccountsForm(settings=settings).fields)
        self.assertIn(('Current Asset','Current Asset'),AccountSetupForm().fields['account_type'].choices)


class TransferConcurrency(TransactionTestCase):
    def test_opposite_direction_transfers_complete_without_deadlock(self):
        transfer_setup(self)
        first=Workflows.intake(self,810)[2];second=Workflows.intake(self,811)[2]
        seed=s.new_doc('Stock Entry',self.branch,warehouse=self.warehouse,notes='Initial physical placement',data={'target_warehouse':str(self.destination.pk)})
        Line.objects.create(document=seed,product=second.product,device=second,quantity=1,rate=0);s.submit(seed,self.user)
        records=[]
        for phone,source,target in [(first,self.warehouse,self.destination),(second,self.destination,self.warehouse)]:
            doc=s.new_doc('Stock Entry',source.branch,warehouse=source,notes='Concurrent opposite transfer',data={'target_warehouse':str(target.pk)})
            Line.objects.create(document=doc,product=phone.product,device=phone,quantity=1,rate=0);records.append(doc.pk)
        from threading import Barrier
        ready=Barrier(2)
        def post(pk):
            close_old_connections()
            try:
                ready.wait(timeout=10)
                return s.submit(Document.objects.get(pk=pk),get_user_model().objects.get(pk=self.user.pk)).status
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(post,records))
        self.assertEqual(results,['Submitted','Submitted'])
        first.refresh_from_db();second.refresh_from_db()
        self.assertEqual(first.branch,self.other);self.assertEqual(second.branch,self.branch)
    def test_sale_and_transfer_cannot_both_take_the_same_phone(self):
        transfer_setup(self);phone=Workflows.intake(self,800)[2]
        transfer=s.new_doc('Stock Entry',self.branch,warehouse=self.warehouse,notes='Concurrent transfer',data={'target_warehouse':str(self.destination.pk)})
        sale=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        for doc,rate in [(transfer,0),(sale,65000)]:Line.objects.create(document=doc,product=phone.product,device=phone,quantity=1,rate=rate)
        def post(pk):
            close_old_connections()
            try:s.submit(Document.objects.get(pk=pk),get_user_model().objects.get(pk=self.user.pk));return 'posted'
            except ValidationError:return 'blocked'
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(post,[transfer.pk,sale.pk]))
        self.assertCountEqual(results,['posted','blocked'])
