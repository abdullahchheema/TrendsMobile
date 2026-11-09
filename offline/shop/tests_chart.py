"""Phase B: chart of accounts tree and standard Pakistani retail chart."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import *
from .tests import fixtures
from . import coa,financial_reports,services as s


class Standard(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def test_standard_chart_installs_once_with_a_proper_tree(self):
        first=coa.install_standard(self.user);second=coa.install_standard(self.user)
        self.assertEqual(first,len(coa.STANDARD));self.assertEqual(second,0)
        rent=Account.objects.get(code='COA-6100');self.assertEqual((rent.root_type,rent.parent.code,rent.account_type),('Expense','COA-6000','Expense Account'))
        self.assertTrue(Account.objects.get(code='COA-1000').is_group);self.assertIsNone(Account.objects.get(code='COA-1000').parent)
        for needed in ('Owner\'s capital','Owner\'s drawings','Retained earnings','Business loans','Accumulated depreciation','Shop rent','Employee salaries','Electricity','Internet and phone','Customer advances'):
            self.assertTrue(Account.objects.filter(name=needed).exists(),needed)
    def test_chart_page_shows_indented_tree_and_installs_from_the_button(self):
        page=self.client.post(reverse('chart'),dict(action='install'),follow=True);self.assertContains(page,'Standard chart installed')
        self.assertContains(page,'Operating expenses');self.assertContains(page,'Shop rent')
    def test_group_balances_roll_up_children_on_their_natural_side(self):
        coa.install_standard(self.user)
        cash=Account.objects.get(code='COA-1110');capital=Account.objects.get(code='COA-3100');doc=s.new_doc('Journal Entry',self.branch,status='Submitted')
        s.entry(doc,cash,debit=Decimal('5000'));s.entry(doc,capital,credit=Decimal('5000'))
        rows={r['account'].code:r['balance'] for r in coa.tree()}
        self.assertEqual(rows['COA-1110'],Decimal('5000'));self.assertEqual(rows['COA-1100'],Decimal('5000'));self.assertEqual(rows['COA-1000'],Decimal('5000'))
        self.assertEqual(rows['COA-3100'],Decimal('5000'));self.assertEqual(rows['COA-3000'],Decimal('5000'))   # equity shown positive on its credit side


class Rules(TestCase):
    def setUp(self):self.__dict__.update(fixtures());coa.install_standard(self.user)
    def test_parent_must_be_a_group_in_the_same_section_without_loops(self):
        cash=Account.objects.get(code='COA-1110');rent=Account.objects.get(code='COA-6100');assets=Account.objects.get(code='COA-1000');expenses=Account.objects.get(code='COA-6000')
        with self.assertRaisesMessage(ValidationError,'group account'):Account(code='X1',name='Bad',account_type='Cash',parent=cash).full_clean()
        with self.assertRaisesMessage(ValidationError,'same section'):Account(code='X2',name='Bad',account_type='Cash',root_type='Asset',parent=expenses).full_clean()
        assets.parent=Account.objects.get(code='COA-1100')
        with self.assertRaisesMessage(ValidationError,'loop'):assets.full_clean()
    def test_an_account_with_sub_accounts_cannot_stop_being_a_group(self):
        group=Account.objects.get(code='COA-6000');group.is_group=False
        with self.assertRaisesMessage(ValidationError,'sub-accounts'):group.full_clean()
    def test_child_inherits_its_parents_section(self):
        child=Account(code='COA-6150',name='Shop insurance',account_type='Expense Account',parent=Account.objects.get(code='COA-6000'));child.full_clean();child.save();self.assertEqual(child.root_type,'Expense')


class StatementsUseSections(TestCase):
    def setUp(self):self.__dict__.update(fixtures());coa.install_standard(self.user)
    def test_new_accounts_classify_without_guessing_from_the_type_name(self):
        result=financial_reports.classifications();by_code={a.code:root for a,root in result.values()}
        self.assertEqual(by_code['COA-4100'],'Income');self.assertEqual(by_code['COA-6100'],'Expense');self.assertEqual(by_code['COA-2500'],'Liability');self.assertEqual(by_code['COA-3300'],'Equity')
    def test_accounts_created_with_a_new_shop_carry_their_section(self):
        from . import setup
        branch=setup.create_shop(dict(code='RT1',name='Section Shop',company='X',warehouse_name='W',till_name='T',payment_methods=['Cash','Bank Transfer']),self.user)
        roots={a.account_type:a.root_type for a in Account.objects.filter(code__startswith='RT1-')}
        self.assertEqual((roots['Receivable'],roots['Payable'],roots['Income Account'],roots['Cost of Goods Sold'],roots['Cash'],roots['Bank']),('Asset','Liability','Income','Expense','Asset','Asset'))


class Access(TestCase):
    def setUp(self):self.__dict__.update(fixtures())
    def test_accountant_can_open_and_edit_accounts_but_sales_staff_cannot(self):
        acct=get_user_model().objects.create_user('acct-coa',password='Unrelated-test-password-729!');Staff.objects.create(code='ACCT-C',name='Acct C',branch=self.branch,user=acct,role=Role.objects.get(code='accountant'))
        clerk=get_user_model().objects.create_user('clerk-coa',password='Unrelated-test-password-729!');Staff.objects.create(code='CLERK-C',name='Clerk C',branch=self.branch,user=clerk,role=Role.objects.get(code='sales'))
        self.client.force_login(acct);self.assertEqual(self.client.get(reverse('chart')).status_code,200);self.assertEqual(self.client.get(reverse('master_new',args=['Account'])).status_code,200)
        self.client.force_login(clerk);self.assertEqual(self.client.get(reverse('chart')).status_code,403);self.assertEqual(self.client.get(reverse('master_new',args=['Account'])).status_code,403)
    def test_account_form_creates_a_sub_account_under_a_group(self):
        coa.install_standard(self.user);self.client.force_login(self.user);parent=Account.objects.get(code='COA-6000')
        self.client.post(reverse('master_new',args=['Account']),dict(code='COA-6950',name='Zakat and donations',account_type='Expense Account',parent=str(parent.pk),enabled='on'))
        made=Account.objects.get(code='COA-6950');self.assertEqual((made.parent_id,made.root_type),(parent.pk,'Expense'))
    def test_a_group_account_needs_no_type(self):
        self.client.force_login(self.user)
        self.client.post(reverse('master_new',args=['Account']),dict(code='GRP-1',name='Marketing group',root_type='Expense',is_group='on',enabled='on'))
        self.assertTrue(Account.objects.get(code='GRP-1').is_group)
