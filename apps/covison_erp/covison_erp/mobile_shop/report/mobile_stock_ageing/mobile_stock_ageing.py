import frappe
from frappe.utils import cint, date_diff, nowdate
from covison_erp.api import device_rows
from covison_erp.devices import can_see_costs


def execute(filters=None):
    filters = filters or {}
    threshold = cint(frappe.db.get_single_value('Mobile Shop Settings', 'ageing_days')) or 30
    query = {'status': ['in', ['Available', 'Reserved']]}
    if filters.get('warehouse'): query['warehouse'] = filters['warehouse']
    rows = device_rows(query)
    for row in rows:
        # Acquisition dates are nonfinancial device metadata; cost remains role restricted.
        receipt = frappe.db.get_value('Mobile Device', row.name, 'purchase_receipt')
        row.received_on = frappe.db.get_value('Purchase Receipt', receipt, 'posting_date') if receipt else None
        row.days_in_stock = max(0, date_diff(nowdate(), row.received_on)) if row.received_on else None
        row.review = 'Set asking price' if not row.asking_price else 'Review price / slow stock' if row.days_in_stock is not None and row.days_in_stock >= threshold else 'Current'
    if cint(filters.get('review_only')):
        rows = [r for r in rows if r.review != 'Current']
    columns = [
        dict(fieldname='name',label='Device',fieldtype='Link',options='Mobile Device',width=170),
        dict(fieldname='device_name',label='Phone / Variant',width=240),
        dict(fieldname='warehouse',label='Shop / Warehouse',fieldtype='Link',options='Warehouse',width=170),
        dict(fieldname='received_on',label='Received',fieldtype='Date',width=110),
        dict(fieldname='days_in_stock',label='Days in Stock',fieldtype='Int',width=110),
        dict(fieldname='asking_price',label='Asking Price',fieldtype='Currency',options='currency',width=140),
        dict(fieldname='review',label='Action',width=210),
    ]
    if can_see_costs():
        columns += [dict(fieldname='purchase_cost',label='Valuation Cost',fieldtype='Currency',options='currency',width=140)]
    return columns, sorted(rows, key=lambda r: r.days_in_stock or 0, reverse=True)
