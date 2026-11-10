"""Owner-only stock transfers with statuses: Pending, Dispatched, Received, Discrepancy, Cancelled."""
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.db.models import Sum
from django.test import TestCase
from django.urls import reverse
from .models import *
from .tests import Workflows
from .tests_transfers import transfer_setup
from . import coa,dashboard_metrics as dm,reports,shop_accounts,shopdefaults,transfers as tr,services as s

D=Decimal


class Base(TestCase):
    def setUp(self):
        transfer_setup(self);coa.install_standard(self.user)
        self.a=shop_accounts.create_login(self.branch,'tf-a','Strong-pass-for-TA-77!');self.b=shop_accounts.create_login(self.other,'tf-b','Strong-pass-for-TB-77!')
        self.n=0
    def phone(self):
        self.n+=1;return Workflows.intake(self,self.n)[2]
    def login(self,user):self.client.force_login(user)
    def stock_value(self,branch):
        return s.account_balance(branch,Account.objects.get(pk=ShopSettings.objects.get(branch=branch).accounts['inventory']))
    def where(self,device):
        device.refresh_from_db();return device.branch_id,device.status
    def balanced(self,doc):
        for branch in (self.branch,self.other):
            t=LedgerEntry.objects.filter(document=doc,branch=branch).aggregate(d=Sum('debit'),c=Sum('credit'));self.assertEqual(t['d'] or 0,t['c'] or 0,branch.name)


class Lifecycle(Base):
    def test_pending_reserves_dispatch_moves_out_receive_moves_in_and_books_balance_at_every_step(self):
        phone=self.phone();cost=phone.purchase_cost;before=self.stock_value(self.branch)
        doc=tr.create(self.user,self.branch,self.other,[phone]);self.assertEqual((tr.status_of(doc),self.where(phone)),('Pending',(self.branch.pk,'Reserved')))
        self.assertEqual(self.stock_value(self.branch),before)                                   # nothing has left yet
        tr.dispatch(self.user,doc);phone.refresh_from_db();doc.refresh_from_db()
        self.assertEqual((tr.status_of(doc),phone.status,phone.warehouse_id),('Dispatched','In transit',None));self.assertEqual(self.stock_value(self.branch),before-cost)
        self.assertEqual(self.stock_value(self.other),0);self.assertEqual(s.on_hand(phone.product,shopdefaults.warehouse(self.branch))[0],0);self.balanced(doc)
        tr.receive(self.user,doc);phone.refresh_from_db();doc.refresh_from_db()
        self.assertEqual((tr.status_of(doc),phone.branch_id,phone.status,phone.warehouse.branch_id),('Received',self.other.pk,'Available',self.other.pk))
        self.assertEqual(self.stock_value(self.other),cost);self.assertEqual(s.on_hand(phone.product,shopdefaults.warehouse(self.other))[0],1);self.balanced(doc)
    def test_a_phone_is_never_available_in_two_shops_during_a_transfer(self):
        phone=self.phone();doc=tr.create(self.user,self.branch,self.other,[phone])
        for step in (lambda:None,lambda:tr.dispatch(self.user,doc),lambda:tr.receive(self.user,doc)):
            step();self.assertLessEqual(Device.objects.filter(pk=phone.pk,status='Available').count(),1)
        self.assertEqual(Device.objects.filter(pk=phone.pk,status='Available',branch=self.branch).count(),0)
    def test_reserved_and_in_transit_phones_cannot_be_sold_or_listed_in_the_pos(self):
        phone=self.phone();doc=tr.create(self.user,self.branch,self.other,[phone]);self.login(self.a)
        self.assertNotIn(str(phone.pk),[i['device'] for i in self.client.get(reverse('pos')).context['items'] if i.get('serialized')])
        tr.dispatch(self.user,doc);self.assertNotIn(str(phone.pk),[i['device'] for i in self.client.get(reverse('pos')).context['items'] if i.get('serialized')])
        tr.receive(self.user,doc);self.login(self.b);self.assertIn(str(phone.pk),[i['device'] for i in self.client.get(reverse('pos')).context['items'] if i.get('serialized')])
        self.login(self.a);self.assertNotIn(str(phone.pk),[i['device'] for i in self.client.get(reverse('pos')).context['items'] if i.get('serialized')])
    def test_rules_same_shop_no_phones_unavailable_phone_and_wrong_status_actions(self):
        phone=self.phone()
        with self.assertRaises(ValidationError):tr.create(self.user,self.branch,self.branch,[phone])
        with self.assertRaises(ValidationError):tr.create(self.user,self.branch,self.other,[])
        doc=tr.create(self.user,self.branch,self.other,[phone])
        with self.assertRaises(ValidationError):tr.create(self.user,self.branch,self.other,[phone])         # already reserved
        with self.assertRaises(ValidationError):tr.receive(self.user,doc)                                    # not dispatched yet
        tr.dispatch(self.user,doc)
        with self.assertRaises(ValidationError):tr.dispatch(self.user,doc)
        with self.assertRaises(ValidationError):tr.update_pending(self.user,doc,self.other,[phone])


