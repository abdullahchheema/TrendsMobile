"""QA audit, phase 12: pages must not ask the database one question per row. Measured on a staging dataset
(qa-audit/PERFORMANCE_AUDIT.md); the regressions fixed there are pinned here by counting SQL statements."""
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from .models import *
from .tests_audit_base import AuditMixin


class QueryCounts(AuditMixin,TestCase):
    def setUp(self):self.build()
    def count(self,client,url):
        client.get(url)                                    # warm anything cached per process
        with CaptureQueriesContext(connection) as captured:self.assertEqual(client.get(url).status_code,200)
        return len(captured)
    def test_the_customer_and_supplier_lists_do_not_grow_with_the_number_of_rows(self):
        client=self.as_user(self.login_a)
        for kind in ('Customer','Supplier'):
            Party.objects.bulk_create([Party(code=f'{kind}-{i}',kind=kind,name=f'{kind} {i:03d}',branch=self.a) for i in range(5)])
            few=self.count(client,reverse('collection',args=[kind]))
            Party.objects.bulk_create([Party(code=f'{kind}-more-{i}',kind=kind,name=f'{kind} more {i:03d}',branch=self.a) for i in range(40)])
            many=self.count(client,reverse('collection',args=[kind]))
            self.assertEqual(few,many,f'{kind} list: {few} statements for 5 rows, {many} for 45')
            self.assertLess(many,20)
    def test_the_device_profit_report_does_not_grow_with_the_number_of_sales(self):
        self.capital(self.a,'5000000');owner=self.as_user(self.owner);url=reverse('report',args=['Device Profit'])+f'?branch={self.a.pk}&from_date={self.today}&to_date={self.today}'
        def sell(n):
            for _ in range(n):
                phone,_=self.buy_phone(self.a,'50000','60000',pay='full');self.assertEqual(self.pos(self.login_a,self.a,[(phone,1,'60000')],[dict(mode='Cash',amount='60000.00')]).status_code,200)
        sell(2);few=self.count(owner,url);sell(6);many=self.count(owner,url)
        self.assertEqual(few,many,f'Device Profit: {few} statements for 2 sales, {many} for 8')
