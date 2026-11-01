"""Staff activity uses posted transactions; sales and collections stay separate."""
import frappe
from frappe.utils import flt, getdate, nowdate
from covison_erp.staff import is_staff_admin


def activity(filters=None):
    if not is_staff_admin():
        frappe.throw('Only shop managers can view staff activity reports.',frappe.PermissionError)
    filters=frappe._dict(filters or {})
    company=filters.company or frappe.db.get_single_value('Global Defaults','default_company')
    frappe.get_doc('Company',company).check_permission('read')
    currency=frappe.get_cached_value('Company',company,'default_currency')
    start=filters.from_date or nowdate()[:7]+'-01'
    end=filters.to_date or nowdate()
    if getdate(start)>getdate(end):frappe.throw('From Date must be before To Date.')
    query={'company':company,'docstatus':1,'posting_date':['between',[start,end]]}
    result=[]
    def add(doc,kind,staff_field,name_field,amount,qty=0,mode=None):
        person=doc.get(staff_field)
        if filters.staff and person!=filters.staff:return
        result.append(dict(posting_date=doc.posting_date,staff=person,
            staff_name=doc.get(name_field) or (frappe.db.get_value('Mobile Staff',person,'staff_name') if person else 'Unassigned / historical'),
            activity=kind,document_type=doc.doctype,document=doc.name,amount=flt(amount),phones=flt(qty),
            mode_of_payment=mode,operator=doc.get('covison_posted_by') or doc.owner,currency=currency))
    for doctype in ('Purchase Receipt','Sales Invoice','POS Invoice','Payment Entry'):
        if not frappe.has_permission(doctype,'read'):continue
        names=frappe.get_list(doctype,filters=query,pluck='name',limit_page_length=0)
        for name in names:
            doc=frappe.get_doc(doctype,name)
            if doctype=='Purchase Receipt':
                qty=sum(flt(r.qty) for r in doc.items if frappe.get_cached_value('Item',r.item_code,'covison_is_mobile'))
                if qty:
                    add(doc,'Purchase Return' if doc.is_return else 'Purchase','covison_purchased_by','covison_purchaser_name',doc.base_net_total,qty)
            elif doctype in ('Sales Invoice','POS Invoice'):
                if doc.get('is_consolidated'):continue
                qty=sum(flt(r.qty) for r in doc.items if r.get('covison_device'))
                # Include accessory-only invoices in staff sales totals too.
                add(doc,'Sales Return' if doc.is_return else 'Sale','covison_sold_by','covison_seller_name',doc.base_net_total,qty)
                # Deferred POS is counted on its original POS invoice, not its closing consolidation.
                if doctype=='POS Invoice' or doc.get('is_pos'):
                    paid=flt(doc.get('base_paid_amount'))-flt(doc.get('base_change_amount'))
                    if doc.is_return:paid=-abs(paid)
                    if paid:
                        add(doc,'Refund' if paid<0 else 'Collection','covison_collected_by','covison_collector_name',abs(paid),mode='POS split payments')
            else:
                if doc.payment_type=='Internal Transfer':
                    kind='Internal Transfer'
                else:kind='Collection' if doc.payment_type=='Receive' else 'Payment / Refund'
                amount=doc.base_received_amount if doc.payment_type=='Receive' else doc.base_paid_amount
                add(doc,kind,'covison_handled_by','covison_handler_name',amount,mode=doc.mode_of_payment)
    return sorted(result,key=lambda r:(str(r['posting_date']),r['document'],r['activity']),reverse=True)


def detail_columns():
    return [dict(fieldname='posting_date',label='Date',fieldtype='Date',width=105),
        dict(fieldname='staff',label='Staff ID',fieldtype='Link',options='Mobile Staff',width=135),
        dict(fieldname='staff_name',label='Staff Name',width=170),dict(fieldname='activity',label='Activity',width=150),
        dict(fieldname='document_type',label='Document Type',width=145),
        dict(fieldname='document',label='Document',fieldtype='Dynamic Link',options='document_type',width=190),
        dict(fieldname='phones',label='Phones (Net Qty)',fieldtype='Float',width=120),
        dict(fieldname='amount',label='Amount',fieldtype='Currency',options='currency',width=145),
        dict(fieldname='mode_of_payment',label='Payment Method',width=150),
        dict(fieldname='operator',label='Posted / Recorded by Login',fieldtype='Link',options='User',width=180)]


def summary(filters=None):
    data={}
    for row in activity(filters):
        key=row['staff'] or ''
        if key not in data:
            data[key]=dict(staff=row['staff'],staff_name=row['staff_name'],currency=row['currency'],
                phones_bought=0,phones_sold=0,purchases=0,sales=0,collected=0,paid=0,transactions=0)
        total=data[key];total['transactions']+=1
        if row['activity'] in ('Purchase','Purchase Return'):
            total['phones_bought']+=row['phones'];total['purchases']+=row['amount']
        elif row['activity'] in ('Sale','Sales Return'):
            total['phones_sold']+=row['phones'];total['sales']+=row['amount']
        elif row['activity']=='Collection':total['collected']+=row['amount']
        elif row['activity'] in ('Payment / Refund','Refund'):total['paid']+=row['amount']
    columns=[dict(fieldname='staff',label='Staff ID',fieldtype='Link',options='Mobile Staff',width=135),
             dict(fieldname='staff_name',label='Staff Name',width=170)]
    for name,label in [('phones_bought','Phones Bought (Net)'),('phones_sold','Phones Sold (Net)'),
                       ('purchases','Net Device Purchases'),('sales','Net Sales'),('collected','Money Collected'),('paid','Money Paid / Refunded')]:
        columns.append(dict(fieldname=name,label=label,fieldtype='Currency' if name not in ('phones_bought','phones_sold') else 'Float',options='currency',width=175))
    return columns,sorted(data.values(),key=lambda r:r['staff_name'])
