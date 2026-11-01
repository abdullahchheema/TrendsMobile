import json,uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import close_old_connections,connections
from django.test import TestCase,TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from .models import Party,Product,Document,Audit,RequestKey
from .tests import fixtures
from .forms import DocumentForm
from . import services as s,customer_names


class TypedCustomers(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
        self.item=Product.objects.create(code='TYPED-SERVICE',name='Setup service',stock_item=False,serialized=False,selling_price=100)
    def shift(self):
        return s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'0'}),self.user)
    def payload(self,name='New Pakistani Customer'):
        return dict(request_key=str(uuid.uuid4()),branch=str(self.branch.pk),customer_name=name,seller=str(self.seller.pk),handler=str(self.handler.pk),
            items=[dict(product=str(self.item.pk),device='',warehouse=str(self.warehouse.pk),quantity=1,rate='100')],payments=[])
    def checkout(self,data):return self.client.post(reverse('pos_checkout'),json.dumps(data),content_type='application/json')
    def draft_data(self,name='Draft Customer'):
        return dict(branch=str(self.branch.pk),posting_date=str(timezone.localdate()),customer_name=name,seller=str(self.seller.pk),warehouse=str(self.warehouse.pk),
            **{'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-0-product':str(self.item.pk),'lines-0-quantity':'1','lines-0-rate':'100','lines-0-warehouse':str(self.warehouse.pk)})
    def test_pos_is_a_required_name_input_with_optional_suggestions(self):
        response=self.client.get(reverse('pos'));self.assertContains(response,'name="customer_name"');self.assertContains(response,'list="pos-customer-suggestions"')
        self.assertNotContains(response,'<select name="customer"');self.assertContains(response,'Type a new name')
    def test_pos_creates_customer_and_posts_sale_once_on_retry(self):
        self.shift();data=self.payload();one=self.checkout(data);two=self.checkout(data)
        self.assertEqual(one.status_code,200,one.content);self.assertEqual(one.json()['id'],two.json()['id'])
        customer=Party.objects.get(kind='Customer',name=data['customer_name']);sale=Document.objects.get(pk=one.json()['id'])
        self.assertEqual(sale.customer,customer);self.assertEqual(sale.status,'Submitted');self.assertEqual(sale.outstanding,100)
        self.assertEqual(Audit.objects.filter(action='Create customer').count(),1)
        self.assertContains(self.client.get(reverse('receipt',args=[sale.pk])),data['customer_name'])
    def test_existing_name_reuses_customer_without_changing_spelling_or_balance_identity(self):
        self.shift();data=self.payload('  demo   CUSTOMER ');response=self.checkout(data)
        self.assertEqual(response.status_code,200,response.content);sale=Document.objects.get(pk=response.json()['id'])
        self.assertEqual(sale.customer,self.customer);self.customer.refresh_from_db();self.assertEqual(self.customer.name,'Demo Customer')
        self.assertFalse(Audit.objects.filter(action='Create customer').exists())
    def test_failed_checkout_rolls_back_customer_audit_invoice_and_request_key(self):
        self.shift();data=self.payload();data['items'][0]['quantity']='-1';before=Document.objects.count()
        response=self.checkout(data);self.assertEqual(response.status_code,400)
        self.assertFalse(Party.objects.filter(name=data['customer_name']).exists());self.assertEqual(Document.objects.count(),before)
        self.assertFalse(Audit.objects.filter(action='Create customer').exists());self.assertFalse(RequestKey.objects.filter(key=data['request_key']).exists())
    def test_missing_shift_does_not_create_customer(self):
        data=self.payload();self.assertEqual(self.checkout(data).status_code,400);self.assertFalse(Party.objects.filter(name=data['customer_name']).exists())
    def test_invalid_names_are_rejected_without_customer_creation(self):
        self.shift();before=Party.objects.count()
        for name in ('','   ','A'*181,[],None,'Bad\x00Name'):
            with self.subTest(name_type=type(name).__name__):self.assertEqual(self.checkout(self.payload(name)).status_code,400)
        self.assertEqual(Party.objects.count(),before)
    def test_inactive_customer_is_not_replaced_or_reactivated(self):
        self.customer.enabled=False;self.customer.save();self.shift();response=self.checkout(self.payload(self.customer.name))
        self.assertEqual(response.status_code,400);self.assertIn('inactive',response.json()['error']);self.assertEqual(Party.objects.filter(kind='Customer').count(),1)
    def test_case_variant_accounts_are_not_silently_merged(self):
        Party.objects.create(code='AMBIGUOUS',name='DEMO CUSTOMER',kind='Customer');self.shift()
        response=self.checkout(self.payload('demo customer'));self.assertEqual(response.status_code,400);self.assertIn('More than one',response.json()['error'])
    def test_supplier_with_same_name_is_not_used_as_customer(self):
        customer=customer_names.resolve(self.supplier.name,self.user);self.assertEqual(customer.kind,'Customer');self.assertNotEqual(customer.pk,self.supplier.pk)
    def test_urdu_name_and_whitespace_are_supported(self):
        one=customer_names.resolve('  محمد   علی  ',self.user);two=customer_names.resolve('محمد علی',self.user)
        self.assertEqual(one.pk,two.pk);self.assertEqual(one.name,'محمد علی')
    def test_legacy_pos_customer_id_still_works(self):
        self.shift();data=self.payload();data.pop('customer_name');data['customer']=str(self.customer.pk)
        response=self.checkout(data);self.assertEqual(response.status_code,200,response.content)
        self.assertEqual(Document.objects.get(pk=response.json()['id']).customer,self.customer)
    def test_changed_retry_name_cannot_reuse_original_request_key(self):
        self.shift();data=self.payload();self.assertEqual(self.checkout(data).status_code,200);data['customer_name']='Another Customer'
        self.assertEqual(self.checkout(data).status_code,400);self.assertFalse(Party.objects.filter(name='Another Customer').exists())
    def test_sales_draft_creates_customer_by_name_without_uuid(self):
        response=self.client.post(reverse('new',args=['Sales Invoice']),self.draft_data());self.assertEqual(response.status_code,302)
        sale=Document.objects.get(kind='Sales Invoice');self.assertEqual(sale.customer.name,'Draft Customer');self.assertEqual(sale.status,'Draft')
        self.assertNotIn('customer_name',sale.data)
    def test_invalid_draft_has_no_orphan_customer(self):
        data=self.draft_data();data['lines-0-rate']='invalid';response=self.client.post(reverse('new',args=['Sales Invoice']),data)
        self.assertEqual(response.status_code,200);self.assertFalse(Party.objects.filter(name='Draft Customer').exists())
    def test_draft_with_no_items_rolls_back_typed_customer(self):
        data=self.draft_data()
        for key in list(data):
            if key.startswith('lines-0-'):data.pop(key)
        response=self.client.post(reverse('new',args=['Sales Invoice']),data);self.assertEqual(response.status_code,200)
        self.assertFalse(Party.objects.filter(name='Draft Customer').exists());self.assertFalse(Audit.objects.filter(action='Create customer').exists())
    def test_edit_draft_can_replace_name_and_clearing_name_does_not_keep_stale_hidden_id(self):
        self.client.post(reverse('new',args=['Sales Invoice']),self.draft_data());sale=Document.objects.get(kind='Sales Invoice')
        data=self.draft_data('Another Customer');data['customer']=str(sale.customer_id);data['lines-TOTAL_FORMS']='1';data['lines-INITIAL_FORMS']='1';data['lines-0-id']=str(sale.lines.get().pk)
        response=self.client.post(reverse('edit',args=['Sales Invoice',sale.pk]),data);self.assertEqual(response.status_code,302)
        sale.refresh_from_db();self.assertEqual(sale.customer.name,'Another Customer')
        data['customer_name']='';data['customer']=str(sale.customer_id)
        response=self.client.post(reverse('edit',args=['Sales Invoice',sale.pk]),data);self.assertEqual(response.status_code,200)
        self.assertContains(response,'Enter a customer name');sale.refresh_from_db();self.assertEqual(sale.customer.name,'Another Customer')
    def test_get_and_form_validation_do_not_create_customer(self):
        response=self.client.get(reverse('new',args=['Sales Invoice']));self.assertContains(response,'name="customer_name"')
        self.assertEqual(response.context['form'].fields['customer'].widget.input_type,'hidden')
        form=DocumentForm(self.draft_data(),instance=Document(kind='Sales Invoice'),user=self.user,kind='Sales Invoice')
        self.assertTrue(form.is_valid(),form.errors);self.assertFalse(Party.objects.filter(name='Draft Customer').exists())
    def test_customer_name_is_available_on_payment_warranty_exchange_and_tradein_forms(self):
        for kind in ('Payment Entry','Warranty Claim','Mobile Exchange'):
            response=self.client.get(reverse('new',args=[kind]));self.assertContains(response,'name="customer_name"')
        self.assertContains(self.client.get(reverse('new',args=['Mobile Intake'])),'name="trade_in_customer_name"')
    def test_payment_can_create_customer_but_cannot_combine_supplier_and_customer(self):
        data=dict(branch=str(self.branch.pk),posting_date=str(timezone.localdate()),customer_name='Payment Customer',handler=str(self.handler.pk),direction='Receive',amount='100',mode='Cash')
        response=self.client.post(reverse('new',args=['Payment Entry']),data);self.assertEqual(response.status_code,302)
        self.assertEqual(Document.objects.get(kind='Payment Entry').customer.name,'Payment Customer')
        data.update(customer_name='Invalid Mixed Customer',supplier=str(self.supplier.pk))
        response=self.client.post(reverse('new',args=['Payment Entry']),data);self.assertEqual(response.status_code,200)
        self.assertFalse(Party.objects.filter(name='Invalid Mixed Customer').exists())
    def test_tradein_name_is_resolved_to_customer_reference(self):
        data=dict(branch=str(self.branch.pk),posting_date=str(timezone.localdate()),supplier=str(self.supplier.pk),buyer=str(self.buyer.pk),warehouse=str(self.warehouse.pk),
            purchase_source='Customer Trade-In',trade_in_customer_name='Trade-in Customer',device_type='Used',condition='Good',inspection_status='Pending',imei_1='990000190000010')
        form=DocumentForm(data,instance=Document(kind='Mobile Intake'),user=self.user,kind='Mobile Intake');self.assertTrue(form.is_valid(),form.errors)
        draft=form.save(commit=False);form.apply_customers(draft,self.user);form.apply_data(draft)
        customer=Party.objects.get(name='Trade-in Customer',kind='Customer');self.assertEqual(draft.data['trade_in_customer'],str(customer.pk))
    def test_returns_keep_original_customer_locked(self):
        original=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller)
        from .models import Line
        Line.objects.create(document=original,product=self.item,warehouse=self.warehouse,quantity=1,rate=100);original=s.submit(original,self.user)
        returned=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,reference=original,is_return=True,warehouse=self.warehouse)
        response=self.client.get(reverse('edit',args=[returned.kind,returned.pk]));form=response.context['form']
        self.assertNotIn('customer_name',form.fields);self.assertTrue(form.fields['customer'].disabled)
    def test_unassigned_or_inactive_login_cannot_create_customer(self):
        user=get_user_model().objects.create_user('unassigned-typed')
        with self.assertRaises(PermissionDenied):customer_names.resolve('Forbidden Customer',user)
        self.assertFalse(Party.objects.filter(name='Forbidden Customer').exists())


class TypedCustomerConcurrency(TransactionTestCase):
    def test_two_counters_typing_same_name_create_one_customer(self):
        values=fixtures();user_id=values['user'].pk;barrier=Barrier(2)
        def create(name):
            close_old_connections()
            try:
                user=get_user_model().objects.get(pk=user_id);barrier.wait(timeout=10)
                return customer_names.resolve(name,user).pk
            finally:connections.close_all()
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(create,'  Adeel   Khan ');two=pool.submit(create,'adeel khan')
            self.assertEqual(one.result(timeout=20),two.result(timeout=20))
        self.assertEqual(Party.objects.filter(kind='Customer',name__iexact='Adeel Khan').count(),1)
