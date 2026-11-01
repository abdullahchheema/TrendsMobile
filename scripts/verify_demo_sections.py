"""Verify section coverage and reconciled totals after the explicit demo fill."""
import json
import frappe
from frappe.utils import flt, nowdate, add_days
from frappe.desk.query_report import run
from covison_erp.api import dashboard
from covison_erp.identifiers import valid_imei

BATCH='COV-DEMO-SECTIONS-20261029-B'
data=json.loads(frappe.db.get_global('covison_demo_sections_'+BATCH) or '{}')
assert data.get('complete'), data
company=data['company']
coverage=[];reports={}
filters=dict(company=company,from_date=add_days(nowdate(),-60),to_date=nowdate(),report_date=nowdate(),
             ageing_based_on='Posting Date',range='30, 60, 90, 120')
for sidebar in ('Mobile Shop','Point of Sale'):
    for row in frappe.get_doc('Sidebar',sidebar).items:
        if row.type!='Link':continue
        entry=dict(sidebar=sidebar,label=row.label,target=row.link_to,type=row.link_type)
        if row.link_type=='DocType':
            meta=frappe.get_meta(row.link_to)
            entry['records']=1 if meta.issingle else frappe.db.count(row.link_to)
            assert entry['records']>0,entry
        elif row.link_type=='Page':
            assert frappe.db.exists('Page',row.link_to),entry
            entry['exists']=True
        elif row.link_type=='Report':
            if row.link_to not in reports:
                result=run(row.link_to,filters=filters,ignore_prepared_report=True)
                reports[row.link_to]=dict(rows=len(result.get('result') or []),columns=len(result.get('columns') or []))
                assert reports[row.link_to]['rows']>0,(entry,result)
            entry.update(reports[row.link_to])
        coverage.append(entry)
assert len(reports)==13
phones=frappe.get_all('Mobile Device',fields=['name','imei_1','imei_2','status','pta_status','pta_status_2',
    'inspection_notes','inspection_status','warehouse','asking_price','covison_demo_batch'])
assert len(phones)==39
identifiers=[v for d in phones for v in (d.imei_1,d.imei_2) if v]
assert len(identifiers)==52 and len(set(identifiers))==52 and all(valid_imei(v) for v in identifiers)
assert all(d.covison_demo_batch and d.asking_price>0 and 'simulated' in d.inspection_notes.lower() for d in phones)
assert all(d.pta_status=='Not Checked' and d.pta_status_2 in (None,'Not Checked') for d in phones)
assert sum(d.status=='Available' for d in phones)==33 and sum(d.status=='Sold' for d in phones)==6
assert frappe.db.get_value('Mobile Device',data['new_devices'][1],'warehouse')==data['counter_warehouse']
for group in ('New Phones','Used Phones','Refurbished Phones','Accessories','Spare Parts'):
    assert frappe.db.count('Item',dict(item_group=group))>0,group
expected={'Mobile Model':12,'Item':24,'Mobile Staff':7,'Customer':8,'Supplier':7,
          'Sales Invoice':9,'Purchase Invoice':17,'Payment Entry':26}
counts={}
for dt,number in expected.items():
    counts[dt]=frappe.db.count(dt,{'docstatus':1} if dt in ('Sales Invoice','Purchase Invoice','Payment Entry') else {})
    assert counts[dt]==number,(dt,counts[dt],number)
for status in ('Open','Work In Progress','Closed'):
    assert frappe.db.exists('Warranty Claim',dict(status=status)),status
settings=frappe.get_single('Mobile Shop Settings')
assert all(settings.get(f) for f in ('shop_phone','shop_address','receipt_terms','urdu_receipt_note'))
assert len(data['contacts'])==14 and len(data['addresses'])==15
for doctype in ('Customer','Supplier'):
    for name in frappe.get_all(doctype,filters={'name':['like','Demo - %']},pluck='name'):
        assert frappe.db.exists('Dynamic Link',dict(parenttype='Contact',link_doctype=doctype,link_name=name)),name
        assert frappe.db.exists('Dynamic Link',dict(parenttype='Address',link_doctype=doctype,link_name=name)),name
assert frappe.db.get_value('Mobile Intake',data['draft_intake'],'docstatus')==0
for name in data['intakes']:
    intake=frappe.get_doc('Mobile Intake',name)
    assert intake.docstatus==1 and intake.covison_demo_batch==BATCH
    assert frappe.db.get_value('Purchase Receipt',intake.purchase_receipt,'covison_demo_batch')==BATCH
for name in data['sales']:
    doc=frappe.get_doc('Sales Invoice',name)
    assert doc.docstatus==1 and doc.covison_demo_batch
    assert 'DEMONSTRATION DATA' in frappe.get_print('Sales Invoice',name,print_format='Covison Mobile Receipt')
for name,amount in ((data['customer_advance'],5000),(data['supplier_advance'],10000)):
    pe=frappe.get_doc('Payment Entry',name)
    assert pe.docstatus==1 and abs(pe.unallocated_amount-amount)<0.01 and len(pe.references)==0
closing=frappe.get_doc('Mobile Daily Closing',data['submitted_closing'])
assert closing.docstatus==1 and all(abs(r.difference)<0.01 for r in closing.balances)
assert frappe.db.get_value('POS Opening Entry','POS-OPE-2026-00001','status')=='Open'
assert frappe.db.get_value('POS Opening Entry','POS-OPE-2026-00002','status')=='Closed'
assert frappe.db.count('POS Closing Entry',{'docstatus':1})==1
assert frappe.db.count('Mobile Daily Closing',{'docstatus':0})==1
phone_qty,phone_value=frappe.db.sql("select sum(actual_qty),sum(stock_value) from `tabBin` where item_code in (select name from `tabItem` where covison_is_mobile=1)")[0]
total_stock=frappe.db.sql('select sum(stock_value) from `tabBin`')[0][0]
assert flt(phone_qty)==33 and abs(flt(phone_value)-2563713)<0.01
assert abs(flt(total_stock)-2593163)<0.01
debit,credit=frappe.db.sql('select sum(debit),sum(credit) from `tabGL Entry` where is_cancelled=0')[0]
assert abs(flt(debit)-flt(credit))<0.01
ar=frappe.db.sql('select sum(outstanding_amount) from `tabSales Invoice` where docstatus=1')[0][0]
ap=frappe.db.sql('select sum(outstanding_amount) from `tabPurchase Invoice` where docstatus=1')[0][0]
revenue=frappe.db.sql('select sum(base_net_total) from `tabSales Invoice` where docstatus=1')[0][0]
assert abs(flt(ar)-205000)<0.01,ar
assert abs(flt(ap)-1101454)<0.01,ap
assert abs(flt(revenue)-668150)<0.01,revenue
dash=dashboard(status='All')
assert len(dash['devices'])==39 and dash['summary']['available']==33
assert dash['summary']['missing_imei']==0 and dash['demo_data'] and dash['currency']=='PKR'
print(json.dumps(dict(batch=BATCH,section_links=coverage,reports=reports,counts=counts,device_records=39,
    synthetic_imeis=52,available_phones=33,sold_phones=6,phone_stock_value_pkr=flt(phone_value),
    total_stock_value_pkr=flt(total_stock),net_sales_pkr=flt(revenue),invoice_receivable_pkr=flt(ar),
    invoice_payable_pkr=flt(ap),customer_advance_pkr=5000,supplier_advance_pkr=10000,
    balanced_general_ledger=True,open_shop_pos_shift_preserved=True,simulated_inspections_labelled=True,
    pta_verification_not_claimed=True,demo_receipts_labelled=True),indent=2,default=str))
