"""Finalize the previously created iPhone orders using user-approved sample values.

Run inside an initialized Frappe site. Posts purchase receipts, never payments.
"""

import json

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
from frappe.model.rename_doc import rename_doc


def finalize():
    frappe.set_user("Administrator")
    names = frappe.get_all(
        "Purchase Order",
        filters={"order_confirmation_no": ["like", "DEMO-USED-IPHONES-20261027-%"]},
        pluck="name",
        order_by="name asc",
    )
    assert len(names) == 5, f"Expected five orders, found {len(names)}"
    result = []
    try:
        codes = frappe.get_all("Item", filters={"item_code": ["like", "DEMO-USED-IP%"]}, pluck="name")
        for code in codes:
            new_code = code.removeprefix("DEMO-")
            if frappe.db.exists("Item", new_code):
                raise ValueError(f"Item already exists: {new_code}")
            rename_doc("Item", code, new_code)
            item = frappe.get_doc("Item", new_code)
            item.item_name = item.item_name.removeprefix("Demo - ")
            item.description = item.description.removeprefix("DEMO ONLY: ").replace(
                "Fictional purchase price; no real device or IMEI assigned.",
                "Purchase values approved by user. No IMEI assigned.",
            )
            item.save()

        for name in names:
            order = frappe.get_doc("Purchase Order", name)
            assert order.docstatus in (0, 1), f"Order is cancelled: {name}"
            if order.docstatus == 0:
                for row in order.items:
                    item = frappe.get_doc("Item", row.item_code)
                    row.item_name = item.item_name
                    row.description = item.description
                order.terms = (
                    "Purchase finalized at user request using the previously entered suppliers, "
                    "quantities and USD prices. Values originated as sample data and were "
                    "explicitly accepted by the user. No supplier payment recorded."
                )
                order.save()
                order.submit()

            receipt_names = frappe.get_all(
                "Purchase Receipt Item", filters={"purchase_order": name, "docstatus": 1},
                pluck="parent",
            )
            if receipt_names:
                assert float(order.per_received) == 100, f"Partial receipt requires review: {name}"
                receipts = sorted(set(receipt_names))
            else:
                assert float(order.per_received) == 0, f"Unexpected received quantity: {name}"
                receipt = make_purchase_receipt(name)
                receipt.posting_date = "2026-10-27"
                receipt.set_posting_time = 1
                receipt.remarks = (
                    "Received into Stores - TM at user request using approved existing "
                    "supplier, quantity and price details. No payment posted."
                )
                for row in receipt.items:
                    item = frappe.get_doc("Item", row.item_code)
                    row.item_name = item.item_name
                    row.description = item.description
                receipt.insert()
                receipt.submit()
                receipts = [receipt.name]
            order.reload()
            assert order.docstatus == 1 and float(order.per_received) == 100
            result.append({"order": name, "receipts": receipts, "status": order.status,
                           "qty": float(order.total_qty), "total_usd": float(order.grand_total)})

        stock = frappe.get_all(
            "Bin", filters={"warehouse": "Stores - TM", "item_code": ["like", "USED-IP%"]},
            fields=["item_code", "actual_qty", "stock_value"], order_by="item_code asc",
        )
        assert len(stock) == 15, f"Expected fifteen stocked variants, found {len(stock)}"
        assert sum(float(b.actual_qty) for b in stock) == 30
        assert sum(float(b.stock_value) for b in stock) == 8790
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    print(json.dumps({"purchases": result, "stock": stock}, indent=2, default=str))


finalize()