class Discrepancy(Base):
    def setup_two(self):
        one,two=self.phone(),self.phone();doc=tr.create(self.user,self.branch,self.other,[one,two]);tr.dispatch(self.user,doc);return one,two,doc
    def test_a_missing_phone_makes_a_discrepancy_that_can_be_returned_to_the_sender(self):
        one,two,doc=self.setup_two();before=self.stock_value(self.branch)
        tr.receive(self.user,doc,[str(one.pk)]);doc.refresh_from_db();self.assertEqual(tr.status_of(doc),'Discrepancy');self.assertEqual(self.where(one),(self.other.pk,'Available'));self.assertEqual(self.where(two),(self.branch.pk,'In transit'))
        tr.resolve(self.user,doc,'return');doc.refresh_from_db();self.assertEqual(tr.status_of(doc),'Received');self.assertEqual(self.where(two),(self.branch.pk,'Available'));self.assertEqual(self.stock_value(self.branch),before+two.purchase_cost);self.balanced(doc)
    def test_a_missing_phone_can_be_written_off_as_a_stock_loss(self):
        one,two,doc=self.setup_two();tr.receive(self.user,doc,[str(one.pk)]);loss_before=s.account_balance(self.branch,Account.objects.get(code='COA-5200'))
        tr.resolve(self.user,doc,'write_off','Lost in the post');two.refresh_from_db();self.assertEqual(two.status,'Written off')
        self.assertEqual(s.account_balance(self.branch,Account.objects.get(code='COA-5200')),loss_before+two.purchase_cost);self.balanced(doc)
    def test_the_rest_can_arrive_late(self):
        one,two,doc=self.setup_two();tr.receive(self.user,doc,[str(one.pk)]);tr.receive(self.user,doc,[str(two.pk)]);doc.refresh_from_db()
        self.assertEqual(tr.status_of(doc),'Received');self.assertEqual(self.where(two),(self.other.pk,'Available'));self.balanced(doc)
    def test_choose_at_least_one_phone_and_a_valid_action(self):
        one,two,doc=self.setup_two()
        with self.assertRaises(ValidationError):tr.receive(self.user,doc,[])
        tr.receive(self.user,doc,[str(one.pk)])
        with self.assertRaises(ValidationError):tr.resolve(self.user,doc,'shrug')


class CancelAndChange(Base):
    def test_cancelling_a_pending_transfer_releases_the_phones(self):
        phone=self.phone();doc=tr.create(self.user,self.branch,self.other,[phone]);tr.cancel(self.user,doc,'Plans changed');doc.refresh_from_db()
        self.assertEqual((tr.status_of(doc),doc.status,self.where(phone)),('Cancelled','Cancelled',(self.branch.pk,'Available')))
    def test_cancelling_a_dispatched_transfer_returns_the_phones_and_restores_the_books(self):
        phone=self.phone();before=self.stock_value(self.branch);doc=tr.create(self.user,self.branch,self.other,[phone]);tr.dispatch(self.user,doc);tr.cancel(self.user,doc)
        self.assertEqual((self.where(phone),self.stock_value(self.branch),self.stock_value(self.other)),(((self.branch.pk,'Available')),before,0));self.balanced(doc);self.assertEqual(s.on_hand(phone.product,shopdefaults.warehouse(self.branch))[0],1)
    def test_a_transfer_with_arrived_phones_cannot_be_cancelled_and_a_finished_one_neither(self):
        one,two=self.phone(),self.phone();doc=tr.create(self.user,self.branch,self.other,[one,two]);tr.dispatch(self.user,doc);tr.receive(self.user,doc,[str(one.pk)])
        with self.assertRaises(ValidationError):tr.cancel(self.user,doc)
    def test_a_pending_transfer_can_change_destination_and_phones(self):
        a,b=self.phone(),self.phone();third=Branch.objects.create(code='THIRD3',name='Third shop',company=self.branch.company);doc=tr.create(self.user,self.branch,self.other,[a])
        tr.update_pending(self.user,doc,third,[b]);doc.refresh_from_db()
        self.assertEqual((self.where(a),self.where(b)),(((self.branch.pk,'Available')),((self.branch.pk,'Reserved'))));self.assertEqual((doc.data['transfer']['target_shop'],doc.data['devices']),('Third shop',[str(b.pk)]))


