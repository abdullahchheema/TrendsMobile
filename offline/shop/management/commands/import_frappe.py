"""Import a signed-by-checksum local snapshot into an empty PostgreSQL database."""
import hashlib,json
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from django.db.models import Sum
from django.utils.dateparse import parse_datetime
from django.utils import timezone
from shop.models import *
from shop.services import money,ZERO

def text(value):return str(value or '')
def date(value):return text(value)[:10] or None
def timestamp(value):
    result=parse_datetime(text(value)) if value else None
    return timezone.make_aware(result,ZoneInfo('Asia/Karachi')) if result and timezone.is_naive(result) else result

class Command(BaseCommand):
    help='Rehearse migration from a read-only Frappe snapshot into an empty Django database.'
    def add_arguments(self,parser):parser.add_argument('snapshot');parser.add_argument('--sha256',required=True)
    @transaction.atomic
    def handle(self,*args,**options):
        raw=Path(options['snapshot']).read_bytes();digest=hashlib.sha256(raw).hexdigest()
        if digest!=options['sha256']:raise CommandError('Snapshot checksum does not match.')
        if ImportRun.objects.filter(fingerprint=digest).exists():self.stdout.write('This snapshot is already imported; no changes made.');return
        if any(model.objects.exists() for model in (Branch,Document,Device,Account,Product)):raise CommandError('Import requires an empty business database. Existing business data was preserved.')
        data=json.loads(raw)
        if data.get('schema')!=1:raise CommandError('Unsupported snapshot schema.')
        records=data['records'];lookup={};companies={};warehouse_map={};account_map={};parties={};models={};products={};staff={};documents={};devices={}
        def base(row,code=None):return dict(code=code or row['name'],source=row,demo_batch=text(row.get('covison_demo_batch')),created=timestamp(row.get('creation')) or timezone.now())
        for dt,rows in records.items():
            for row in rows:SourceRecord.objects.create(doctype=dt,name=row['name'],payload=row)
        for number,row in enumerate(records['Company'],1):
            if row.get('default_currency')!='PKR':raise CommandError('This importer requires PKR companies.')
            branch=Branch.objects.create(**base(row,code=f'PK-{number:03d}'),name=row['name'],company=row['name']);companies[row['name']]=branch
            Till.objects.create(code='TILL-'+str(branch.pk),name='Main till',branch=branch)
        for row in records['Warehouse']:
            if row.get('is_group') or not row.get('company'):continue
            warehouse_map[row['name']]=Warehouse.objects.create(**base(row),branch=companies[row['company']],name=row['name'],enabled=not row.get('disabled'))
        for row in records['Account']:
            account_map[row['name']]=Account.objects.create(**base(row),name=row['name'],account_type=text(row.get('account_type')),is_group=bool(row.get('is_group')),enabled=not row.get('disabled'))
        for dt in ('Customer','Supplier'):
            for row in records[dt]:
                addr=next((a for a in records['Address'] if any(link.get('link_doctype')==dt and link.get('link_name')==row['name'] for link in a.get('links',[]))),{})
                contact=next((c for c in records['Contact'] if any(link.get('link_doctype')==dt and link.get('link_name')==row['name'] for link in c.get('links',[]))),{})
                parties[(dt,row['name'])]=Party.objects.create(**base(row,code=dt.upper()+'-'+row['name']),kind=dt,name=row.get(dt.lower()+'_name') or row['name'],
                    city=text(addr.get('city')),address='\n'.join(text(addr.get(k)) for k in ('address_line1','address_line2','city','country') if addr.get(k)),
                    email=text(contact.get('email_id')),phone=text(contact.get('mobile_no') or contact.get('phone')),enabled=not row.get('disabled'))
        for row in records['Mobile Model']:models[row['name']]=PhoneModel.objects.create(**base(row),name=row.get('model_name') or row['name'],brand=text(row.get('brand')),description=text(row.get('description')))
        for row in records['Item']:
            price=next((p.get('price_list_rate') for p in records['Item Price'] if p.get('item_code')==row['name'] and p.get('currency')=='PKR' and 'Selling' in p.get('price_list','')),0)
            products[row['name']]=Product.objects.create(**base(row),name=row['item_name'],group=row.get('item_group') or 'Products',model=models.get(row.get('covison_model')),
                brand=text(row.get('brand')),storage=text(row.get('covison_storage')),ram=text(row.get('covison_ram')),colour=text(row.get('covison_color')),
                serialized=bool(row.get('covison_is_mobile') and not row.get('has_variants')),stock_item=bool(row.get('is_stock_item')),selling_price=money(price),
                barcodes=[b['barcode'] for b in row.get('barcodes',[])],enabled=not row.get('disabled'))
        for row in records['Mobile Staff']:
            default=warehouse_map.get(row.get('warehouse'))
            if default and default.branch!=companies[row['company']]:default=None
            staff[row['name']]=Staff.objects.create(**base(row),name=row['staff_name'],branch=companies[row['company']],default_warehouse=default,designation=text(row.get('designation')),
                enabled=bool(row.get('enabled')),manager=row.get('designation')=='Manager',can_buy=bool(row.get('can_buy')),can_sell=bool(row.get('can_sell')),can_handle_payments=bool(row.get('can_handle_payments')))
        dt_list=['Mobile Intake','Purchase Order','Purchase Receipt','Purchase Invoice','Sales Invoice','POS Invoice','Payment Entry','Journal Entry','Stock Entry','Mobile Exchange','Warranty Claim','Mobile Daily Closing','POS Opening Entry','POS Closing Entry']
        for dt in dt_list:
            for row in records[dt]:
                branch=companies.get(row.get('company')) or next(iter(companies.values()))
                state=text(row.get('settlement_status') if dt=='Mobile Exchange' else row.get('status'))
                extras={}
                if dt=='Payment Entry':extras=dict(amount=text(row.get('paid_amount')),direction=row.get('payment_type'),mode=row.get('mode_of_payment') or 'Cash',reference=text(row.get('reference_no')),unallocated=text(row.get('unallocated_amount')))
                elif dt in ('Sales Invoice','POS Invoice'):
                    extras['receipt']=dict(shop_phone=text(row.get('covison_shop_phone')),shop_address=text(row.get('covison_shop_address')),receipt_terms=text(row.get('covison_receipt_terms')),urdu_receipt_note=text(row.get('covison_urdu_receipt_note')))
                elif dt=='Purchase Receipt':extras['trade_in_customer']=str(parties[('Customer',row['covison_trade_in_customer'])].pk) if row.get('covison_trade_in_customer') else None
                elif dt=='Warranty Claim':extras['resolution']=text(row.get('covison_resolution'))
                elif dt=='Mobile Daily Closing':extras['balances']=[dict(account=str(account_map[b['account']].pk),name=b['account'],expected=text(b.get('expected_balance')),counted=text(b.get('counted_balance')),difference=text(b.get('difference')),notes=text(b.get('notes'))) for b in row.get('balances',[])]
                doc=Document.objects.create(**base(row),kind=dt,branch=branch,status={0:'Draft',1:'Submitted',2:'Cancelled'}[int(row.get('docstatus',0))],state=state,
                    posting_date=date(row.get('posting_date') or row.get('transaction_date') or row.get('closing_date') or row.get('period_start_date') or row.get('creation')),
                    customer=parties.get(('Customer',row.get('customer') or (row.get('party') if row.get('party_type')=='Customer' else None))),
                    supplier=parties.get(('Supplier',row.get('supplier') or (row.get('party') if row.get('party_type')=='Supplier' else None))),
                    warehouse=warehouse_map.get(row.get('warehouse') or row.get('set_warehouse')),buyer=staff.get(row.get('covison_purchased_by')),
                    seller=staff.get(row.get('covison_sold_by')),handler=staff.get(row.get('covison_handled_by') or row.get('covison_closed_by') or row.get('covison_collected_by')),
                    staff_names=dict(buyer=text(row.get('covison_purchaser_name')),seller=text(row.get('covison_seller_name')),handler=text(row.get('covison_handler_name') or row.get('covison_closer_name') or row.get('covison_collector_name'))),
                    total=money(row.get('grand_total') or row.get('total') or row.get('paid_amount') or row.get('purchase_price')),
                    outstanding=money(row.get('outstanding_amount')),currency=row.get('currency') or 'PKR',is_return=bool(row.get('is_return')),notes=text(row.get('remarks') or row.get('notes') or row.get('complaint')),data=extras)
                if dt=='Mobile Exchange' and state=='Settled':doc.status='Submitted';doc.save()
                documents[(dt,row['name'])]=doc
        for row in records['Mobile Device']:
            source_receipt=documents.get(('Purchase Receipt',row.get('purchase_receipt')))
            details={k:v for k,v in row.items() if k not in ('purchase_cost','asking_price','minimum_price','wholesale_price')}
            devices[row['name']]=Device.objects.create(**base(row),branch=companies[row['company']],product=products[row['item_code']],warehouse=warehouse_map.get(row.get('warehouse')),
                status=row.get('status') or 'Unavailable',device_type=row.get('device_type') or 'Used',condition=row.get('condition_grade') or 'Not Inspected',
                inspection_status=row.get('inspection_status') or 'Pending',inspection_date=date(row.get('inspection_date')),battery_checked=bool(row.get('battery_health_checked')),battery=int(row.get('battery_health') or 0),
                purchase_cost=money(row.get('purchase_cost')),asking_price=money(row.get('asking_price')),minimum_price=money(row.get('minimum_price')),wholesale_price=money(row.get('wholesale_price')),
                warranty_days=int(row.get('warranty_days') or 0),warranty_terms=text(row.get('warranty_terms')),faults=text(row.get('disclosed_faults')),details=details,
                received_on=source_receipt.posting_date if source_receipt else date(row.get('creation')),sold_on=date(row.get('sold_on')),sold_price=money(row.get('sold_price')),
                customer=parties.get(('Customer',row.get('customer'))),sale=documents.get(('Sales Invoice',row.get('sales_invoice'))) or documents.get(('POS Invoice',row.get('pos_invoice'))))
            for slot in (1,2):
                number=row.get('imei_'+str(slot))
                if not number:continue
                suffix='' if slot==1 else '_2'
                obj=IMEI(number=number,device=devices[row['name']],slot=slot,pta_status=row.get('pta_status'+suffix) or 'Not Checked',verified_on=date(row.get('pta_verified_on'+suffix)),verified_by=text(row.get('pta_verified_by')))
                obj.full_clean();obj.save()
                if row.get('pta_evidence'+suffix):raise CommandError('PTA evidence attachments need a reviewed file migration before import.')
        for (dt,name),doc in documents.items():
            row=doc.source
            if row.get('return_against'):doc.reference=documents.get((dt,row['return_against']))
            elif dt=='Purchase Invoice':
                receipt_names={r.get('purchase_receipt') for r in row.get('items',[]) if r.get('purchase_receipt')}
                if len(receipt_names)==1:doc.reference=documents.get(('Purchase Receipt',next(iter(receipt_names))))
            elif dt=='Purchase Receipt':
                po_names={r.get('purchase_order') for r in row.get('items',[]) if r.get('purchase_order')}
                if len(po_names)==1:doc.reference=documents.get(('Purchase Order',next(iter(po_names))))
            elif dt=='Warranty Claim':
                doc.reference=documents.get(('Sales Invoice',row.get('covison_sales_invoice')))
                if row.get('covison_device') in devices:doc.data['device']=str(devices[row['covison_device']].pk)
            elif dt=='POS Closing Entry':doc.reference=documents.get(('POS Opening Entry',row.get('pos_opening_entry')))
            elif dt=='Payment Entry' and len(row.get('references',[]))==1:
                ref=row['references'][0];doc.reference=documents.get((ref.get('reference_doctype'),ref.get('reference_name')))
            elif dt=='Mobile Exchange':
                for key,target in [('sales_invoice','Sales Invoice'),('purchase_invoice','Purchase Invoice')]:
                    if row.get(key):doc.data[key]=str(documents[(target,row[key])].pk)
            elif dt=='Mobile Intake':
                # Preserve editable intake data rather than treating it as already received.
                doc.data.update(purchase_source=row.get('purchase_source'),trade_in_customer=str(parties[('Customer',row['trade_in_customer'])].pk) if row.get('trade_in_customer') else None)
                if row.get('purchase_receipt'):doc.data['purchase_receipt']=str(documents[('Purchase Receipt',row['purchase_receipt'])].pk)
                if row.get('mobile_device'):doc.data['device']=str(devices[row['mobile_device']].pk)
                else:doc.data['device']=dict(device_type=row.get('device_type'),condition=row.get('condition_grade'),inspection_status=row.get('inspection_status'),inspection_date=date(row.get('inspection_date')),
                    asking_price=text(row.get('asking_price')),minimum_price=text(row.get('minimum_price')),wholesale_price=text(row.get('wholesale_price')),warranty_days=row.get('warranty_days'),warranty_terms=text(row.get('warranty_terms')),
                    battery_checked=bool(row.get('battery_health_checked')),battery=row.get('battery_health') or 0,details={'inspection_notes':text(row.get('inspection_notes'))},imeis=[{'number':text(row.get('imei_1'))},{'number':text(row.get('imei_2'))}])
            doc.save()
            source_lines=row.get('items',[])
            if dt=='Mobile Intake':source_lines=[dict(item_code=row['item_code'],qty=1,rate=row.get('purchase_price'),warehouse=row.get('warehouse'))]
            for item in source_lines:
                if item.get('item_code') not in products:continue
                device=devices.get(item.get('covison_device') or text(item.get('serial_no')).strip())
                cost=money(device.purchase_cost) if device else ZERO
                matching=[s for s in records['Stock Ledger Entry'] if not s.get('is_cancelled') and s.get('voucher_type')==dt and s.get('voucher_no')==name and s.get('item_code')==item['item_code'] and Decimal(text(s.get('actual_qty')) or '0')!=0]
                if matching:
                    s=matching[0];cost=abs(money(money(s.get('stock_value_difference'))/Decimal(text(s.get('actual_qty')))))
                snap=dict(imei_1=text(item.get('covison_imei')),imei_2=text(item.get('covison_imei_2')),condition=text(item.get('covison_condition')),battery=item.get('covison_battery'),
                    faults=text(item.get('covison_disclosed_faults')),warranty_days=item.get('covison_warranty_days') or 0,warranty_terms=text(item.get('covison_warranty_terms')),
                    pta=[dict(slot=1,status=item.get('covison_pta_status') or 'Not Checked'),dict(slot=2,status=item.get('covison_pta_status_2') or 'Not Checked')])
                Line.objects.create(document=doc,product=products[item['item_code']],device=device,warehouse=warehouse_map.get(item.get('warehouse')),
                    quantity=abs(Decimal(text(item.get('qty')) or '1')),rate=abs(money(item.get('base_net_rate') if item.get('base_net_rate') is not None else item.get('rate'))),cost=cost,snapshot=snap)
        for row in records['GL Entry']:
            if row.get('is_cancelled'):continue
            doc=documents.get((row['voucher_type'],row['voucher_no']))
            if not doc:raise CommandError('Unmapped ledger voucher: '+row['voucher_type']+' '+row['voucher_no'])
            LedgerEntry.objects.create(document=doc,branch=companies[row['company']],posting_date=date(row['posting_date']),account=account_map[row['account']],
                party=parties.get((row.get('party_type'),row.get('party'))),reference=documents.get((row.get('against_voucher_type'),row.get('against_voucher'))),debit=money(row.get('debit')),credit=money(row.get('credit')))
        for row in records['Stock Ledger Entry']:
            if row.get('is_cancelled'):continue
            doc=documents.get((row['voucher_type'],row['voucher_no']))
            if not doc:raise CommandError('Unmapped stock voucher.')
            StockMovement.objects.create(document=doc,posting_date=date(row['posting_date']),product=products[row['item_code']],warehouse=warehouse_map[row['warehouse']],
                quantity=Decimal(text(row.get('actual_qty')) or '0'),value=money(row.get('stock_value_difference')))
        original_settings=records['Mobile Shop Settings'][0]
        for company in records['Company']:
            def acct(field,fallback=None):
                name=company.get(field) or fallback
                if name not in account_map:raise CommandError('Missing account mapping for '+field)
                return str(account_map[name].pk)
            accounts=dict(receivable=acct('default_receivable_account'),payable=acct('default_payable_account'),inventory=acct('default_inventory_account'),
                stock_received=acct('stock_received_but_not_billed'),sales=acct('default_income_account'),cogs=acct('default_expense_account'),expense=acct('default_expense_account'))
            methods={}
            for mode in records['Mode of Payment']:
                if not mode.get('enabled'):continue
                name=next((r.get('default_account') for r in mode.get('accounts',[]) if r.get('company')==company['name']),None)
                if name in account_map:methods[mode['name']]=str(account_map[name].pk)
            ShopSettings.objects.create(branch=companies[company['name']],accounts=accounts,payment_methods=methods,
                shop_phone=text(original_settings.get('shop_phone')),shop_address=text(original_settings.get('shop_address')),receipt_terms=text(original_settings.get('receipt_terms')),
                urdu_receipt_note=text(original_settings.get('urdu_receipt_note')),ageing_days=original_settings.get('ageing_days') or 30)
        summary=dict(devices=Device.objects.count(),imeis=IMEI.objects.count(),stock_qty=StockMovement.objects.aggregate(v=Sum('quantity'))['v'] or ZERO,
            stock_value=StockMovement.objects.aggregate(v=Sum('value'))['v'] or ZERO,gl_debit=LedgerEntry.objects.aggregate(v=Sum('debit'))['v'] or ZERO,gl_credit=LedgerEntry.objects.aggregate(v=Sum('credit'))['v'] or ZERO,
            customer_invoice_outstanding=Document.objects.filter(kind='Sales Invoice',status='Submitted').aggregate(v=Sum('outstanding'))['v'] or ZERO,
            supplier_invoice_outstanding=Document.objects.filter(kind='Purchase Invoice',status='Submitted').aggregate(v=Sum('outstanding'))['v'] or ZERO)
        for key,value in data['expected'].items():
            if abs(Decimal(str(summary[key]))-Decimal(str(value)))>Decimal('0.01'):raise CommandError(f'Reconciliation failed for {key}: {summary[key]} != {value}')
        for row in records['Bin']:
            actual=StockMovement.objects.filter(product=products[row['item_code']],warehouse=warehouse_map[row['warehouse']]).aggregate(q=Sum('quantity'),v=Sum('value'))
            if abs(Decimal(str(actual['q'] or 0))-Decimal(str(row['actual_qty'])))>Decimal('0.001') or abs(money(actual['v'])-money(row['stock_value']))>Decimal('0.01'):
                raise CommandError('Warehouse-level stock reconciliation failed for '+row['item_code'])
        ImportRun.objects.create(fingerprint=digest,summary=json.loads(json.dumps(summary,default=str)))
        self.stdout.write(json.dumps(dict(imported=True,sha256=digest,summary=summary),indent=2,default=str))
