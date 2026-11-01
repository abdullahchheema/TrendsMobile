"""Integration checks run via bench execute; every test rolls back its records."""

import unittest

import frappe
from frappe.utils import flt, nowdate, now_datetime
from covison_erp.api import create_sale, dashboard
from covison_erp.identifiers import valid_imei
from erpnext.controllers.sales_and_purchase_return import make_return_doc


class MobileShopChecks(unittest.TestCase):
    def setUp(self):
        if not frappe.conf.get("covison_qa"):
            raise RuntimeError("Transaction fixtures require an isolated site with covison_qa enabled.")
        frappe.in_test = True
        frappe.set_user("Administrator")
        frappe.db.savepoint("covison_checks")
        # Each case starts without staff defaults; staff-specific cases add their own.
        # This runs only in the QA clone and is rolled back with the test.
        frappe.db.set_value("Mobile Staff", {}, "enabled", 0, update_modified=False)
        frappe.db.set_value("POS Opening Entry", {"docstatus": 1, "status": "Open"}, "status", "Closed", update_modified=False)
        frappe.local.covison_posting_sale = None
        self.phones = frappe.get_all("Mobile Device", filters={"status": "Available"}, pluck="name", order_by="name asc")
        self.phone = frappe.get_doc("Mobile Device", self.phones[0])

    def tearDown(self):
        frappe.set_user("Administrator")
        frappe.db.rollback(save_point="covison_checks")
        frappe.clear_cache()

    def customer(self):
        return frappe.get_doc({"doctype": "Customer", "customer_name": "Covison rollback test customer",
                                "customer_type": "Individual", "customer_group": "Individual",
                                "territory": "All Territories", "default_currency": "PKR"}).insert().name

    def register(self, phone, imei="490154203237518"):
        phone.imei_1 = imei
        phone.battery_health_checked = 1
        phone.battery_health = 88
        phone.asking_price = 65000
        phone.minimum_price = 50000
        phone.warranty_days = 7
        phone.save()

    def test_initial_stock_and_dashboard(self):
        data = dashboard()
        self.assertEqual(data["summary"]["available"], 30)
        self.assertEqual(data["summary"]["stock_value"], 2441862)
        self.assertEqual(len(data["devices"]), 30)
        self.assertTrue(all(not d.imei_1 for d in data["devices"]))

    def test_imei_validation_and_cross_slot_uniqueness(self):
        self.assertTrue(valid_imei("490154203237518"))
        for value in ("490154203237519", "000000000000000", "12345", "49015420323751A"):
            self.assertFalse(valid_imei(value))
        self.register(self.phone)
        other = frappe.get_doc("Mobile Device", self.phones[1])
        other.imei_1 = "356938035643809"
        other.imei_2 = self.phone.imei_1
        with self.assertRaises(frappe.ValidationError):
            other.save()

    def test_unregistered_phone_cannot_be_sold(self):
        with self.assertRaises(frappe.ValidationError):
            create_sale(self.phone.name, self.customer(), 65000)

    def test_native_model_variants_keep_mobile_details(self):
        from erpnext.controllers.item_variant import create_variant
        template = frappe.get_doc({"doctype": "Item", "item_code": "COV-ROLLBACK-TEMPLATE",
                                   "item_name": "Rollback phone template", "item_group": "Used Phones", "stock_uom": "Nos",
                                   "has_variants": 1, "variant_based_on": "Item Attribute", "covison_is_mobile": 1,
                                   "covison_model": self.phone.model,
                                   "attributes": [{"attribute": "Storage"}, {"attribute": "Color"}]}).insert()
        variant = create_variant(template.name, {"Storage": "256GB", "Color": "Blue"})
        variant.insert()
        self.assertEqual(variant.covison_model, self.phone.model)
        self.assertEqual(variant.covison_storage, "256GB")
        self.assertEqual(variant.covison_color, "Blue")
        self.assertTrue(variant.has_serial_no)
        self.assertTrue(variant.use_serial_no_wise_valuation)

    def test_sale_return_receipt_and_device_cost(self):
        self.register(self.phone)
        original_qty = frappe.db.get_value("Bin", {"item_code": self.phone.item_code, "warehouse": self.phone.warehouse}, "actual_qty")
        invoice_name = create_sale(self.phone.name, self.customer(), 65000)["name"]
        invoice = frappe.get_doc("Sales Invoice", invoice_name)
        invoice.submit()
        self.assertEqual(invoice.grand_total, 65000)
        self.phone.reload()
        self.assertEqual(self.phone.status, "Sold")
        self.assertEqual(self.phone.sales_invoice, invoice.name)
        self.assertEqual(invoice.items[0].covison_imei, "490154203237518")
        self.assertEqual(self.phone.sold_price, 65000)
        self.assertIsNone(frappe.db.get_value("Serial No", self.phone.serial_no, "warehouse"))
        html = frappe.get_print("Sales Invoice", invoice.name, print_format="Covison Mobile Receipt")
        self.assertIn("Covison ERP", html)
        self.assertIn("490154203237518", html)
        returned = make_return_doc("Sales Invoice", invoice.name)
        returned.covison_return_condition = "Same as sold"
        returned.covison_return_reason = "Customer return"
        returned.insert()
        returned.submit()
        self.phone.reload()
        self.assertEqual(self.phone.status, "Available")
        self.assertEqual(self.phone.sold_price, 0)
        self.assertEqual(frappe.db.get_value("Bin", {"item_code": self.phone.item_code, "warehouse": self.phone.warehouse}, "actual_qty"), original_qty)
        from covison_erp.mobile_shop.report.device_profit.device_profit import execute
        _, margins = execute()
        self.assertEqual(len(margins), 2)
        self.assertAlmostEqual(sum(r["device_margin"] for r in margins), 0, places=2)

    def test_salesperson_cost_visibility_and_minimum_price(self):
        self.register(self.phone)
        customer = self.customer()
        user = frappe.get_doc({"doctype": "User", "email": "covison-rollback@example.invalid", "first_name": "Covison test",
                               "send_welcome_email": 0, "user_type": "System User",
                               "roles": [{"role": "Sales User"}, {"role": "Mobile Salesperson"}]}).insert()
        frappe.set_user(user.name)
        data = dashboard()
        self.assertFalse(data["can_see_costs"])
        self.assertNotIn("stock_value", data["summary"])
        self.assertNotIn("purchase_cost", data["devices"][0])
        with self.assertRaises(frappe.ValidationError):
            create_sale(self.phone.name, customer, 30000)

    def test_pos_reserves_phone_until_closing_or_cancellation(self):
        self.register(self.phone)
        settings = frappe.get_single("POS Settings")
        settings.invoice_type = "POS Invoice"
        settings.save()
        opening = frappe.get_doc({"doctype": "POS Opening Entry", "company": self.phone.company,
                                  "pos_profile": "Covison Mobile Shop", "user": "Administrator",
                                  "period_start_date": now_datetime(),
                                  "balance_details": [{"mode_of_payment": "Cash", "opening_amount": 0}]}).insert()
        opening.submit()
        pos = frappe.get_doc({"doctype": "POS Invoice", "update_stock": 1, "company": self.phone.company, "customer": self.customer(),
                              "pos_profile": "Covison Mobile Shop", "currency": "PKR", "conversion_rate": 1,
                              "selling_price_list": "Standard Selling PKR", "taxes_and_charges": "", "taxes": [],
                              "payments": [{"mode_of_payment": "Cash", "amount": 65000}],
                              "items": [{"item_code": self.phone.item_code, "qty": 1, "rate": 65000,
                                         "warehouse": self.phone.warehouse, "serial_no": self.phone.serial_no,
                                         "use_serial_batch_fields": 1, "covison_device": self.phone.name}]}).insert()
        pos.submit()
        self.phone.reload()
        self.assertEqual(self.phone.status, "Reserved")
        with self.assertRaises(frappe.ValidationError):
            create_sale(self.phone.name, pos.customer, 65000)
        pos.cancel()
        self.phone.reload()
        self.assertEqual(self.phone.status, "Available")

    def test_current_pos_sales_invoice_mode_posts_device_immediately(self):
        self.register(self.phone)
        opening = frappe.get_doc({"doctype": "POS Opening Entry", "company": self.phone.company,
                                  "pos_profile": "Covison Mobile Shop", "user": "Administrator",
                                  "period_start_date": now_datetime(),
                                  "balance_details": [{"mode_of_payment": "Cash", "opening_amount": 0}]}).insert()
        opening.submit()
        invoice = frappe.get_doc("Sales Invoice", create_sale(self.phone.name, self.customer(), 65000)["name"])
        invoice.is_pos = 1
        invoice.pos_profile = "Covison Mobile Shop"
        invoice.append("payments", {"mode_of_payment": "Cash", "amount": 65000})
        invoice.save()
        invoice.submit()
        self.phone.reload()
        self.assertEqual(self.phone.status, "Sold")
        self.assertEqual(invoice.outstanding_amount, 0)

    def test_same_variant_can_have_different_exact_cost(self):
        cost = flt(self.phone.purchase_cost) + 1000
        warehouse = self.phone.warehouse
        receipt = frappe.get_doc({"doctype": "Purchase Receipt", "company": self.phone.company,
                                  "supplier": self.phone.supplier, "currency": "PKR", "conversion_rate": 1,
                                  "posting_date": nowdate(), "taxes_and_charges": "", "taxes": [],
                                  "items": [{"item_code": self.phone.item_code, "qty": 1, "received_qty": 1,
                                             "rate": cost, "warehouse": warehouse, "serial_no": "COV-ROLLBACK-TEST",
                                             "use_serial_batch_fields": 1}]}).insert()
        receipt.submit()
        new_phone = frappe.get_doc("Mobile Device", "COV-ROLLBACK-TEST")
        self.assertEqual(new_phone.purchase_cost, cost)
        self.assertNotEqual(new_phone.purchase_cost, self.phone.purchase_cost)
        self.register(new_phone)
        invoice = frappe.get_doc("Sales Invoice", create_sale(new_phone.name, self.customer(), 70000)["name"])
        invoice.submit()
        entries = frappe.get_all("Stock Ledger Entry", filters={"voucher_type": "Sales Invoice", "voucher_no": invoice.name,
                                 "is_cancelled": 0}, fields=["stock_value_difference"])
        self.assertAlmostEqual(sum(flt(d.stock_value_difference) for d in entries), -cost, places=2)
        self.assertEqual(frappe.db.get_value("Serial No", self.phone.serial_no, "warehouse"), warehouse)


def run():
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(MobileShopChecks))
    if not result.wasSuccessful():
        raise AssertionError("Covison mobile-shop integration checks failed")


run()
