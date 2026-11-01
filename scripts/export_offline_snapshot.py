"""Read-only Frappe export for a separate Django migration rehearsal.

Run explicitly using bench execute. Does not alter source business records.
"""
import json,hashlib,os
from pathlib import Path
import frappe
from frappe.utils import now_datetime,flt

DOCTYPES=['Company','Warehouse','Account','Mode of Payment','Mobile Shop Settings','Mobile Model','Item','Item Price',
    'Mobile Device','Mobile IMEI','Mobile Staff','Customer','Supplier','Contact','Address','Serial No',
    'Mobile Intake','Purchase Order','Purchase Receipt','Purchase Invoice','Sales Invoice','POS Invoice','Payment Entry',
    'Journal Entry','Stock Entry','Mobile Exchange','Warranty Claim','Mobile Daily Closing','POS Opening Entry','POS Closing Entry',
    'POS Profile','POS Settings','Sales Taxes and Charges Template','Purchase Taxes and Charges Template','Stock Ledger Entry','GL Entry','Bin']
# A single read-only database snapshot preserves configuration and trading rows together.
frappe.db.sql('ROLLBACK')
frappe.db.sql('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY')
frappe.db.sql('START TRANSACTION WITH CONSISTENT SNAPSHOT')
records={}
for dt in DOCTYPES:
    if frappe.get_meta(dt).issingle:records[dt]=[frappe.get_single(dt).as_dict()]
    elif dt in ('Stock Ledger Entry','GL Entry','Bin'):
        records[dt]=frappe.get_all(dt,fields=['*'],limit_page_length=0)
    else:records[dt]=[frappe.get_doc(dt,name).as_dict() for name in frappe.get_all(dt,pluck='name',limit_page_length=0)]
debit,credit=frappe.db.sql('select sum(debit),sum(credit) from `tabGL Entry` where is_cancelled=0')[0]
stock_qty,stock_value=frappe.db.sql('select sum(actual_qty),sum(stock_value) from `tabBin`')[0]
payload={'schema':1,'site':frappe.local.site,'exported_at':str(now_datetime()),'records':records,
    'expected':dict(devices=frappe.db.count('Mobile Device'),imeis=frappe.db.count('Mobile IMEI'),
        stock_qty=flt(stock_qty),stock_value=flt(stock_value),gl_debit=flt(debit),gl_credit=flt(credit),
        customer_invoice_outstanding=flt(frappe.db.sql('select sum(outstanding_amount) from `tabSales Invoice` where docstatus=1')[0][0]),
        supplier_invoice_outstanding=flt(frappe.db.sql('select sum(outstanding_amount) from `tabPurchase Invoice` where docstatus=1')[0][0]))}
frappe.db.sql('ROLLBACK')
path=Path(os.environ.get('COVISON_SNAPSHOT_PATH','/tmp/covison-offline-snapshot.json'))
path.write_text(json.dumps(payload,default=str));path.chmod(0o600)
print(json.dumps(dict(path=str(path),counts={dt:len(rows) for dt,rows in records.items()},sha256=hashlib.sha256(path.read_bytes()).hexdigest(),expected=payload['expected']),indent=2))
