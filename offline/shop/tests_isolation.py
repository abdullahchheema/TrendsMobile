"""Two kinds of user (owner, shop login) and strict per-shop privacy for customers, suppliers, accounts and history."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase,TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,Workflows
from .tests_transfers import transfer_setup
from . import access,customer_names,easy_retail,imei as imei_module,shop_accounts,shopdefaults,transfers,services as s


class Base(TestCase):
    def setUp(self):
        transfer_setup(self);self.today=timezone.localdate()
        self.a=shop_accounts.create_login(self.branch,'iso-a','Strong-pass-for-A-77!')
        self.b=shop_accounts.create_login(self.other,'iso-b','Strong-pass-for-B-77!')
        self.cust_a=Party.objects.create(code='CA',kind='Customer',name='Alice Customer',phone='0300 1111111',branch=self.branch,credit_limit=Decimal('5000'))
        self.cust_b=Party.objects.create(code='CB',kind='Customer',name='Bilal Customer',phone='0300 2222222',branch=self.other)
        self.sup_b=Party.objects.create(code='SB',kind='Supplier',name='Bravo Supplier',phone='0300 3333333',branch=self.other)
    def login(self,user):self.client.force_login(user)


class TwoKindsOfUser(Base):
    def test_every_login_is_a_full_shop_person_and_nobody_is_limited(self):
        for user in (self.a,self.b):
            person=Staff.objects.get(user=user);self.assertTrue(person.manager and person.can_buy and person.can_sell and person.can_handle_payments)
    def test_owner_only_pages_are_closed_to_a_shop(self):
        self.login(self.a)
        for name in ('chart','periods','year_end','assets','deferrals','loans','journals','setup','setup_new','users','tax_rules','audit_log'):
            self.assertEqual(self.client.get(reverse(name)).status_code,403,name)
        for kind in ('Account','Mobile Staff','Warehouse','Till','Shop'):self.assertEqual(self.client.get(reverse('collection',args=[kind])).status_code,403,kind)
        for kind in ('Item','Mobile Model','Account'):self.assertEqual(self.client.get(reverse('master_new',args=[kind])).status_code,403,kind)
    def test_roles_screens_are_gone(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):reverse('roles')
    def test_owner_reaches_everything(self):
        self.login(self.user)
        for name in ('chart','periods','year_end','assets','deferrals','loans','journals','setup','users','tax_rules'):self.assertEqual(self.client.get(reverse(name)).status_code,200,name)


class CustomersAreSeparatePerShop(Base):
    def test_a_shop_sees_only_its_own_customers_and_suppliers(self):
        self.login(self.a)
        page=self.client.get(reverse('collection',args=['Customer']));self.assertContains(page,'Alice Customer');self.assertNotContains(page,'Bilal Customer')
        self.assertNotContains(self.client.get(reverse('collection',args=['Supplier'])),'Bravo Supplier')
        self.login(self.b);self.assertContains(self.client.get(reverse('collection',args=['Supplier'])),'Bravo Supplier')
    def test_another_shops_customer_cannot_be_opened_edited_or_found(self):
        self.login(self.a)
        for url in (reverse('party',args=[self.cust_b.pk]),reverse('statement',args=[self.cust_b.pk]),reverse('master_edit',args=['Customer',self.cust_b.pk]),reverse('party',args=[self.sup_b.pk])):
            self.assertEqual(self.client.get(url).status_code,404,url)
        post=self.client.post(reverse('master_edit',args=['Customer',self.cust_b.pk]),dict(name='Hijacked',credit_limit='1',enabled='on'));self.assertEqual(post.status_code,404)
        self.cust_b.refresh_from_db();self.assertEqual(self.cust_b.name,'Bilal Customer')
        found=self.client.get(reverse('search'),{'q':'Bilal'}).json()['results'];self.assertEqual([r for r in found if r['kind']=='Customer'],[])
        self.assertEqual(self.client.get(reverse('party',args=[self.cust_a.pk])).status_code,200)
    def test_owner_sees_every_shops_parties(self):
        self.login(self.user);page=self.client.get(reverse('collection',args=['Customer']));self.assertContains(page,'Alice Customer');self.assertContains(page,'Bilal Customer')
        self.assertEqual(self.client.get(reverse('party',args=[self.cust_b.pk])).status_code,200)
    def test_the_same_name_in_two_shops_is_two_separate_accounts(self):
        one=customer_names.resolve('Shared Name',self.a,branch=self.branch);two=customer_names.resolve('Shared Name',self.b,branch=self.other)
        self.assertNotEqual(one.pk,two.pk);self.assertEqual((one.branch_id,two.branch_id),(self.branch.pk,self.other.pk))
        one.credit_limit=Decimal('1000');one.save();two.refresh_from_db();self.assertEqual(two.credit_limit,0)
        self.assertEqual(customer_names.resolve('shared  name',self.a,branch=self.branch).pk,one.pk)
    def test_old_unassigned_customer_is_adopted_by_the_first_shop_that_uses_it(self):
        legacy=Party.objects.create(code='LEG',kind='Customer',name='Legacy Larry')
        got=customer_names.resolve('Legacy Larry',self.a,branch=self.branch);self.assertEqual(got.pk,legacy.pk);legacy.refresh_from_db();self.assertEqual(legacy.branch_id,self.branch.pk)
        other=customer_names.resolve('Legacy Larry',self.b,branch=self.other);self.assertNotEqual(other.pk,legacy.pk)
    def test_pos_and_forms_suggest_only_this_shops_customers(self):
        self.login(self.a);names=[p['name'] for p in self.client.get(reverse('pos')).context['customers']];self.assertIn('Alice Customer',names);self.assertNotIn('Bilal Customer',names)
        page=self.client.get(reverse('pos')).content.decode();self.assertIn('Alice Customer',page);self.assertNotIn('Bilal Customer',page)
    def test_credit_limit_only_counts_this_shops_debt(self):
        from . import credit
        self.assertEqual(credit.customer_balance(self.cust_a),0)
    def test_reports_party_filter_lists_only_this_shops_parties(self):
        from .report_filters import ReportFilters
        form=ReportFilters(user=self.a,name='Sales Register');names=[p.name for p in form.fields['party'].queryset];self.assertIn('Alice Customer',names);self.assertNotIn('Bilal Customer',names)


class AccountsAndHistory(Base):
    def test_a_shop_never_picks_another_shops_ledger_accounts(self):
        self.login(self.a);mine=[a.code for a in self.client.get(reverse('ledger')).context['accounts']]
        self.assertTrue(any(c.startswith(self.branch.code+'-') for c in mine));self.assertFalse(any(c.startswith(self.other.code+'-') for c in mine))
        self.login(self.user);every=[a.code for a in self.client.get(reverse('ledger')).context['accounts']];self.assertTrue(any(c.startswith(self.other.code+'-') for c in every))
        self.login(self.a);choices=[value for group,items in self.client.get(reverse('voucher_new')).context['form'].fields['account'].choices if isinstance(items,(list,tuple)) for value,_ in items]
        other_accounts={str(a.pk) for a in Account.objects.filter(code__startswith=self.other.code+'-')};self.assertFalse(set(choices)&other_accounts)
    def test_duplicate_imei_message_never_names_another_shops_phone(self):
        device=Workflows.intake(self,1)[2];number=device.imeis.first().number
        with self.assertRaises(ValidationError) as caught:easy_retail.parse_imeis(number,1)
        message=' '.join(caught.exception.messages);self.assertIn('already registered',message);self.assertNotIn(device.code,message);self.assertNotIn(device.status,message)
    def test_history_of_a_phone_moved_into_a_shop_hides_the_old_shops_purchase_and_parties(self):
        device=Workflows.intake(self,2)[2]
        transfers.move_now(self.user,self.branch,self.other,[device])
        device.refresh_from_db();self.login(self.b);page=self.client.get(reverse('device_history',args=[device.pk]))
        self.assertEqual(page.status_code,200);labels=[e['label'] for e in page.context['events']];self.assertNotIn('Received into stock',labels)
        self.login(self.user);self.assertIn('Received into stock',[e['label'] for e in self.client.get(reverse('device_history',args=[device.pk])).context['events']])
        self.login(self.a);self.assertEqual(self.client.get(reverse('device_history',args=[device.pk])).status_code,404)       # no longer in shop A


class Migrations(TransactionTestCase):
    """The data migration splits shared customers per shop and keeps exactly one login per shop."""
    def migrate(self,target):
        executor=MigrationExecutor(connection);executor.migrate(target);return executor.loader.project_state(target).apps
    def test_shared_party_is_cloned_per_shop_and_extra_logins_are_deactivated(self):
        before=self.migrate([('shop','0018_advanced_accounting')])
        Branch=before.get_model('shop','Branch');Party=before.get_model('shop','Party');Document=before.get_model('shop','Document');Staff=before.get_model('shop','Staff');User=before.get_model('auth','User')
        one=Branch.objects.create(code='M1',name='M1',company='C');two=Branch.objects.create(code='M2',name='M2',company='C')
        shared=Party.objects.create(code='SH',kind='Customer',name='Shared Sam',phone='0300 5555555',credit_limit=1000)
        for branch,n in ((one,2),(two,1)):
            for i in range(n):Document.objects.create(code=f'D-{branch.code}-{i}',kind='Sales Invoice',branch=branch,customer=shared,status='Submitted')
        Party.objects.create(code='UN',kind='Customer',name='Unused Una')
        keep=User.objects.create(username='keep');extra=User.objects.create(username='extra')
        Staff.objects.create(code='M1-SHOP',name='M1',branch=one,user=keep,enabled=True,manager=True)
        Staff.objects.create(code='M1-X',name='Extra',branch=one,user=extra,enabled=True,can_sell=True)
        after=self.migrate([('shop','0020_split_parties_and_logins')])
        Party=after.get_model('shop','Party');Document=after.get_model('shop','Document');Staff=after.get_model('shop','Staff');User=after.get_model('auth','User')
        mine=Party.objects.get(name='Shared Sam',branch__code='M1');theirs=Party.objects.get(name='Shared Sam',branch__code='M2')
        self.assertNotEqual(mine.pk,theirs.pk);self.assertEqual((mine.phone,theirs.phone,theirs.credit_limit),('0300 5555555','0300 5555555',1000))
        self.assertEqual(Document.objects.filter(customer=mine).count(),2);self.assertEqual(Document.objects.filter(customer=theirs).count(),1);self.assertFalse(Document.objects.filter(customer__branch__isnull=True).exists())
        self.assertIsNone(Party.objects.get(name='Unused Una').branch_id)
        self.assertTrue(User.objects.get(username='keep').is_active);self.assertFalse(User.objects.get(username='extra').is_active)
        self.assertIsNone(Staff.objects.get(code='M1-X').user_id);self.assertFalse(Staff.objects.get(code='M1-X').enabled);self.assertTrue(Staff.objects.get(code='M1-SHOP').can_buy)
        self.migrate([('shop','0020_split_parties_and_logins')])


class ShopAccountsScreen(Base):
    def test_owner_edits_shop_details_and_shop_cannot(self):
        self.login(self.user)
        post=self.client.post(reverse('users'),dict(action='edit',edit_branch=str(self.other.pk),name='Renamed Shop',shop_phone='0300 9999999',shop_address='Main Road'),follow=True)
        self.assertContains(post,'Shop details saved');self.other.refresh_from_db();self.assertEqual(self.other.name,'Renamed Shop')
        config=ShopSettings.objects.get(branch=self.other);self.assertEqual((config.shop_phone,config.shop_address),('0300 9999999','Main Road'))
        bad=self.client.post(reverse('users'),dict(action='edit',edit_branch=str(self.other.pk),name=self.branch.name),follow=True);self.assertContains(bad,'Another shop already has this name')
        self.login(self.a);self.assertEqual(self.client.post(reverse('users'),dict(action='edit',edit_branch=str(self.other.pk),name='Hijack')).status_code,403)
