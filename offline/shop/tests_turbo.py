"""In-place page navigation (Turbo): server rules that make background form posts behave exactly like normal ones."""
from django.contrib.staticfiles import finders
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .tests import fixtures

TURBO={'HTTP_ACCEPT':'text/vnd.turbo-stream.html, text/html, application/xhtml+xml'}


class Forms(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
    def bad_purchase(self,**headers):
        # No brand or model typed: the screen must show its messages again, nothing is saved.
        return self.client.post(reverse('easy_entry',args=['purchase']),dict(branch=str(self.branch.pk),request_key='00000000-0000-4000-8000-0000000000a1',party_name='X',quantity='1',price='1',asking_price='2',pta_status='PTA',device_type='Used',condition='Good',payment_status='unpaid',warehouse=str(self.warehouse.pk),staff=str(self.buyer.pk),posting_date=str(timezone.localdate())),**headers)
    def test_a_form_shown_again_with_errors_is_marked_422_for_turbo_only(self):
        normal=self.bad_purchase();self.assertEqual(normal.status_code,200);self.assertContains(normal,'Enter the brand')
        turbo=self.bad_purchase(**TURBO);self.assertEqual(turbo.status_code,422);self.assertContains(turbo,'Enter the brand',status_code=422)
    def test_a_redirect_after_saving_is_left_alone(self):
        r=self.client.post(reverse('logout'),{},**TURBO);self.assertEqual(r.status_code,302)
    def test_ordinary_page_views_are_left_alone(self):
        self.assertEqual(self.client.get(reverse('dashboard'),**TURBO).status_code,200)
    def test_json_calls_are_left_alone(self):
        r=self.client.post(reverse('pos_checkout'),'{}',content_type='application/json',HTTP_ACCEPT='application/json');self.assertNotEqual(r.status_code,422)


class Shell(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user)
    def test_pages_load_the_navigation_scripts_once_in_the_head_and_never_cache_snapshots(self):
        html=self.client.get(reverse('dashboard')).content.decode();head=html[:html.index('</head>')]
        for name in ('vendor/turbo-','/page.','/offline.','/payments.','/shell.','/ui.'):self.assertIn(name,head)
        self.assertIn('name="turbo-cache-control" content="no-cache"',head)
        body=html[html.index('</head>'):]
        for name in ('/offline.','/payments.','/shell.','/ui.','vendor/turbo-'):self.assertNotIn(name,body)
    def test_dialogs_and_search_survive_page_swaps(self):
        html=self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('id="confirm-dialog" data-turbo-permanent',html);self.assertIn('id="palette" data-turbo-permanent',html)
    def test_standalone_pages_force_a_full_load(self):
        self.client.logout();self.assertContains(self.client.get(reverse('login')),'turbo-visit-control')
    def test_downloads_are_not_intercepted(self):
        from pathlib import Path
        root=Path(__file__).resolve().parent.parent/'templates'
        for name in ('ledger.html','statement.html'):self.assertIn('data-turbo="false" href="?{{ request.GET.urlencode }}',(root/name).read_text(encoding='utf8'))
        self.assertIn('name="format" value="csv" data-turbo="false"',(root/'report.html').read_text(encoding='utf8'))
    def test_script_files_exist(self):
        for name in ('vendor/turbo-8.0.13.js','page.js','offline.js','payments.js','shell.js','ui.js','connection.js','pos.js'):self.assertTrue(finders.find(name),name)
