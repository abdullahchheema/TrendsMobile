"""Migrate the sample project's active purchases and prices to PKR.

Uses normal cancellation/amendment, document validation and ledger posting.
Retains USD documents and disabled price lists as history. Requires a site backup.
"""

import json
from decimal import Decimal

import frappe
from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt


COMPANY = "trends mobile"
RATE = Decimal("277.80")
DATE = "2026-10-28"
SOURCE = "https://pakistancurrency.com/currency-rates/usd-to-pkr"


def pkr(value):
    return float((Decimal(str(value or 0)) * RATE).quantize(Decimal("0.01")))


def migrate():
    frappe.set_user("Administrator")
    company = frappe.get_doc("Company", COMPANY)
    assert company.default_currency == "USD", "Migration expects the original USD company"
    names = frappe.get_all("Purchase Order", filters={
        "company": COMPANY, "docstatus": 1,
        "order_confirmation_no": ["like", "DEMO-USED-IPHONES-20261027-%"],
    }, pluck="name", order_by="name asc")
    assert len(names) == 5
    for dt in ["Sales Invoice", "Sales Order", "Delivery Note", "Quotation", "Purchase Invoice",
               "Supplier Quotation", "Stock Entry", "Payment Entry", "Journal Entry"]:
        assert not frappe.db.count(dt, {"company": COMPANY, "docstatus": 1}), f"Unexpected {dt}"
    assert frappe.db.count("Purchase Order", {"company": COMPANY, "docstatus": 1}) == 5
    assert frappe.db.count("Purchase Receipt", {"company": COMPANY, "docstatus": 1}) == 5
    orders = [frappe.get_doc("Purchase Order", name) for name in names]
    assert sum(float(d.grand_total) for d in orders) == 8790
    prices = [frappe.get_doc("Item Price", name) for name in frappe.get_all("Item Price", pluck="name")]
    assert len(prices) == 15 and all(d.currency == "USD" for d in prices)
    assert not frappe.db.count("GL Entry", {"company": COMPANY, "is_cancelled": 0,
                                           "voucher_type": ["!=", "Purchase Receipt"]})
    result = []
    try:
        # Reverse the original stock/ledger entries before changing any currency.
        for name in frappe.get_all("Purchase Receipt", filters={"company": COMPANY, "docstatus": 1},
                                   pluck="name", order_by="posting_date desc, creation desc"):
            frappe.get_doc("Purchase Receipt", name).cancel()
        for order in reversed(orders):
            order.reload()
            order.cancel()
        assert not frappe.db.count("GL Entry", {"company": COMPANY, "is_cancelled": 0})
        assert not company.check_if_transactions_exist()

        currency = frappe.get_doc("Currency", "PKR")
        currency.enabled = 1
        currency.save()
        # The account validator permits changing currency only after active entries are cancelled.
        for name in frappe.get_all("Account", filters={"company": COMPANY}, pluck="name", order_by="lft asc"):
            account = frappe.get_doc("Account", name)
            assert account.account_currency in (None, "", "USD")
            account.account_currency = "PKR"
            if not account.parent_account:
                # Root group documents reject every save, including metadata edits.
                # They cannot hold postings; validate currency and update only this field.
                assert account.is_group and not frappe.db.exists("GL Entry", {"account": name})
                account.validate_account_currency()
                account.db_set("account_currency", "PKR")
            else:
                account.save()
        frappe.clear_cache()
        company.reload()
        company.default_currency = "PKR"
        company.country = "Pakistan"
        company.save()
        defaults = frappe.get_single("Global Defaults")
        defaults.default_currency = "PKR"
        defaults.country = "Pakistan"
        defaults.save()

        # A new price list is the supported path when existing prices have another currency.
        for side in ("Buying", "Selling"):
            old_name = f"Standard {side}"
            new_name = f"Standard {side} PKR"
            assert not frappe.db.exists("Price List", new_name)
            old = frappe.get_doc("Price List", old_name)
            new = frappe.copy_doc(old)
            new.price_list_name = new_name
            new.currency = "PKR"
            new.enabled = 1
            new.insert()
            settings = frappe.get_single(f"{side} Settings")
            settings.set(f"{side.lower()}_price_list", new_name)
            settings.save()
            old.enabled = 0
            old.save()

        for old in prices:
            new = frappe.copy_doc(old)
            new.price_list = old.price_list + " PKR"
            new.price_list_rate = pkr(old.price_list_rate)
            new.currency = "PKR"
            new.note = f"Converted from USD at {RATE} PKR/USD on {DATE}. Source: {SOURCE}"
            new.insert()

        for dt in ("Supplier", "Customer"):
            for name in frappe.get_all(dt, pluck="name"):
                doc = frappe.get_doc(dt, name)
                if doc.default_currency in (None, "", "USD"):
                    doc.default_currency = "PKR"
                if doc.default_price_list in ("Standard Buying", "Standard Selling"):
                    doc.default_price_list += " PKR"
                doc.save()

        frappe.get_doc({"doctype": "Currency Exchange", "date": DATE,
                        "from_currency": "USD", "to_currency": "PKR", "exchange_rate": float(RATE),
                        "for_buying": 1, "for_selling": 1}).insert()

        for original in orders:
            original.reload()
            order = frappe.copy_doc(original)
            order.amended_from = original.name
            order.currency = "PKR"
            order.conversion_rate = 1
            order.buying_price_list = "Standard Buying PKR"
            order.price_list_currency = "PKR"
            order.plc_conversion_rate = 1
            order.transaction_date = DATE
            order.schedule_date = DATE
            order.terms = (f"Converted from {original.name} (USD) at {RATE} PKR/USD on {DATE}. "
                           f"Rate source: {SOURCE}. Original supplier and quantities retained. No payment recorded.")
            for row, old in zip(order.items, original.items):
                row.rate = pkr(old.rate)
                row.price_list_rate = pkr(old.price_list_rate or old.rate)
                row.base_rate = row.rate
                row.base_price_list_rate = row.price_list_rate
                row.schedule_date = DATE
                row.received_qty = 0
                row.billed_amt = 0
                row.discount_percentage = 0
                row.discount_amount = 0
            order.insert()
            order.submit()
            assert abs(float(order.grand_total) - pkr(original.grand_total)) < 0.01
            receipt = make_purchase_receipt(order.name)
            receipt.posting_date = DATE
            receipt.set_posting_time = 1
            receipt.remarks = f"PKR replacement for the USD receipt linked to {original.name}; rate {RATE} PKR/USD."
            receipt.insert()
            receipt.submit()
            order.reload()
            assert order.docstatus == 1 and float(order.per_received) == 100
            result.append({"original_usd_order": original.name, "pkr_order": order.name,
                           "receipt": receipt.name, "status": order.status,
                           "total_pkr": float(order.grand_total)})

        bins = frappe.get_all("Bin", filters={"warehouse": "Stores - TM", "item_code": ["like", "USED-IP%"]},
                              fields=["item_code", "actual_qty", "stock_value"])
        assert len(bins) == 15 and sum(float(b.actual_qty) for b in bins) == 30
        assert abs(sum(float(b.stock_value) for b in bins) - pkr(8790)) < 0.01
        assert frappe.db.count("Item Price", {"currency": "PKR"}) == 15
        assert not frappe.db.count("Price List", {"enabled": 1, "currency": ["!=", "PKR"]})
        assert not frappe.db.count("GL Entry", {"company": COMPANY, "is_cancelled": 0,
                                               "account_currency": ["!=", "PKR"]})
        assert not frappe.db.count("Purchase Order", {"company": COMPANY, "docstatus": 1,
                                                     "currency": ["!=", "PKR"]})
        frappe.db.commit()
        frappe.clear_cache()
    except Exception:
        frappe.db.rollback()
        frappe.clear_cache()
        raise
    print(json.dumps({"rate": str(RATE), "source": SOURCE, "purchases": result,
                      "stock_quantity": 30, "stock_value_pkr": pkr(8790)}, indent=2))


migrate()
