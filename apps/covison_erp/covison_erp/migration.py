"""Explicit, guarded conversion of the existing 30 sample phones to serial tracking."""

import json
import re

import frappe
from frappe.model.naming import make_autoname
from frappe.utils import cint, flt


def enable_existing_device_tracking():
    frappe.set_user("Administrator")
    if frappe.db.count("Mobile Device"):
        return {"status": "already configured", "devices": frappe.db.count("Mobile Device")}
    names = frappe.get_all("Item", filters={"name": ["like", "USED-IP%"]}, pluck="name")
    assert len(names) == 15, "Expected the 15 existing iPhone variants"
    bins = frappe.get_all("Bin", filters={"item_code": ["in", names]}, fields=["actual_qty", "stock_value"])
    old_qty = sum(flt(b.actual_qty) for b in bins)
    old_value = sum(flt(b.stock_value) for b in bins)
    assert old_qty == 30 and abs(old_value - 2441862) < .01
    assert not frappe.db.count("Sales Invoice", {"docstatus": 1})
    assert not frappe.db.count("POS Invoice", {"docstatus": 1})
    receipts = [frappe.get_doc("Purchase Receipt", name) for name in frappe.get_all(
        "Purchase Receipt", filters={"company": "trends mobile", "docstatus": 1}, pluck="name", order_by="name asc")]
    assert len(receipts) == 5 and all(r.currency == "PKR" for r in receipts)
    assert all(row.item_code in names for r in receipts for row in r.items)
    replacements = []
    try:
        for receipt in reversed(receipts):
            receipt.cancel()
        for name in names:
            item = frappe.get_doc("Item", name)
            match = re.fullmatch(r"USED-IP(\d+)(-PRO)?-(\d+)", name)
            assert match
            generation, pro, storage = match.groups()
            model = f"Apple iPhone {generation}" + (" Pro" if pro else "")
            if not frappe.db.exists("Mobile Model", model):
                frappe.get_doc({"doctype": "Mobile Model", "model_name": model, "brand": "Apple"}).insert()
            color = re.search(r"\(([^)]+)\)", item.item_name)
            item.update({"covison_is_mobile": 1, "covison_model": model, "covison_storage": storage + "GB",
                         "covison_color": color.group(1) if color else "", "brand": "Apple", "item_group": "Used Phones",
                         "has_serial_no": 1, "use_serial_no_wise_valuation": 1,
                         "serial_no_series": "COV-PHN-.######"})
            item.save()
        for old in receipts:
            receipt = frappe.copy_doc(old)
            receipt.amended_from = old.name
            receipt.remarks = (f"Device-tracking replacement of {old.name}. Same phones, quantities and PKR costs. "
                               "Internal serial IDs assigned; actual IMEIs and inspection details must be recorded.")
            for row in receipt.items:
                assert flt(row.qty) == cint(row.qty) and row.qty > 0
                row.serial_and_batch_bundle = None
                row.rejected_serial_and_batch_bundle = None
                row.serial_no = "\n".join(make_autoname("COV-PHN-.######") for _ in range(cint(row.qty)))
                row.use_serial_batch_fields = 1
            receipt.insert()
            receipt.submit()
            for row in receipt.items:
                from covison_erp.devices import serials_for_row
                condition = re.search(r"Condition: ([^.]+)\.", row.description or "")
                for serial in serials_for_row(row):
                    phone = frappe.get_doc("Mobile Device", serial)
                    if condition and condition.group(1) in ("Good", "Very Good", "Excellent", "Fair"):
                        phone.condition_grade = condition.group(1)
                    phone.inspection_status = "Pending"
                    phone.inspection_notes = "Imported from existing stock. Confirm the physical phone's IMEI, condition, battery and PTA result before sale."
                    phone.save()
            replacements.append({"original": old.name, "replacement": receipt.name})
        bins = frappe.get_all("Bin", filters={"item_code": ["in", names]}, fields=["actual_qty", "stock_value"])
        assert sum(flt(b.actual_qty) for b in bins) == old_qty
        assert abs(sum(flt(b.stock_value) for b in bins) - old_value) < .01
        assert frappe.db.count("Mobile Device") == 30
        assert frappe.db.count("Serial No", {"item_code": ["in", names], "status": "Active"}) == 30
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        frappe.clear_cache()
        raise
    return {"devices": 30, "quantity": old_qty, "value_pkr": old_value, "receipts": replacements}
