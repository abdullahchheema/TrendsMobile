import json
from django.core.management.base import BaseCommand,CommandError
from django.contrib.auth import get_user_model
from django.db.models import Sum
from shop.models import *
from shop.services import money
from shop.reports import run
from shop.access import REPORTS
class Command(BaseCommand):
    help='Read-only verification of the imported baseline before any new trading.'
    def handle(self,*args,**kwargs):
        imported=ImportRun.objects.first()
        if not imported:raise CommandError('No migration has been imported.')
        expected=imported.summary
        actual=dict(devices=Device.objects.count(),imeis=IMEI.objects.count(),stock_qty=StockMovement.objects.aggregate(v=Sum('quantity'))['v'],
            stock_value=StockMovement.objects.aggregate(v=Sum('value'))['v'],gl_debit=LedgerEntry.objects.aggregate(v=Sum('debit'))['v'],gl_credit=LedgerEntry.objects.aggregate(v=Sum('credit'))['v'],
            customer_invoice_outstanding=Document.objects.filter(kind='Sales Invoice',status='Submitted').aggregate(v=Sum('outstanding'))['v'],
            supplier_invoice_outstanding=Document.objects.filter(kind='Purchase Invoice',status='Submitted').aggregate(v=Sum('outstanding'))['v'])
        for key,value in expected.items():
            if money(value)!=money(actual[key]):raise CommandError('Baseline changed or reconciliation failed: '+key)
        owner=get_user_model().objects.filter(is_superuser=True).first()
        if not owner:raise CommandError('Create the local owner before checking report access.')
        reports={}
        for name in REPORTS:
            columns,rows=run(name,owner,{})
            if not rows:raise CommandError('Report is empty: '+name)
            reports[name]={'rows':len(rows),'columns':len(columns)}
        _,profit=run('Device Profit',owner,{})
        _,staff=run('Staff Summary',owner,{})
        result=dict(reconciled=True,source_sha256=imported.fingerprint,actual=actual,reports=reports,
            device_margin=sum(row['Margin'] for row in profit),staff_totals={key:sum(row[key] for row in staff) for key in ('Net Device Purchases','Net Sales','Money Collected','Money Paid / Refunded')})
        self.stdout.write(json.dumps(result,indent=2,default=str))
