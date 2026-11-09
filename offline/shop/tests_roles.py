"""Phase B: roles and permissions, derived flags, role admin, audit viewer."""
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import TestCase
from django.urls import reverse
from .models import *
from .tests import fixtures
from . import access,permissions,services as s


class RoleBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures())
    def member(self,role_code,name='Member'):
        user=get_user_model().objects.create_user(name.lower().replace(' ','-'),password='Unrelated-test-password-729!')
        person=Staff.objects.create(code=name.upper().replace(' ','-'),name=name,branch=self.branch,user=user,role=Role.objects.get(code=role_code))
        return user,person


class RoleCatalogue(RoleBase):
    def test_default_roles_exist_and_are_marked_built_in(self):
        codes=set(Role.objects.filter(system=True).values_list('code',flat=True))
        self.assertTrue({'owner','manager','sales','accountant'}<=codes)
        self.assertEqual(set(Role.objects.get(code='owner').permissions),permissions.CODES)
    def test_staff_flags_follow_the_role_on_save(self):
        _,manager=self.member('manager','Mgr');self.assertEqual((manager.manager,manager.can_buy,manager.can_sell,manager.can_handle_payments),(True,True,True,True))
        _,sales=self.member('sales','Roletest Seller');self.assertEqual((sales.manager,sales.can_buy,sales.can_sell,sales.can_handle_payments),(False,False,True,False))
        _,accountant=self.member('accountant','Acct');self.assertEqual((accountant.manager,accountant.can_buy,accountant.can_sell,accountant.can_handle_payments),(False,False,False,True))
    def test_staff_without_a_role_still_resolve_from_legacy_flags(self):
        self.assertEqual(permissions.staff_permissions(self.buyer),{'buy','view_costs'})
        self.assertIn('sell',permissions.staff_permissions(self.seller))


class AccountantRole(RoleBase):
    def setUp(self):
        super().setUp();self.user_acct,self.acct=self.member('accountant','Accountant One');self.client.force_login(self.user_acct)
    def test_accountant_reads_statements_and_cost_reports_but_cannot_sell_or_buy(self):
        self.assertTrue(access.statements(self.user_acct));self.assertTrue(access.costs(self.user_acct))
        self.assertEqual(self.client.get(reverse('report',args=['Profit and Loss Statement'])).status_code,200)
        self.assertEqual(self.client.get(reverse('report',args=['Accounts Payable'])).status_code,200)
        self.assertEqual(self.client.get(reverse('easy_entry',args=['sale'])).status_code,403)
        self.assertEqual(self.client.get(reverse('easy_entry',args=['purchase'])).status_code,403)
    def test_accountant_menu_hides_buying_and_selling_but_shows_audit(self):
        html=self.client.get(reverse('dashboard')).content.decode()
        self.assertNotIn(reverse('easy_entry',args=['purchase']),html);self.assertNotIn(reverse('easy_entry',args=['sale']),html)
        self.assertIn(reverse('audit_log'),html);self.assertIn(reverse('sales_accounts'),html)
    def test_accountant_cannot_manage_roles(self):
        self.assertEqual(self.client.get(reverse('roles')).status_code,403)


class RoleAdmin(RoleBase):
    def setUp(self):super().setUp();self.client.force_login(self.user)
    def test_owner_edits_a_role_and_members_follow_with_an_audit_trail(self):
        _,seller=self.member('sales','Seller Two')
        form={'name':'Sales Employee','description':'x','discount_max_pct':'10','refund_max':'0','enabled':'on','perms':['sell','payments']}
        self.assertRedirects(self.client.post(reverse('role_edit',args=['sales']),form),reverse('roles'))
        seller.refresh_from_db();self.assertTrue(seller.can_handle_payments)
        entry=Audit.objects.filter(action='Edit role').latest('created')
        self.assertEqual(entry.details['before']['permissions'],['sell']);self.assertEqual(entry.details['after']['permissions'],['payments','sell'])
    def test_custom_role_can_be_created_and_the_owner_role_is_read_only(self):
        form={'name':'Phone Technician Lead','description':'','discount_max_pct':'0','refund_max':'0','enabled':'on','perms':['adjust_stock']}
        self.client.post(reverse('role_new'),form);self.assertTrue(Role.objects.filter(code='phone-technician-lead',permissions=['adjust_stock']).exists())
        page=self.client.post(reverse('role_edit',args=['owner']),dict(form,name='Owner'));self.assertContains(page,'cannot be edited')
    def test_non_owner_without_the_permission_is_refused(self):
        user,_=self.member('manager','Manager Two');self.client.force_login(user)
        self.assertEqual(self.client.get(reverse('roles')).status_code,403)


class StaffForm(RoleBase):
    def setUp(self):super().setUp();self.client.force_login(self.user)
    def test_staff_form_assigns_the_role_by_code_and_hashes_the_pin(self):
        self.client.post(reverse('master_new',args=['Mobile Staff']),dict(name='Counter Cashier',branch=str(self.branch.pk),default_warehouse=str(self.warehouse.pk),role='cashier',enabled='on',approval_pin='4821'))
        person=Staff.objects.get(name='Counter Cashier')
        self.assertEqual(person.role.code,'cashier');self.assertTrue(person.can_handle_payments and not person.manager)
        self.client.post(reverse('master_new',args=['Mobile Staff']),dict(name='Floor Manager',branch=str(self.branch.pk),default_warehouse=str(self.warehouse.pk),role='manager',enabled='on',approval_pin='73019'))
        boss=Staff.objects.get(name='Floor Manager');self.assertNotEqual(boss.approval_pin,'73019');self.assertTrue(check_password('73019',boss.approval_pin))


class AuditViewer(RoleBase):
    def setUp(self):
        super().setUp();self.other=Branch.objects.create(code='OTH',name='Other shop',company='X')
        s.audit(self.user,'Test mine',None,branch=self.branch,note='visible');s.audit(self.user,'Test other',None,branch=self.other,note='hidden')
    def test_owner_sees_everything(self):
        self.client.force_login(self.user);page=self.client.get(reverse('audit_log'));self.assertContains(page,'Test mine');self.assertContains(page,'Test other')
    def test_branch_staff_only_see_their_own_shop(self):
        user,_=self.member('manager','Manager Three');self.client.force_login(user)
        page=self.client.get(reverse('audit_log'));self.assertContains(page,'Test mine');self.assertNotContains(page,'Test other')
    def test_sales_staff_cannot_open_it_and_filters_work(self):
        user,_=self.member('sales','Seller Three');self.client.force_login(user);self.assertEqual(self.client.get(reverse('audit_log')).status_code,403)
        self.client.force_login(self.user);page=self.client.get(reverse('audit_log'),{'action':'mine'});self.assertEqual([a.action for a in page.context['rows']],['Test mine'])
