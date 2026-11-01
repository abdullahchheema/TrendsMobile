import frappe
from frappe.model.document import Document

class MobileStaff(Document):
    def validate(self):
        self.staff_name=(self.staff_name or '').strip()
        if not self.staff_name:
            frappe.throw('Enter the staff member’s name.')
        if self.warehouse:
            shop=frappe.get_doc('Warehouse',self.warehouse)
            shop.check_permission('read')
            if shop.company!=self.company or shop.is_group:
                frappe.throw('Select a shop / warehouse belonging to this company.')
        self.user=self.user or None
        if self.user:
            user=frappe.get_doc('User',self.user)
            if not user.enabled or user.user_type!='System User':
                frappe.throw('Link an enabled system login.')
            if self.enabled and frappe.db.exists('Mobile Staff',{'company':self.company,'user':self.user,'enabled':1,'name':['!=',self.name]}):
                frappe.throw('This login already has an active staff record for this company.')
