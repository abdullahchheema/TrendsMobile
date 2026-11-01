"""Preflight must flag incomplete deployments without altering business data."""
import io,json
from django.core.management import call_command
from django.test import TestCase
from .models import Document,Audit,StockMovement
from .tests import fixtures
from . import services as s


class DeploymentReadiness(TestCase):
    def setUp(self):self.__dict__.update(fixtures())
    def report(self):
        output=io.StringIO();call_command('deployment_readiness',stdout=output);return json.loads(output.getvalue())
    def test_partial_configuration_is_not_certified_and_is_read_only(self):
        before=(list(Document.objects.values()),list(Audit.objects.values()))
        report=self.report()
        self.assertFalse(report['automated_configuration_ready']);self.assertEqual(report['active_shops'],1)
        self.assertIn('Not assessed',report['client_and_onsite_acceptance']);self.assertEqual(report['configured_sales_tax_rules'],0)
        self.assertEqual(before,(list(Document.objects.values()),list(Audit.objects.values())))
        self.assertTrue(any('four active shops' in issue for issue in report['configuration_issues']))
    def test_legacy_shifts_and_demo_history_are_explicit(self):
        s.new_doc('POS Opening Entry',self.branch,status='Submitted',state='Open',demo_batch='SYNTHETIC-ONLY')
        report=self.report()
        self.assertEqual(report['data_checks']['open_legacy_shifts'],1);self.assertEqual(report['data_checks']['submitted_demo_documents'],1)
        self.assertTrue(any('demonstration' in issue for issue in report['configuration_issues']))
