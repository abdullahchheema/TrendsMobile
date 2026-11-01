"""Submit the prepared sample intake on the isolated QA copy, then roll it back."""
import json
import frappe

assert frappe.local.site=='covison-qa.local' and frappe.conf.get('covison_qa')
data=json.loads(frappe.db.get_global('covison_demo_sections_COV-DEMO-SECTIONS-20261029-B'))
before=frappe.db.count('Mobile Device')
frappe.db.savepoint('prepared_intake_check')
try:
    doc=frappe.get_doc('Mobile Intake',data['draft_intake'])
    doc.submit();doc.reload()
    assert doc.docstatus==1 and doc.purchase_receipt and doc.mobile_device
    assert frappe.db.get_value('Purchase Receipt',doc.purchase_receipt,'covison_demo_batch')==data['batch']
    assert frappe.db.get_value('Mobile Device',doc.mobile_device,'covison_demo_batch')==data['batch']
    assert frappe.db.count('Mobile Device')==before+1
finally:
    frappe.db.rollback(save_point='prepared_intake_check')
assert frappe.db.count('Mobile Device')==before
assert frappe.db.get_value('Mobile Intake',data['draft_intake'],'docstatus')==0
print(json.dumps(dict(prepared_intake_submit_passed=True,demo_labels_inherited=True,rolled_back=True)))
