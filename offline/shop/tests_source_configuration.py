"""Fresh source evidence must never overwrite trading records or activate tax."""
from copy import deepcopy
import hashlib,io,json,tempfile,unittest
from pathlib import Path
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase
from .models import SourceRecord,ImportRun,Document,LedgerEntry,SalesTaxRule,Audit


class SourceConfiguration(TestCase):
    @classmethod
    def setUpTestData(cls):
        path=Path('/imports/current-crm.json')
        if not path.exists():raise unittest.SkipTest('Migration snapshot not mounted.')
        raw=path.read_bytes();cls.snapshot=json.loads(raw)
        call_command('import_frappe',str(path),sha256=hashlib.sha256(raw).hexdigest(),stdout=io.StringIO())
    def fresh(self):
        data=deepcopy(self.snapshot)
        for kind in ('Sales','Purchase'):
            data['records'][kind+' Taxes and Charges Template']=[{'name':'SOURCE-'+kind,'disabled':1,'taxes':[{'rate':5,'charge_type':'On Net Total'}]}]
        return data
    def apply(self,data=None,check=False,wrong_digest=False):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.json';path.write_text(json.dumps(data or self.fresh()))
            output=io.StringIO();digest='0'*64 if wrong_digest else hashlib.sha256(path.read_bytes()).hexdigest()
            call_command('preserve_source_configuration',str(path),sha256=digest,check_only=check,stdout=output)
            return json.loads(output.getvalue())
    def test_check_only_compares_every_source_row_without_writing(self):
        before=(SourceRecord.objects.count(),ImportRun.objects.count(),Audit.objects.count())
        result=self.apply(check=True)
        self.assertTrue(result['original_source_rows_identical']);self.assertEqual(result['additional_source_templates'],2)
        self.assertEqual(before,(SourceRecord.objects.count(),ImportRun.objects.count(),Audit.objects.count()))
    def test_preserves_templates_and_provenance_without_repricing_or_tax_activation(self):
        before=(list(Document.objects.values()),list(LedgerEntry.objects.values()),Audit.objects.count())
        result=self.apply();self.assertEqual(result['source_records_created'],2)
        self.assertEqual(SourceRecord.objects.get(doctype='Sales Taxes and Charges Template').payload['disabled'],1)
        self.assertEqual(SalesTaxRule.objects.count(),0);self.assertEqual(ImportRun.objects.count(),2)
        self.assertEqual(before,(list(Document.objects.values()),list(LedgerEntry.objects.values()),Audit.objects.count()))
        self.assertEqual(self.apply()['source_records_created'],0);self.assertEqual(ImportRun.objects.count(),2)
    def test_changed_source_business_row_blocks_import(self):
        data=self.fresh();data['records']['Mobile Device'][0]['asking_price']=123
        before=SourceRecord.objects.count()
        with self.assertRaises(CommandError):self.apply(data)
        self.assertEqual(SourceRecord.objects.count(),before);self.assertEqual(ImportRun.objects.count(),1)
    def test_bad_checksum_and_changed_control_totals_are_rejected(self):
        with self.assertRaises(CommandError):self.apply(wrong_digest=True)
        data=self.fresh();data['expected']['stock_value']+=1
        with self.assertRaises(CommandError):self.apply(data)
    def test_changed_existing_template_cannot_overwrite_source_history(self):
        self.apply();data=self.fresh();data['records']['Sales Taxes and Charges Template'][0]['disabled']=0
        with self.assertRaises(CommandError):self.apply(data)
        self.assertEqual(SourceRecord.objects.get(doctype='Sales Taxes and Charges Template').payload['disabled'],1)
    def test_company_timestamp_revision_is_preserved_without_overwriting_original(self):
        data=self.fresh();company=data['records']['Company'][0];old=SourceRecord.objects.get(doctype='Company').payload
        company['modified']='2026-10-31 12:00:00.000000'
        result=self.apply(data);self.assertTrue(result['business_source_fields_identical']);self.assertEqual(result['timestamp_only_source_revisions'],1)
        self.assertEqual(SourceRecord.objects.get(doctype='Company').payload,old)
        revision=SourceRecord.objects.get(doctype='Migration Source Revision');self.assertEqual(revision.payload['payload']['modified'],company['modified'])
        self.assertEqual(self.apply(data)['source_records_created'],0)
    def test_company_business_change_and_invoice_timestamp_change_still_rejected(self):
        data=self.fresh();data['records']['Company'][0]['default_currency']='USD'
        with self.assertRaises(CommandError):self.apply(data)
        data=self.fresh();data['records']['Sales Invoice'][0]['modified']='2026-10-31 12:00:00.000000'
        with self.assertRaises(CommandError):self.apply(data)
