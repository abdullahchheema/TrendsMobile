"""Read-only configuration and data preflight; does not certify physical acceptance."""
import json
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Sum,Q
from django.utils import timezone
from shop.models import Branch,Document,Device,IMEI,LedgerEntry,StockMovement,SalesTaxRule
from shop.setup import readiness
from shop.services import valid_imei


class Command(BaseCommand):
    help='Inspect the actual four-shop configuration and retail data without changing it.'
    def handle(self,*args,**options):
        shops=[]
        for branch in Branch.objects.filter(enabled=True).order_by('code'):
            row=readiness(branch)
            shops.append(dict(id=str(branch.pk),code=branch.code,name=branch.name,ready=row['ready'],issues=row['issues'],warehouses=row['warehouses'],tills=row['tills'],staff=row['staff_count'],linked_logins=row['linked_logins'],payment_methods=row['methods']))
        balances=list(LedgerEntry.objects.values('branch__code').annotate(debit=Sum('debit'),credit=Sum('credit')))
        unbalanced=[row['branch__code'] for row in balances if row['debit']!=row['credit']]
        negative=StockMovement.objects.values('warehouse_id','product_id').annotate(quantity=Sum('quantity'),value=Sum('value')).filter(Q(quantity__lt=0)|Q(value__lt=0)).count()
        bad_imeis=sum(not valid_imei(number) for number in IMEI.objects.values_list('number',flat=True))
        missing=Device.objects.filter(status='Available').exclude(imeis__slot=1).count()
        imported_legacy=Document.objects.filter(kind='POS Opening Entry',status='Submitted',state='Open',till__isnull=True).count()
        sample_docs=Document.objects.filter(status='Submitted').exclude(demo_batch='').count()
        issues=[]
        if not settings.SUPABASE_AUTH_ENABLED:issues.append('The selected deployment requires Supabase Auth; this instance is using the standalone development login.')
        if len(shops)!=4:issues.append('Configure the actual four active shops; confirm the disposition of the imported shop.')
        if any(not row['ready'] for row in shops):issues.append('Resolve each shop’s account, staff-login, warehouse and till setup issues.')
        if unbalanced:issues.append('Review the unbalanced shop ledgers.')
        if negative or bad_imeis or missing:issues.append('Review stock quantities and device IMEI exceptions.')
        if imported_legacy:issues.append('Review and close imported legacy POS shifts with actual evidence.')
        if sample_docs:issues.append('Confirm how demonstration data will be separated from live trading; do not delete posted history.')
        result=dict(generated_at_utc=timezone.now().isoformat(),read_only=True,planned_shops=4,active_shops=len(shops),shops=shops,
            supabase_auth_enabled=settings.SUPABASE_AUTH_ENABLED,
            data_checks=dict(unbalanced_shop_ledgers=unbalanced,negative_stock_groups=negative,invalid_format_imeis=bad_imeis,available_phones_missing_primary_imei=missing,open_legacy_shifts=imported_legacy,submitted_demo_documents=sample_docs),
            configured_sales_tax_rules=SalesTaxRule.objects.count(),manual_journals=Document.objects.filter(data__manual_journal__version=1).count(),
            configuration_issues=issues,automated_configuration_ready=not issues,
            client_and_onsite_acceptance='Not assessed by this command',
            required_external_evidence=['Client-approved tax treatment (including explicit no-tax decision if appropriate)','Actual shop/staff configuration and demo-data disposition','Connection from every shop PC with internet disconnected and LAN connected','Physical scanner and printed receipt checks per shop','Independent backup copy and replacement-server recovery/reboot','Client acceptance of the purchase/sale/return/exchange/payment/closing workflows','Fresh trading-paused export and reconciliation before the switch'])
        self.stdout.write(json.dumps(result,indent=2,ensure_ascii=False))
