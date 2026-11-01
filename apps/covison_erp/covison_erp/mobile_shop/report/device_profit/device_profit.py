import frappe
from frappe.utils import flt
from covison_erp.devices import is_manager


def execute(filters=None):
    if not is_manager():
        frappe.throw("Only shop managers can view device profit.", frappe.PermissionError)
    filters = filters or {}
    query = {"docstatus": 1}
    if filters.get("from_date") and filters.get("to_date"):
        query["posting_date"] = ["between", [filters["from_date"], filters["to_date"]]]
    parents = frappe.get_list("Sales Invoice", filters=query, fields=["name", "posting_date", "company"], limit_page_length=0)
    data = []
    for parent in parents:
        rows = frappe.get_all("Sales Invoice Item", filters={"parent": parent.name, "covison_device": ["is", "set"]},
            fields=["name", "covison_device", "item_name", "base_net_amount", "delivery_note", "dn_detail"])
        for row in rows:
            cost_entries = frappe.get_all("Stock Ledger Entry", filters={"voucher_detail_no": row.dn_detail or row.name,
                "is_cancelled": 0}, fields=["stock_value_difference"])
            cost = -sum(flt(entry.stock_value_difference) for entry in cost_entries)
            revenue = flt(row.base_net_amount)
            data.append({"name": row.covison_device, "device_name": row.item_name, "sales_invoice": parent.name,
                         "sold_on": parent.posting_date, "currency": frappe.get_cached_value("Company", parent.company, "default_currency"), "purchase_cost": cost,
                         "sold_price": revenue, "device_margin": revenue - cost})
    columns = [
        {"fieldname": "name", "label": "Device", "fieldtype": "Link", "options": "Mobile Device", "width": 160},
        {"fieldname": "device_name", "label": "Phone", "width": 250},
        {"fieldname": "sales_invoice", "label": "Sales Invoice", "fieldtype": "Link", "options": "Sales Invoice", "width": 180},
        {"fieldname": "sold_on", "label": "Sold On", "fieldtype": "Date", "width": 110},
        {"fieldname": "purchase_cost", "label": "Posted Device Cost", "fieldtype": "Currency", "options": "currency", "width": 140},
        {"fieldname": "sold_price", "label": "Net Selling Price", "fieldtype": "Currency", "options": "currency", "width": 150},
        {"fieldname": "device_margin", "label": "Margin Before Overheads", "fieldtype": "Currency", "options": "currency", "width": 190},
    ]
    return columns, data
