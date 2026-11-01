import frappe
from frappe.model.document import Document
from frappe.utils import cint

class MobileShopSettings(Document):
    def validate(self):
        if cint(self.ageing_days) < 1:
            frappe.throw('Slow stock threshold must be at least one day.')
