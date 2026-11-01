"""Create labeled demo purchases in an initialized Frappe site (drafts only).

Run with bench execute against this file inside the backend container.
Prices are fictional USD examples, not market valuations. Safe to rerun.
"""

import json

import frappe
from frappe.utils import getdate


COMPANY = "trends mobile"
WAREHOUSE = "Stores - TM"
DEMO_DATE = "2026-10-27"
MARKER = "DEMO-USED-IPHONES-20261027"
SUPPLIERS = [
    "Demo - Used Phone Wholesale",
    "Demo - Trade-In Mobiles",
    "Demo - Refurbished Phone Supply",
]
# Generation, model suffix, capacity (GB), finish, condition, quantity, USD each.
PHONES = [
    (11, "", 64, "Black", "Good", 3, 120),
    (11, "", 128, "White", "Very Good", 2, 145),
    (11, "Pro", 256, "Midnight Green", "Excellent", 1, 210),
    (12, "", 64, "Blue", "Good", 3, 175),
    (12, "", 128, "Black", "Very Good", 2, 200),
    (12, "Pro", 256, "Pacific Blue", "Excellent", 1, 285),
    (13, "", 128, "Midnight", "Good", 3, 250),
    (13, "", 256, "Starlight", "Very Good", 2, 285),
    (13, "Pro", 256, "Sierra Blue", "Excellent", 1, 375),
    (14, "", 128, "Blue", "Good", 3, 330),
    (14, "", 256, "Purple", "Very Good", 2, 370),
    (14, "Pro", 256, "Deep Purple", "Excellent", 1, 470),
    (15, "", 128, "Black", "Good", 3, 425),
    (15, "", 256, "Green", "Very Good", 2, 475),
    (15, "Pro", 256, "Natural Titanium", "Excellent", 1, 600),
]


def seed():
    frappe.set_user("Administrator")
    assert frappe.db.get_value("Company", COMPANY, "default_currency") == "USD"
    assert frappe.db.get_value("Warehouse", WAREHOUSE, "company") == COMPANY
    summary = []
    try:
        for supplier in SUPPLIERS:
            if not frappe.db.exists("Supplier", supplier):
                frappe.get_doc({
                    "doctype": "Supplier",
                    "supplier_name": supplier,
                    "supplier_group": "Local",
                    "supplier_type": "Company",
                    "default_currency": "USD",
                }).insert()

        for generation in range(11, 16):
            reference = f"{MARKER}-IP{generation}"
            existing = frappe.db.get_value("Purchase Order", {"order_confirmation_no": reference}, "name")
            if existing:
                order = frappe.get_doc("Purchase Order", existing)
            else:
                items = []
                for gen, suffix, capacity, color, condition, qty, rate in PHONES:
                    if gen != generation:
                        continue
                    model = f"iPhone {gen}" + (f" {suffix}" if suffix else "")
                    code = f"DEMO-USED-IP{gen}{'-PRO' if suffix else ''}-{capacity}"
                    description = (
                        f"DEMO ONLY: Used {model}, {capacity}GB, {color}. "
                        f"Condition: {condition}. Factory unlocked. "
                        "Fictional purchase price; no real device or IMEI assigned."
                    )
                    if not frappe.db.exists("Item", code):
                        frappe.get_doc({
                            "doctype": "Item",
                            "item_code": code,
                            "item_name": f"Demo - Used {model} {capacity}GB ({color})",
                            "item_group": "Products",
                            "stock_uom": "Nos",
                            "is_stock_item": 1,
                            "is_purchase_item": 1,
                            "is_sales_item": 1,
                            "description": description,
                            "item_defaults": [{"company": COMPANY, "default_warehouse": WAREHOUSE}],
                        }).insert()
                    items.append({
                        "item_code": code,
                        "description": description,
                        "qty": qty,
                        "uom": "Nos",
                        "conversion_factor": 1,
                        "rate": rate,
                        "schedule_date": getdate(DEMO_DATE),
                        "warehouse": WAREHOUSE,
                    })
                order = frappe.get_doc({
                    "doctype": "Purchase Order",
                    "company": COMPANY,
                    "supplier": SUPPLIERS[(generation - 11) % len(SUPPLIERS)],
                    "transaction_date": getdate(DEMO_DATE),
                    "schedule_date": getdate(DEMO_DATE),
                    "order_confirmation_no": reference,
                    "currency": "USD",
                    "conversion_rate": 1,
                    "set_warehouse": WAREHOUSE,
                    "buying_price_list": "Standard Buying",
                    "taxes_and_charges": "",
                    "taxes": [],
                    "items": items,
                    "terms": (
                        f"{reference}: DEMO ONLY. Used iPhone sample purchase. "
                        "All suppliers, quantities and prices are fictional. "
                        "Draft for demonstration; no stock receipt or payment posted."
                    ),
                }).insert()
            assert order.docstatus == 0, f"Expected a draft: {order.name}"
            assert len(order.items) == 3
            assert order.currency == "USD"
            assert float(order.grand_total) == sum(float(i.qty) * float(i.rate) for i in order.items)
            summary.append({
                "name": order.name,
                "model": f"iPhone {generation}",
                "supplier": order.supplier,
                "status": order.status,
                "qty": sum(float(i.qty) for i in order.items),
                "total_usd": float(order.grand_total),
                "items": [{"name": i.item_name, "qty": float(i.qty), "rate_usd": float(i.rate)} for i in order.items],
            })
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
        raise
    print(json.dumps({"demo_purchases": summary, "total_usd": sum(p["total_usd"] for p in summary)}, indent=2))


seed()
