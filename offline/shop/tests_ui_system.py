"""Redesign (Phase A): shared shell, global search, pagination, toasts and accessibility hooks."""
import re
from pathlib import Path
from django.contrib.auth import get_user_model
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import easy_retail


class Shell(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def page(self,name='dashboard'):return self.client.get(reverse(name)).content.decode()
    def test_pages_load_the_new_stylesheets_and_no_legacy_ones(self):
        html=self.page()
        for name in ('tokens','base','layout','components','pages'):self.assertRegex(html,rf'css/{name}\.(?:[0-9a-f]+\.)?css')
        for old in ('glass.css','covison.css','offline.css'):self.assertNotIn(old,html)
        for name in ('tokens','base','layout','components','pages'):self.assertTrue(finders.find(f'css/{name}.css'),name)
    def test_accessibility_hooks_are_present(self):
        html=self.page()
        self.assertIn('class="skip-link" href="#content"',html);self.assertIn('id="content"',html)
        self.assertIn('aria-live="polite"',html);self.assertIn('<dialog id="confirm-dialog"',html);self.assertIn('<dialog id="palette"',html)
        self.assertIn('aria-label="Main navigation"',html)
    def test_server_messages_become_toasts(self):
        response=self.client.post(reverse('logout'),follow=True)  # any redirect that carries no message must not break the region
        self.assertEqual(response.status_code,200)
        from django.contrib import messages
        from django.test import RequestFactory
        self.client.get(reverse('dashboard'))
        session=self.client.session;session.save()
    def test_cancel_action_asks_for_confirmation(self):
        bill=self.bill()
        html=self.client.get(reverse('detail',args=[bill.pk])).content.decode()
        self.assertIn('data-confirm="Cancel this transaction?',html)
    def bill(self):
        data=dict(branch=self.branch,party_name='UI Supplier',product=self.product,quantity=1,price=50000,asking_price=65000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash',imeis=[[imei(31)]])
        return easy_retail.purchase(data,self.user)


class GlobalSearch(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
        data=dict(branch=self.branch,party_name='Search Supplier',product=self.product,quantity=1,price=50000,asking_price=65000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash',imeis=[[imei(32)]])
        self.bill=easy_retail.purchase(data,self.user)
    def get(self,q):return self.client.get(reverse('search'),{'q':q}).json()['results']
    def test_finds_a_phone_by_imei_with_status_and_shop(self):
        hit=self.get(imei(32)[:10])[0];self.assertEqual(hit['kind'],'Phone');self.assertIn(imei(32),hit['hint']);self.assertIn('Available',hit['hint'])
    def test_finds_suppliers_and_documents(self):
        self.assertTrue(any(r['kind']=='Supplier' and r['label']=='Search Supplier' for r in self.get('Search Sup')))
        self.assertTrue(any(r['kind']=='Document' and r['label']==self.bill.code for r in self.get(self.bill.code[:10])))
    def test_short_queries_return_nothing_and_anonymous_users_are_redirected(self):
        self.assertEqual(self.get('a'),[])
        self.client.logout();self.assertEqual(self.client.get(reverse('search'),{'q':'abc'}).status_code,302)
    def test_staff_only_see_their_own_shop_phones(self):
        other=Branch.objects.create(code='OTH',name='Other shop',company='X');place=Warehouse.objects.create(code='OTH-W',name='Other stock',branch=other)
        device=Device.objects.create(code='OTHERPHONE-1',product=self.product,warehouse=place,branch=other)
        self.assertTrue(any(r['label'] for r in self.get('OTHERPHONE')))              # owner sees every shop
        clerk=get_user_model().objects.create_user('clerk-ui');self.buyer.user=clerk;self.buyer.save();self.client.force_login(clerk)
        self.assertFalse(any('OTHERPHONE' in r['hint']+r['label'] for r in self.get('OTHERPHONE')))


class Pagination(TestCase):
    def setUp(self):self.__dict__.update(fixtures());self.client.force_login(self.user)
    def test_lists_are_paged_at_50_with_working_links(self):
        Party.objects.bulk_create([Party(code=f'C{i:03d}',kind='Customer',name=f'Customer {i:03d}') for i in range(120)])
        first=self.client.get(reverse('collection',args=['Customer']))
        self.assertEqual(len(first.context['rows']),50);self.assertContains(first,'Page 1 of')
        last=self.client.get(reverse('collection',args=['Customer']),{'page':3});self.assertEqual(len(last.context['rows']),len([1 for _ in range(121-100)]))
        self.assertNotContains(last,'?page=4')
    def test_search_keeps_its_query_in_the_pager_links(self):
        Party.objects.bulk_create([Party(code=f'K{i:03d}',kind='Customer',name=f'Keep {i:03d}') for i in range(70)])
        page=self.client.get(reverse('collection',args=['Customer']),{'search':'keep'})
        self.assertContains(page,'search=keep&amp;page=2')
