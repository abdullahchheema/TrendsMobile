"""Page speed (query counts stay small) and the friendly page for an expired form."""
from datetime import date
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client,TestCase
from django.test.utils import CaptureQueriesContext
from . import dashboard_metrics as dm,report_extra
from .models import Branch


class World(TestCase):
    def setUp(self):
        self.owner=get_user_model().objects.create_superuser('speed-owner',password='Unrelated-test-password-729!')
        self.branch=Branch.objects.create(code='SPD',name='Speed shop')


class Dashboard(World):
    def test_span_totals_match_branch_comparison_for_each_span(self):
        shops=[self.branch];today=date.today()
        spans=[(today,today),(today.replace(day=1),today)]
        for (a,b),got in zip(spans,report_extra.span_totals(shops,spans)):
            row=report_extra.branch_comparison(shops,a,b)[0]
            self.assertEqual((got['sales'],got['purchases'],got['expenses'],got['profit']),(row['Net sales'],row['Purchases'],row['Expenses'],row['Net profit']))
    def test_trend_asks_the_database_a_constant_number_of_questions(self):
        with CaptureQueriesContext(connection) as q:rows=dm.trend([self.branch],6,date.today())
        self.assertEqual(len(rows),6);self.assertLessEqual(len(q),8)
    def test_dashboard_page_stays_cheap(self):
        c=Client();c.force_login(self.owner)
        with CaptureQueriesContext(connection) as q:r=c.get('/desk/mobile-shop')
        self.assertEqual(r.status_code,200);self.assertLess(len(q),60)


class ExpiredForm(World):
    def test_stale_form_returns_to_login_with_a_message_not_a_bare_403(self):
        c=Client(enforce_csrf_checks=True)
        r=c.post('/login/',{'username':'speed-owner','password':'x'},follow=True)
        self.assertEqual(r.status_code,200);self.assertEqual(r.redirect_chain[-1][0],'/login/')
        self.assertContains(r,'could not be saved')
    def test_stale_form_when_signed_in_goes_back_without_changing_anything(self):
        c=Client(enforce_csrf_checks=True);c.force_login(self.owner)
        r=c.post('/logout/',{},HTTP_REFERER='http://testserver/desk/mobile-shop')
        self.assertEqual(r.status_code,302);self.assertEqual(r['Location'],'http://testserver/desk/mobile-shop')
    def test_pages_are_not_replayed_from_the_browser_cache(self):
        c=Client();c.force_login(self.owner);r=c.get('/desk/mobile-shop');self.assertIn('no-store',r['Cache-Control'])
