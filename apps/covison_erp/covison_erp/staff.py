"""Staff responsibility is recorded separately from the login creating a document."""
import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.utils import flt

ADMIN_ROLES={'System Manager','Mobile Shop Manager'}
# (field, name snapshot, capability, label)
BUY=('covison_purchased_by','covison_purchaser_name','can_buy','Purchased by')
SELL=('covison_sold_by','covison_seller_name','can_sell','Sold by')
COLLECT=('covison_collected_by','covison_collector_name','can_handle_payments','Payment collected / refunded by')
HANDLE=('covison_handled_by','covison_handler_name','can_handle_payments','Payment handled by')
CLOSE=('covison_closed_by','covison_closer_name','can_handle_payments','Cash / account closing by')


def is_staff_admin():
    return frappe.session.user=='Administrator' or bool(ADMIN_ROLES.intersection(frappe.get_roles()))


def fields_for(spec):
    link,snapshot,_,label=spec
    return [dict(fieldname=link,label=label,fieldtype='Link',options='Mobile Staff',no_copy=1,in_standard_filter=1),
            dict(fieldname=snapshot,label=label+' (Name)',fieldtype='Data',read_only=1,no_copy=1)]


def setup():
    mapping={dt:fields_for(BUY) for dt in ('Mobile Intake','Purchase Receipt','Purchase Invoice','Purchase Order')}
    mapping.update({dt:fields_for(SELL)+fields_for(COLLECT) for dt in ('Sales Invoice','POS Invoice')})
    mapping['Payment Entry']=fields_for(HANDLE)
    mapping['Mobile Daily Closing']=fields_for(CLOSE)
    for dt in mapping:
        mapping[dt].append(dict(fieldname='covison_posted_by',label='Posted by Login',fieldtype='Link',options='User',read_only=1,no_copy=1))
    create_custom_fields(mapping,update=True)
    # Native POS renders configured additional invoice fields. Keep existing client fields.
    settings=frappe.get_single('POS Settings')
    for name,label in ((SELL[0],SELL[3]),(COLLECT[0],COLLECT[3])):
        if not any(r.fieldname==name for r in settings.invoice_fields):
            settings.append('invoice_fields',dict(fieldname=name,label=label,fieldtype='Link',options='Mobile Staff',reqd=0,read_only=0))
    settings.save(ignore_permissions=True)


def linked_staff(company, capability):
    return frappe.db.get_value('Mobile Staff',{'company':company,'enabled':1,capability:1,'user':frappe.session.user},'name')


def validate_assignment(doc,spec,required=False):
    field,snapshot,capability,label=spec
    company=doc.get('company')
    if not company:
        return
    selected=doc.get(field)
    if not selected:
        selected=linked_staff(company,capability)
        if selected:doc.set(field,selected)
    if not selected:
        if required and frappe.db.exists('Mobile Staff',{'company':company,'enabled':1}):
            frappe.throw(f'{label}: select an active staff member with the appropriate responsibility.')
        doc.set(snapshot,None)
        return
    person=frappe.get_doc('Mobile Staff',selected)
    person.check_permission('read')
    if person.company!=company or not person.enabled or not person.get(capability):
        frappe.throw(f'{label}: select active staff for this company enabled for this responsibility.')
    doc.set(snapshot,person.staff_name)


def specs(doc):
    if doc.doctype in ('Mobile Intake','Purchase Receipt','Purchase Invoice','Purchase Order'):
        return [(BUY,True)]
    if doc.doctype in ('Sales Invoice','POS Invoice'):
        # Consolidated documents contain multiple POS actors; report original POS invoices.
        if doc.get('is_consolidated'):return []
        paid=(doc.doctype=='POS Invoice' or doc.get('is_pos')) and abs(flt(doc.get('paid_amount')))>0
        return [(SELL,True),(COLLECT,paid)]
    if doc.doctype=='Payment Entry':return [(HANDLE,True)]
    if doc.doctype=='Mobile Daily Closing':return [(CLOSE,True)]
    return []


def validate_transaction(doc,method=None):
    if doc.docstatus==2:return
    for spec,_ in specs(doc):
        validate_assignment(doc,spec)


def before_submit(doc,method=None):
    doc.covison_posted_by = frappe.session.user
    for spec,required in specs(doc):
        validate_assignment(doc,spec,required=required)


@frappe.whitelist()
def defaults(company):
    frappe.get_doc('Company',company).check_permission('read')
    if not frappe.has_permission('Mobile Staff','read'):
        frappe.throw('No access to staff names.',frappe.PermissionError)
    return {'sold_by':linked_staff(company,'can_sell'),'collected_by':linked_staff(company,'can_handle_payments'),
            'purchased_by':linked_staff(company,'can_buy')}
