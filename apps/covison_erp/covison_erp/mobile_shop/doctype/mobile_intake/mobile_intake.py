import frappe
from frappe.model.document import Document
from frappe.model.naming import make_autoname
from frappe.utils import flt, getdate, nowdate
from covison_erp.identifiers import valid_imei
from covison_erp.workflows import pkr_company


class MobileIntake(Document):
    def amended_serial(self):
        if not self.amended_from:
            return None
        previous = frappe.get_doc('Mobile Intake', self.amended_from)
        previous.check_permission('read')
        if previous.docstatus != 2 or previous.company != self.company or previous.item_code != self.item_code:
            frappe.throw('Amend the cancelled intake for the same company and product variant.')
        return previous.mobile_device

    def validate(self):
        if self.docstatus == 0 and (self.purchase_receipt or self.mobile_device):
            frappe.throw('Posted receipt and device links are assigned only when receiving a phone.')
        pkr_company(self.company)
        frappe.get_doc('Supplier', self.supplier).check_permission('read')
        item = frappe.get_doc('Item', self.item_code); item.check_permission('read')
        if not item.get('covison_is_mobile') or item.has_variants or item.disabled:
            frappe.throw('Select an active mobile phone product variant.')
        warehouse = frappe.get_doc('Warehouse', self.warehouse); warehouse.check_permission('read')
        if warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
            frappe.throw('Select an active receiving warehouse for this company.')
        if flt(self.purchase_price) <= 0:
            frappe.throw('Enter the agreed purchase price in PKR.')
        if getdate(self.posting_date) > getdate(nowdate()):
            frappe.throw('Purchase date cannot be in the future.')
        if self.purchase_source == 'Customer Trade-In':
            if not self.trade_in_customer:
                frappe.throw('Select the customer trading in this phone.')
            frappe.get_doc('Customer', self.trade_in_customer).check_permission('read')
        for name in ('imei_1', 'imei_2'):
            value = (self.get(name) or '').strip()
            self.set(name, value or None)
            if value and not valid_imei(value):
                frappe.throw('Enter a valid 15-digit IMEI from the actual phone.')
            owner = frappe.db.get_value('Mobile IMEI', value, 'mobile_device') if value else None
            if owner and owner not in (self.mobile_device, self.amended_serial()):
                frappe.throw(f'This IMEI already belongs to {owner}. Use a sales return for a previously sold phone.')
        if self.imei_1 and self.imei_1 == self.imei_2:
            frappe.throw('IMEI 1 and IMEI 2 must be different.')
        self.currency = 'PKR'

    def before_submit(self):
        if not self.imei_1:
            frappe.throw('Record the actual IMEI before receiving this phone.')
        if self.inspection_status in (None, 'Pending') or self.condition_grade == 'Not Inspected':
            frappe.throw('Complete the condition and inspection assessment before receiving this phone.')

    def on_submit(self):
        serial = self.amended_serial() or make_autoname('COV-PHN-.######')
        receipt = frappe.get_doc(dict(doctype='Purchase Receipt', company=self.company, supplier=self.supplier,
            posting_date=self.posting_date, set_posting_time=1, currency='PKR', conversion_rate=1,
            covison_demo_batch=self.get("covison_demo_batch"),
            covison_purchased_by=self.get("covison_purchased_by"),
            covison_purchase_source=self.purchase_source, covison_trade_in_customer=self.trade_in_customer,
            taxes_and_charges='', taxes=[], items=[dict(item_code=self.item_code, qty=1, received_qty=1,
                rate=self.purchase_price, warehouse=self.warehouse, serial_no=serial, use_serial_batch_fields=1)]))
        receipt.insert(); receipt.submit()
        phone = frappe.get_doc('Mobile Device', serial)
        # Explicitly copy editable inspection/pricing data only; stock and costs come from the ledger.
        names = ('imei_1 imei_2 manufacturer_serial regional_version market_description device_type condition_grade '
                 'battery_health_checked battery_health inspection_status inspection_date display_condition biometrics '
                 'camera_test charging_test audio_test network_test carrier_lock activation_lock sim_configuration '
                 'repair_history included_accessories inspection_notes pta_status pta_verified_on pta_verified_by '
                 'pta_evidence pta_notes pta_status_2 pta_verified_on_2 pta_evidence_2 asking_price minimum_price '
                 'wholesale_price warranty_days warranty_terms disclosed_faults').split()
        for name in names:
            phone.set(name, self.get(name))
        phone.save()
        self.db_set('purchase_receipt', receipt.name)
        self.db_set('mobile_device', serial)

    def on_cancel(self):
        if self.purchase_receipt and frappe.db.get_value('Purchase Receipt', self.purchase_receipt, 'docstatus') == 1:
            receipt = frappe.get_doc('Purchase Receipt', self.purchase_receipt)
            receipt.cancel()
