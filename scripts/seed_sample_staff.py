"""Add user-requested sample Pakistani staff names; no logins or permissions are created.

Run inside the existing site via bench execute. Existing matching staff are retained.
"""
import json
import frappe

SAMPLES = [
    ('Ahmed Raza', 'Manager', 1, 1, 1),
    ('Ali Hassan', 'Salesperson', 1, 0, 1),
    ('Bilal Ahmed', 'Salesperson', 1, 0, 0),
    ('Usman Khan', 'Purchaser', 0, 1, 1),
    ('Muhammad Hamza', 'Cashier', 0, 0, 1),
    ('Ayesha Malik', 'Cashier', 0, 0, 1),
]
company = frappe.db.get_single_value('Global Defaults', 'default_company')
if not company:
    frappe.throw('Set the default company first.')
warehouse = frappe.db.get_value('Warehouse', {'company': company, 'warehouse_name': 'Stores', 'is_group': 0, 'disabled': 0})
if not warehouse:
    frappe.throw('Configure the receiving shop / warehouse first.')
result = []
try:
    for staff_name, designation, sells, buys, payments in SAMPLES:
        existing = frappe.db.get_value('Mobile Staff', {'staff_name': staff_name, 'company': company}, 'name')
        if existing:
            person = frappe.get_doc('Mobile Staff', existing)
            person.check_permission('read')
        else:
            person = frappe.get_doc(dict(doctype='Mobile Staff', staff_name=staff_name, designation=designation,
                company=company, warehouse=warehouse, enabled=1, can_sell=sells, can_buy=buys,
                can_handle_payments=payments)).insert()
        result.append(dict(id=person.name, name=person.staff_name, role=person.designation,
                           sells=bool(person.can_sell), buys=bool(person.can_buy), payments=bool(person.can_handle_payments)))
    print(json.dumps({'company': company, 'shop': warehouse, 'staff': result}))
except Exception:
    frappe.db.rollback()
    raise
