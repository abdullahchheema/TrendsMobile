"""Permission-aware mobile-shop endpoints. Native ERP documents post the ledgers."""

import frappe
from frappe.utils import flt, nowdate
from covison_erp.devices import can_see_costs, is_manager


def device_rows(filters=None):
    if not frappe.has_permission("Mobile Device", "read"):
        frappe.throw("You do not have access to phone inventory.", frappe.PermissionError)
    fields = ["name", "device_name", "serial_no", "item_code", "model", "storage", "color", "imei_1", "imei_2",
              "company", "warehouse", "status", "condition_grade", "battery_health", "battery_health_checked", "pta_status",
              "inspection_status", "asking_price", "wholesale_price", "currency", "sales_invoice", "sold_price",
              "sold_on", "customer", "warranty_days"]
    if frappe.get_meta("Mobile Device").has_field("covison_demo_batch"):
        fields.append("covison_demo_batch")
    if can_see_costs():
        fields += ["purchase_cost", "minimum_price", "supplier", "purchase_receipt"]
    rows = frappe.get_list("Mobile Device", fields=fields, filters=filters or {},
                            order_by="model asc, storage asc, name asc", limit_page_length=0)
    if can_see_costs() and rows:
        entries = frappe.get_all("Serial and Batch Entry", filters={"serial_no": ["in", [d.serial_no for d in rows]],
            "docstatus": 1, "is_cancelled": 0, "type_of_transaction": "Inward", "qty": [">", 0]},
            fields=["serial_no", "incoming_rate"], order_by="posting_datetime desc, creation desc")
        costs = {}
        for entry in entries:
            costs.setdefault(entry.serial_no, flt(entry.incoming_rate))
        for row in rows:
            if row.serial_no in costs:
                row.purchase_cost = costs[row.serial_no]
    return rows


@frappe.whitelist()
def dashboard(search=None, status="Available", model=None, pta_status=None, condition=None):
    filters = {}
    for field, value in (("status", status), ("model", model), ("pta_status", pta_status), ("condition_grade", condition)):
        if value and value != "All":
            filters[field] = value
    rows = device_rows(filters)
    if search:
        needle = str(search).strip().lower()
        rows = [d for d in rows if any(needle in str(d.get(k) or "").lower()
                for k in ("name", "device_name", "item_code", "model", "imei_1", "imei_2", "storage", "color"))]
    all_rows = device_rows()
    available = [d for d in all_rows if d.status == "Available"]
    sold_today = [d for d in all_rows if d.status in ("Sold", "Reserved") and str(d.sold_on) == nowdate()]
    summary = {"available": len(available), "pending_inspection": sum(d.inspection_status != "Passed" for d in available),
               "missing_imei": sum(not d.imei_1 for d in available), "sold_today": len(sold_today),
               "sales_today": sum(flt(d.sold_price) for d in sold_today)}
    if can_see_costs():
        summary["stock_value"] = sum(flt(d.purchase_cost) for d in all_rows if d.status in ("Available", "Reserved"))
    return {"devices": rows, "summary": summary, "models": sorted({d.model for d in all_rows if d.model}),
            "demo_data": any(d.get("covison_demo_batch") for d in all_rows),
            "can_see_costs": can_see_costs(), "currency": "PKR", "today": nowdate()}


@frappe.whitelist(methods=["POST"])
def create_sale(device, customer, rate, sold_by=None):
    if not frappe.has_permission("Sales Invoice", "create"):
        frappe.throw("You do not have permission to create sales invoices.", frappe.PermissionError)
    phone = frappe.get_doc("Mobile Device", device)
    phone.check_permission("read")
    if frappe.db.get_value("Serial No", phone.serial_no, "warehouse") != phone.warehouse or not phone.warehouse:
        frappe.throw("This phone is no longer in stock.")
    if not phone.imei_1:
        frappe.throw("Record the actual IMEI before selling this phone.")
    if flt(rate) <= 0:
        frappe.throw("Enter a positive selling price.")
    if flt(rate) < flt(phone.minimum_price) and not is_manager():
        frappe.throw("A manager must approve a price below the minimum.")
    frappe.get_doc("Customer", customer).check_permission("read")
    doc = frappe.get_doc({"doctype": "Sales Invoice", "company": phone.company, "customer": customer,
                           "currency": phone.currency, "conversion_rate": 1, "update_stock": 1, "covison_sold_by": sold_by,
                           "selling_price_list": "Standard Selling PKR", "ignore_pricing_rule": 1,
                           "taxes_and_charges": "", "taxes": [],
                           "items": [{"item_code": phone.item_code, "qty": 1, "rate": flt(rate),
                                      "warehouse": phone.warehouse, "use_serial_batch_fields": 1,
                                      "serial_no": phone.serial_no, "covison_device": phone.name}],
                           "remarks": "Review this device-specific invoice and record payment before submission."})
    doc.insert()
    return {"name": doc.name, "docstatus": doc.docstatus}