class OwnerOnly(Base):
    def test_a_shop_login_can_do_none_of_it_through_the_screens_or_the_functions(self):
        phone=self.phone();doc=tr.create(self.user,self.branch,self.other,[phone]);self.login(self.a)
        for url in (reverse('stock_transfer'),reverse('transfer_detail',args=[doc.pk]),reverse('transfer_slip',args=[doc.pk])):self.assertEqual(self.client.get(url).status_code,403,url)
        for action in ('dispatch','cancel','receive','return','write_off','update'):self.assertEqual(self.client.post(reverse('transfer_detail',args=[doc.pk]),dict(action=action,request_key='00000000-0000-4000-8000-0000000000c1')).status_code,403,action)
        for call in (lambda:tr.create(self.a,self.branch,self.other,[phone]),lambda:tr.dispatch(self.a,doc),lambda:tr.cancel(self.a,doc),lambda:tr.receive(self.a,doc)):
            with self.assertRaises(PermissionDenied):call()
        self.assertEqual(tr.status_of(Document.objects.get(pk=doc.pk)),'Pending')
        side=self.client.get(reverse('dashboard')).content.decode().split('<aside',1)[1].split('</aside>',1)[0];self.assertNotIn('Stock Transfers',side)


class Screens(Base):
    def test_owner_creates_dispatches_receives_through_the_screens(self):
        phone=self.phone();self.login(self.user)
        page=self.client.get(reverse('stock_transfer'),{'branch':str(self.branch.pk)});self.assertContains(page,phone.code);self.assertContains(page,'Stock Transfers')
        post=self.client.post(reverse('stock_transfer'),dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-0000000000d1',target=str(self.other.pk),phone=[str(phone.pk)],mode='pending'),follow=True)
        doc=Document.objects.get(kind='Stock Entry');self.assertContains(post,'created (pending)');self.assertEqual(tr.status_of(doc),'Pending')
        for n,(action,text) in enumerate((('dispatch','dispatched'),('receive','Receipt recorded')),2):
            extra=dict(arrived=[str(phone.pk)]) if action=='receive' else {}
            page=self.client.post(reverse('transfer_detail',args=[doc.pk]),dict(action=action,request_key=f'00000000-0000-4000-8000-0000000000d{n}',**extra),follow=True);self.assertContains(page,text)
        doc.refresh_from_db();self.assertEqual(tr.status_of(doc),'Received');self.assertEqual(self.where(phone),(self.other.pk,'Available'))
        self.assertContains(self.client.get(reverse('transfer_detail',args=[doc.pk])),'Received');self.assertContains(self.client.get(reverse('transfer_slip',args=[doc.pk])),'<h1>'+self.branch.name+'</h1>')
    def test_create_and_dispatch_in_one_step_by_imei_and_a_bad_imei_is_refused(self):
        phone=self.phone();number=phone.imeis.first().number;self.login(self.user)
        data=dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-0000000000e1',target=str(self.other.pk),imeis=number,mode='dispatch')
        page=self.client.post(reverse('stock_transfer'),data,follow=True);self.assertContains(page,'dispatched');self.assertEqual(self.where(phone),(self.branch.pk,'In transit'))
        bad=self.client.post(reverse('stock_transfer'),dict(data,request_key='00000000-0000-4000-8000-0000000000e2',imeis='123456789012345'),follow=True);self.assertContains(bad,'Not available in the sending shop')
    def test_same_form_twice_creates_one_transfer(self):
        phone=self.phone();self.login(self.user);data=dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-0000000000f1',target=str(self.other.pk),phone=[str(phone.pk)],mode='pending')
        self.client.post(reverse('stock_transfer'),data);self.client.post(reverse('stock_transfer'),data);self.assertEqual(Document.objects.filter(kind='Stock Entry').count(),1)
    def test_filters_counts_menu_and_dashboards_show_pending_transfers(self):
        phone=self.phone();doc=tr.create(self.user,self.branch,self.other,[phone]);tr.dispatch(self.user,doc);self.login(self.user)
        page=self.client.get(reverse('stock_transfer'),{'state':'Open'});self.assertEqual(page.context['counts']['Dispatched'],1);self.assertContains(page,doc.code)
        self.assertEqual([r['doc'].pk for r in self.client.get(reverse('stock_transfer'),{'state':'Received'}).context['rows']],[])
        side=page.content.decode().split('<aside',1)[1].split('</aside>',1)[0]
        for text in ('All Transfers','Pending Transfers','Transfer History'):self.assertIn(text,side,text)
        self.assertEqual(dm.pending_transfers([self.other]),(1,0));self.assertEqual(dm.pending_transfers([self.branch]),(0,1))
        self.login(self.b);self.assertContains(self.client.get(reverse('dashboard')),'1 in')
        self.login(self.a);self.assertContains(self.client.get(reverse('dashboard')),'0 in · 1 out')
    def test_transfer_report_shows_the_status(self):
        phone=self.phone();doc=tr.create(self.user,self.branch,self.other,[phone]);cols,rows=reports.run('Stock Transfer Report',self.user,{})
        self.assertIn('Status',cols);self.assertEqual(rows[0]['Status'],'Pending')
        tr.dispatch(self.user,doc);tr.receive(self.user,doc);self.assertEqual(reports.run('Stock Transfer Report',self.user,{})[1][0]['Status'],'Received')
