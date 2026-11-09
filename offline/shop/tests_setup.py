from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError,PermissionDenied
from django.db import IntegrityError
from django.test import TestCase,override_settings
import uuid
from django.urls import reverse
from .models import *
from . import setup,services,reports
from .setup_forms import CreateShopForm
from .forms import DocumentForm
from django.utils import timezone

class ShopSetup(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('setup-owner',password='Testing-only-291')
        self.client.force_login(self.owner)
    def values(self,number=1):
        return dict(code=f'QA-{number}',name=f'QA shop {number}',company='QA mobile group',shop_phone='',shop_address='Synthetic QA location',warehouse_name='Stock room',till_name='Main till',payment_methods=['Cash','Bank Transfer','Card','JazzCash','Easypaisa'])
    def shop(self,number=1):return setup.create_shop(self.values(number),self.owner)
    def staff(self,branch,code='CASHIER',name='Ali Hassan',user=None,manager=False):
        return Staff.objects.create(code=branch.code+'-'+code,name=name,branch=branch,user=user,manager=manager,can_buy=manager,can_sell=True,can_handle_payments=True,default_warehouse=Warehouse.objects.get(branch=branch))
    def payment(self,branch,staff,amount):
        party=Party.objects.get_or_create(code='QA-CUSTOMER',defaults={'name':'QA customer','kind':'Customer'})[0]
        return services.submit(services.new_doc('Payment Entry',branch,customer=party,handler=staff,data=dict(amount=str(amount),direction='Receive',mode='Cash')),self.owner)
    def test_four_shops_create_complete_empty_configuration_through_http(self):
        for number in range(1,5):
            response=self.client.post(reverse('setup_new'),self.values(number));self.assertRedirects(response,reverse('users'))
        self.assertEqual(Branch.objects.count(),4);self.assertEqual(Warehouse.objects.count(),4);self.assertEqual(Till.objects.count(),4)
        self.assertFalse(Document.objects.exists());self.assertFalse(LedgerEntry.objects.exists());self.assertFalse(StockMovement.objects.exists())
        for branch in Branch.objects.all():
            configuration=ShopSettings.objects.get(branch=branch)
            self.assertEqual(set(configuration.accounts),set(setup.POSTING));self.assertEqual(set(configuration.payment_methods),set(setup.METHODS))
            setup.validate_mapping(configuration.accounts,configuration.payment_methods)
        response=self.client.get(reverse('setup'));self.assertContains(response,'4 shops configured')
    def test_shop_creation_rolls_back_everything_on_account_conflict(self):
        Account.objects.create(code='QA-1-SALES',name='Existing conflicting account')
        with self.assertRaises(IntegrityError):self.shop()
        self.assertFalse(Branch.objects.exists());self.assertFalse(Warehouse.objects.exists());self.assertFalse(Till.objects.exists());self.assertEqual(Account.objects.count(),1)
    def test_shop_form_rejects_duplicate_code_and_missing_cash(self):
        self.shop();form=CreateShopForm({**self.values(),'code':'qa-1'})
        self.assertFalse(form.is_valid());self.assertIn('code',form.errors)
        form=CreateShopForm({**self.values(2),'payment_methods':['JazzCash']});self.assertFalse(form.is_valid());self.assertIn('payment_methods',form.errors)
    def test_owner_only_setup_and_finance_even_for_shop_manager(self):
        branch=self.shop();user=get_user_model().objects.create_user('shop-manager');self.staff(branch,user=user,manager=True)
        self.client.force_login(user)
        for url in [reverse('setup'),reverse('setup_new'),reverse('setup_finance',args=[branch.pk]),reverse('collection',args=['Account']),reverse('master_new',args=['Shop'])]:
            with self.subTest(url=url):self.assertEqual(self.client.get(url).status_code,403)
        with self.assertRaises(PermissionDenied):setup.create_shop(self.values(2),user)
        self.assertEqual(Branch.objects.count(),1)
    def test_wrong_payment_account_type_rejected_without_configuration_change(self):
        branch=self.shop();config=ShopSettings.objects.get(branch=branch);before=config.payment_methods.copy()
        methods={**before,'JazzCash':before['Cash']}
        with self.assertRaisesMessage(ValidationError,'Bank accounts'):setup.configure(branch,config.accounts,methods,self.owner)
        config.refresh_from_db();self.assertEqual(config.payment_methods,before)
    def test_posting_accounts_cannot_be_changed_after_trading(self):
        branch=self.shop();self.payment(branch,self.staff(branch),1000);config=ShopSettings.objects.get(branch=branch)
        replacement=Account.objects.create(code='NEW-AR',name='New receivable',account_type='Receivable')
        old=config.accounts.copy()
        with self.assertRaisesMessage(ValidationError,'locked after trading'):setup.configure(branch,{**old,'receivable':str(replacement.pk)},config.payment_methods,self.owner)
        config.refresh_from_db();self.assertEqual(config.accounts,old);self.assertEqual(LedgerEntry.objects.count(),2)
    def test_payment_mapping_changes_wait_until_shift_is_closed(self):
        branch=self.shop();person=self.staff(branch)
        services.submit(services.new_doc('POS Opening Entry',branch,handler=person,till=Till.objects.get(branch=branch),data={'opening_float':'0'}),self.owner)
        config=ShopSettings.objects.get(branch=branch);methods={**config.payment_methods,'JazzCash':config.payment_methods['Bank Transfer']}
        with self.assertRaisesMessage(ValidationError,'Close this shop'):setup.configure(branch,config.accounts,methods,self.owner)
    def test_mapped_account_code_type_and_enabled_state_are_protected(self):
        branch=self.shop();config=ShopSettings.objects.get(branch=branch)
        for field,value in [('code','RENAMED'),('account_type','Bank'),('enabled',False)]:
            account=Account.objects.get(pk=config.payment_methods['Cash']);setattr(account,field,value)
            with self.subTest(field=field),self.assertRaises(ValidationError):account.full_clean()
    def test_staff_role_preset_and_default_warehouse_are_saved(self):
        branch=self.shop();warehouse=Warehouse.objects.get(branch=branch)
        response=self.client.post(reverse('master_new',args=['Mobile Staff']),dict(name='QA cashier',branch=str(branch.pk),default_warehouse=str(warehouse.pk),role='cashier',enabled='on',manager='on',can_buy='on'))
        self.assertEqual(response.status_code,302);person=Staff.objects.get()
        self.assertFalse(person.manager);self.assertFalse(person.can_buy);self.assertTrue(person.can_handle_payments);self.assertEqual(person.default_warehouse,warehouse)
    def test_foreign_default_warehouse_is_rejected(self):
        branch=self.shop();other=self.shop(2)
        response=self.client.post(reverse('master_new',args=['Mobile Staff']),dict(name='QA cashier',branch=str(branch.pk),default_warehouse=str(Warehouse.objects.get(branch=other).pk),role='cashier',enabled='on'))
        self.assertEqual(response.status_code,200);self.assertFalse(Staff.objects.exists())
    def test_staff_defaults_prefill_own_shop_and_warehouse(self):
        branch=self.shop();user=get_user_model().objects.create_user('buyer');person=self.staff(branch,user=user,manager=True)
        self.client.force_login(user);response=self.client.get(reverse('new',args=['Mobile Intake']))
        self.assertEqual(str(response.context['form']['warehouse'].value()),str(person.default_warehouse_id))
    def test_open_shift_prevents_staff_login_or_shop_reassignment(self):
        branch=self.shop();other=self.shop(2);person=self.staff(branch)
        services.submit(services.new_doc('POS Opening Entry',branch,handler=person,till=Till.objects.get(branch=branch),data={'opening_float':'0'}),self.owner)
        person.branch=other;person.default_warehouse=None
        with self.assertRaises(ValidationError):person.full_clean()
    def test_same_staff_name_in_four_shops_is_not_merged_in_owner_reports(self):
        branches=[self.shop(number) for number in range(1,5)]
        for number,branch in enumerate(branches,1):self.payment(branch,self.staff(branch),100*number)
        _,rows=reports.run('Staff Summary',self.owner,{})
        self.assertEqual(len(rows),4);self.assertEqual(len({row['Staff ID'] for row in rows}),4)
        self.assertEqual(sum(row['Money Collected'] for row in rows),Decimal('1000'))
        user=get_user_model().objects.create_user('local-manager');self.staff(branches[0],code='MANAGER',name='QA local manager',user=user,manager=True)
        _,own=reports.run('Staff Summary',user,{})
        self.assertEqual(len(own),1);self.assertEqual(own[0]['Shop'],branches[0].name)
        with self.assertRaises(PermissionDenied):services.submit(services.new_doc('Payment Entry',branches[1]),user)
    def test_readiness_requires_roles_and_a_staff_login(self):
        branch=self.shop();self.assertFalse(setup.readiness(branch)['ready'])
        user=get_user_model().objects.create_user('ready-manager');self.staff(branch,user=user,manager=True)
        self.assertTrue(setup.readiness(branch)['ready'])
    def test_finance_http_form_adds_method_and_rejects_duplicate_names(self):
        branch=self.shop();config=ShopSettings.objects.get(branch=branch)
        bank=Account.objects.create(code='QA-NEW-BANK',name='QA bank transfer',account_type='Bank')
        rows=list(config.payment_methods.items())+[('QA transfer',str(bank.pk))]
        payload={**config.accounts,'methods-TOTAL_FORMS':str(len(rows)),'methods-INITIAL_FORMS':str(len(rows)-1),'methods-MIN_NUM_FORMS':'0','methods-MAX_NUM_FORMS':'30'}
        for index,(label,account) in enumerate(rows):payload.update({f'methods-{index}-label':label,f'methods-{index}-account':account})
        response=self.client.post(reverse('setup_finance',args=[branch.pk]),payload)
        self.assertEqual(response.status_code,302);config.refresh_from_db();self.assertEqual(config.payment_methods['QA transfer'],str(bank.pk))
        before=config.payment_methods.copy();payload['methods-5-label']='Cash'
        response=self.client.post(reverse('setup_finance',args=[branch.pk]),payload)
        self.assertEqual(response.status_code,200);self.assertContains(response,'Payment method names must be unique')
        config.refresh_from_db();self.assertEqual(config.payment_methods,before);self.assertFalse(LedgerEntry.objects.exists())
    def test_warehouse_with_staff_defaults_cannot_move_between_shops(self):
        branch=self.shop();other=self.shop(2);self.staff(branch)
        warehouse=Warehouse.objects.get(branch=branch);warehouse.branch=other
        with self.assertRaises(ValidationError):warehouse.full_clean()
        warehouse.branch=branch;warehouse.enabled=False
        with self.assertRaises(ValidationError):warehouse.full_clean()
    def test_disabled_shop_can_be_reopened_by_owner(self):
        branch=self.shop();branch.enabled=False;branch.save()
        response=self.client.get(reverse('master_edit',args=['Shop',branch.pk]));self.assertEqual(response.status_code,200)
        response=self.client.post(reverse('master_edit',args=['Shop',branch.pk]),dict(code=branch.code,name=branch.name,company=branch.company,enabled='on'))
        self.assertEqual(response.status_code,302);branch.refresh_from_db();self.assertTrue(branch.enabled)
    def test_transaction_form_never_uses_another_shops_warehouse(self):
        branch=self.shop();other=self.shop(2);seller=self.staff(branch)
        customer=Party.objects.create(code='FORM-CUSTOMER',name='QA customer',kind='Customer')
        form=DocumentForm({'branch':str(branch.pk),'posting_date':str(timezone.localdate()),'customer':str(customer.pk),'seller':str(seller.pk),'warehouse':str(Warehouse.objects.get(branch=other).pk)},user=self.owner,kind='Sales Invoice')
        self.assertTrue(form.is_valid(),form.errors);self.assertEqual(form.cleaned_data['warehouse'].branch_id,branch.pk)      # a wrong-shop stock location is replaced by the shop's own, never accepted
    @override_settings(SUPABASE_AUTH_ENABLED=True)
    def test_supabase_readiness_requires_a_provider_linked_staff_login(self):
        branch=self.shop();user=get_user_model().objects.create_user('unlinked-staff');self.staff(branch,user=user,manager=True)
        self.assertFalse(setup.readiness(branch)['ready'])
        SupabaseIdentity.objects.create(auth_id=uuid.uuid4(),user=user)
        self.assertTrue(setup.readiness(branch)['ready'])
    def test_shop_without_a_login_is_not_ready(self):
        branch=self.shop();Staff.objects.create(code='NO-LOGIN',name='Unlinked staff',branch=branch)
        status=setup.readiness(branch)
        self.assertFalse(status['ready']);self.assertIn('Create the shop login (Shop accounts)',status['issues'])
    def test_same_warehouse_names_remain_separate_in_owner_stock_report(self):
        branches=[self.shop(1),self.shop(2)];product=Product.objects.create(code='QA-CHARGER',name='QA charger')
        supplier=Party.objects.create(code='QA-STOCK-SUPPLIER',name='QA stock supplier',kind='Supplier')
        for quantity,branch in enumerate(branches,1):
            warehouse=Warehouse.objects.get(branch=branch);buyer=self.staff(branch,manager=True)
            receipt=services.new_doc('Purchase Receipt',branch,supplier=supplier,buyer=buyer,warehouse=warehouse)
            Line.objects.create(document=receipt,product=product,warehouse=warehouse,quantity=quantity,rate=1000)
            services.submit(receipt,self.owner)
        _,rows=reports.run('Stock Balance',self.owner,{})
        self.assertEqual(len(rows),2);self.assertEqual(len({row['Warehouse code'] for row in rows}),2)
        self.assertEqual(sum(row['Quantity'] for row in rows),Decimal('3'));self.assertEqual(sum(row['Stock value'] for row in rows),Decimal('3000'))
