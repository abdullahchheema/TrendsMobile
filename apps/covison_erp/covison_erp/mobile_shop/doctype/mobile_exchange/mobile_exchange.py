import frappe
from frappe.model.document import Document
from frappe.utils import flt


class MobileExchange(Document):
    def validate(self):
        receipt = frappe.get_doc('Purchase Receipt', self.purchase_receipt)
        invoice = frappe.get_doc('Sales Invoice', self.sales_invoice)
        receipt.check_permission('read'); invoice.check_permission('read')
        if receipt.company != self.company or invoice.company != self.company:
            frappe.throw('The purchase and sale must belong to this company.')
        if invoice.customer != self.customer:
            frappe.throw('The sales invoice belongs to another customer.')
        if receipt.docstatus != 1 or invoice.docstatus == 2 or receipt.is_return or invoice.is_return:
            frappe.throw('Select a submitted incoming receipt and an active outgoing sale, not returns.')
        if receipt.get('covison_trade_in_customer') != self.customer:
            frappe.throw('The incoming receipt must identify this customer as its trade-in customer.')
        if receipt.currency != invoice.currency:
            frappe.throw('Both exchange documents must use the same currency.')
        if not any(r.get('covison_device') for r in invoice.items):
            frappe.throw('The outgoing invoice must include a mobile phone.')
        for key in ('purchase_receipt', 'sales_invoice'):
            if frappe.db.exists('Mobile Exchange', {key: self.get(key), 'name': ['!=', self.name]}):
                frappe.throw('This document already belongs to another exchange.')
        if self.purchase_invoice:
            bill = frappe.get_doc('Purchase Invoice', self.purchase_invoice); bill.check_permission('read')
            if bill.docstatus == 2 or bill.is_return or bill.company != self.company or bill.supplier != receipt.supplier or bill.currency != invoice.currency:
                frappe.throw('Select the active supplier bill for this incoming purchase.')
            if not bill.items or any(r.purchase_receipt != receipt.name for r in bill.items):
                frappe.throw('The exchange supplier bill must contain only this incoming receipt.')
            if abs(flt(bill.grand_total)-flt(receipt.grand_total)) > 0.01:
                frappe.throw('The exchange bill must cover the complete incoming receipt at its agreed value.')
        old = self.get_doc_before_save()
        if not self.flags.get('settling_exchange'):
            for key in ('journal_entry', 'payment_entry'):
                if self.get(key) != (old.get(key) if old else None):
                    frappe.throw('Settlement links are assigned by the Settle Exchange action.')
        if old and (old.journal_entry or old.payment_entry):
            for key in ('company', 'customer', 'purchase_receipt', 'sales_invoice', 'purchase_invoice', 'journal_entry', 'payment_entry'):
                if self.get(key) != old.get(key):
                    frappe.throw('Settled exchange references cannot be changed. Review the original accounting records.')
        self.currency = receipt.currency
        self.incoming_value = receipt.grand_total
        self.outgoing_value = invoice.grand_total
        self.net_difference = flt(self.outgoing_value) - flt(self.incoming_value)
        self.settlement_direction = ('Customer pays shop' if self.net_difference > 0 else
                                     'Shop pays customer' if self.net_difference < 0 else 'No difference')
        if self.journal_entry or self.payment_entry:
            active = all(frappe.db.get_value(dt, name, 'docstatus') == 1 for dt, name in
                (('Journal Entry', self.journal_entry), ('Payment Entry', self.payment_entry)) if name)
            self.settlement_status = 'Settled' if active else 'Reversed / Review'
        else:
            self.settlement_status = 'Not Settled'
