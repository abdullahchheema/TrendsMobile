"""Phase B: accounting period lock."""
from datetime import timedelta
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import services as s,easy_retail


class PeriodLock(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.today=timezone.localdate();self.yesterday=self.today-timedelta(days=1)
    def purchase(self,on,number):
        data=dict(branch=self.branch,party_name='Period Supplier',product=self.product,quantity=1,price=40000,asking_price=60000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,
                  staff=self.buyer,handler=self.handler,posting_date=on,payment_status='unpaid',method='Cash',imeis=[[imei(number)]])
        return easy_retail.purchase(data,self.user)
    def lock(self,through):Branch.objects.filter(pk=self.branch.pk).update(lock_through=through);self.branch.refresh_from_db()
    def test_posting_into_a_locked_period_is_refused_and_later_dates_still_work(self):
        self.lock(self.yesterday)
        with self.assertRaisesMessage(ValidationError,'is locked'):self.purchase(self.yesterday,71)
        self.assertEqual(Device.objects.count(),0)
        self.assertEqual(self.purchase(self.today,72).status,'Submitted')
    def test_a_posted_document_in_a_period_that_is_later_locked_cannot_be_cancelled(self):
        bill=self.purchase(self.yesterday,73);self.lock(self.yesterday)
        with self.assertRaisesMessage(ValidationError,'is locked'):s.cancel(bill,self.user)
    def test_lock_date_page_audits_and_accountants_cannot_reopen(self):
        accountant=get_user_model().objects.create_user('acct-period',password='Unrelated-test-password-729!')
        Staff.objects.create(code='ACCT-P',name='Accountant P',branch=self.branch,user=accountant,role=Role.objects.get(code='accountant'))
        self.client.force_login(accountant)
        self.client.post(reverse('periods'),dict(branch=str(self.branch.pk),lock_through=str(self.yesterday)))
        self.branch.refresh_from_db();self.assertEqual(self.branch.lock_through,self.yesterday)
        self.assertTrue(Audit.objects.filter(action='Lock period',branch=self.branch).exists())
        self.assertEqual(self.client.post(reverse('periods'),dict(branch=str(self.branch.pk),lock_through=str(self.yesterday-timedelta(days=5)),reason='trying to reopen')).status_code,403)
    def test_owner_reopening_needs_a_reason_and_future_dates_are_rejected(self):
        self.lock(self.yesterday);self.client.force_login(self.user)
        self.client.post(reverse('periods'),dict(branch=str(self.branch.pk),lock_through=str(self.yesterday-timedelta(days=3)),reason=''))
        self.branch.refresh_from_db();self.assertEqual(self.branch.lock_through,self.yesterday)
        self.client.post(reverse('periods'),dict(branch=str(self.branch.pk),lock_through=str(self.yesterday-timedelta(days=3)),reason='Late supplier bill found'))
        self.branch.refresh_from_db();self.assertEqual(self.branch.lock_through,self.yesterday-timedelta(days=3))
        entry=Audit.objects.get(action='Reopen period');self.assertEqual(entry.details['reason'],'Late supplier bill found')
        self.client.post(reverse('periods'),dict(branch=str(self.branch.pk),lock_through=str(self.today+timedelta(days=2))))
        self.branch.refresh_from_db();self.assertEqual(self.branch.lock_through,self.yesterday-timedelta(days=3))
    def test_sales_staff_cannot_see_the_page(self):
        clerk=get_user_model().objects.create_user('clerk-period',password='Unrelated-test-password-729!')
        Staff.objects.create(code='CLERK-P',name='Clerk P',branch=self.branch,user=clerk,role=Role.objects.get(code='sales'))
        self.client.force_login(clerk);self.assertEqual(self.client.get(reverse('periods')).status_code,403)
