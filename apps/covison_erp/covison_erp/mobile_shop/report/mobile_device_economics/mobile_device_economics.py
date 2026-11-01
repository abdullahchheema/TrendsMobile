"""Lifetime posted revenue/costs by device, with expensed repairs counted once."""
import frappe
from frappe.utils import flt
from covison_erp.api import device_rows
from covison_erp.devices import is_manager
from covison_erp.mobile_shop.report.device_profit.device_profit import execute as trading_profit


def execute(filters=None):
    if not is_manager():
        frappe.throw('Only managers can view device economics.', frappe.PermissionError)
    filters = filters or {}
    query = {'warehouse': filters['warehouse']} if filters.get('warehouse') else {}
    rows = device_rows(query)
    lookup = {r.name: r for r in rows}
    for row in rows:
        row.repair_expenses = 0; row.net_sales = 0; row.posted_stock_cost = 0; row.posted_margin = 0
    _, trades = trading_profit()
    for trade in trades:
        row = lookup.get(trade['name'])
        if row:
            row.net_sales += flt(trade['sold_price'])
            row.posted_stock_cost += flt(trade['purchase_cost'])
    invoices = frappe.get_list('Purchase Invoice', filters={'docstatus': 1}, pluck='name', limit_page_length=0)
    if invoices:
        for expense in frappe.get_all('Purchase Invoice Item', filters={'parent': ['in', invoices],
              'covison_device': ['in', list(lookup) or ['']]}, fields=['covison_device', 'base_net_amount']):
            lookup[expense.covison_device].repair_expenses += flt(expense.base_net_amount)
    for row in rows:
        row.total_device_cost = flt(row.purchase_cost) + row.repair_expenses
        row.posted_margin = row.net_sales - row.posted_stock_cost - row.repair_expenses
    columns = [dict(fieldname='name',label='Device',fieldtype='Link',options='Mobile Device',width=170),
               dict(fieldname='device_name',label='Phone',width=220),dict(fieldname='status',label='Status',width=110)]
    for field, label in [('purchase_cost','Current / Last Valuation'),('repair_expenses','Expensed Repairs (Net)'),
                         ('total_device_cost','Valuation + Repairs'),('net_sales','Lifetime Net Sales'),
                         ('posted_stock_cost','Net Posted Stock Cost'),('posted_margin','Posted Margin After Repairs')]:
        columns.append(dict(fieldname=field,label=label,fieldtype='Currency',options='currency',width=190))
    return columns, rows, 'Lifetime posted sales and returns, less stock cost and device service expenses. Unsold-device repair expenses appear as costs already incurred. Excludes unallocated overhead and taxes; capitalized costs stay in valuation.'
