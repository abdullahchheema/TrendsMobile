"""Visible provenance for explicitly requested demonstration records.

Installing/migrating only adds metadata. Sample transactions are created only by
scripts/seed_mobile_demo_dataset.py, never by an application hook.
"""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

DEMO_DOCTYPES=('Mobile Device','Mobile Intake','Purchase Order','Purchase Receipt','Purchase Invoice',
    'Sales Invoice','POS Invoice','Payment Entry','Journal Entry','Mobile Exchange','Mobile Daily Closing',
    'POS Profile','POS Opening Entry','POS Closing Entry','Customer','Supplier','Warranty Claim','Item Price',
    'Item','Stock Entry','Delivery Note','Contact','Address','Mobile Staff','Mobile Model','Mobile Shop Settings')


def setup():
    create_custom_fields({dt:[dict(fieldname='covison_demo_batch',label='Demo Data Batch',fieldtype='Data',
        read_only=1,no_copy=1,in_standard_filter=1,depends_on='eval:doc.covison_demo_batch',
        description='Synthetic demonstration data; not evidence of actual devices, payments or PTA verification.')]
        for dt in DEMO_DOCTYPES},update=True)


def mark_transaction(doc, method=None):
    """Keep demo labels on documents generated while testing linked sample data."""
    if not doc.meta.has_field('covison_demo_batch') or doc.get('covison_demo_batch'):
        return
    batches=set()
    def collect(doctype,name):
        if name and frappe.get_meta(doctype).has_field('covison_demo_batch'):
            value=frappe.db.get_value(doctype,name,'covison_demo_batch')
            if value:batches.add(value)
    if doc.get('return_against'):collect(doc.doctype,doc.return_against)
    for field,doctype in (('customer','Customer'),('supplier','Supplier'),('covison_device','Mobile Device'),
        ('purchase_receipt','Purchase Receipt'),('sales_invoice','Sales Invoice'),('covison_sales_invoice','Sales Invoice'),
        ('pos_profile','POS Profile')):
        collect(doctype,doc.get(field))
    if doc.doctype=='Payment Entry' and doc.get('party_type') in ('Customer','Supplier'):
        collect(doc.party_type,doc.party)
    for row in doc.get('items') or []:
        for field,doctype in (('covison_device','Mobile Device'),('item_code','Item'),('purchase_receipt','Purchase Receipt'),
                             ('purchase_order','Purchase Order'),('sales_invoice','Sales Invoice'),('pos_invoice','POS Invoice')):
            collect(doctype,row.get(field))
        serials=[s.strip() for s in (row.get('serial_no') or '').splitlines() if s.strip()]
        if row.get('serial_and_batch_bundle'):
            serials+=frappe.get_all('Serial and Batch Entry',filters={'parent':row.serial_and_batch_bundle},pluck='serial_no')
        for serial in serials:collect('Mobile Device',serial)
    for row in doc.get('references') or []:
        if row.get('reference_doctype') in ('Sales Invoice','Purchase Invoice','Purchase Order'):
            collect(row.reference_doctype,row.reference_name)
    for row in doc.get('accounts') or []:
        if row.get('reference_type') in ('Sales Invoice','Purchase Invoice'):
            collect(row.reference_type,row.reference_name)
    for field in ('pos_transactions','sales_invoices'):
        for row in doc.get(field) or []:
            collect('POS Invoice',row.get('pos_invoice'))
            collect('Sales Invoice',row.get('sales_invoice'))
    if batches:doc.covison_demo_batch=' | '.join(sorted(batches))[:140]
