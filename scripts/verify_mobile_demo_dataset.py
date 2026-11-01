"""Read-only verification of the explicit mobile-market demonstration dataset."""
import json
import frappe
from frappe.utils import flt,add_days,nowdate
from covison_erp.identifiers import valid_imei
from covison_erp.api import dashboard
from covison_erp.staff_reports import summary
from covison_erp.mobile_shop.report.device_profit.device_profit import execute as profit
from covison_erp.mobile_shop.report.mobile_stock_ageing.mobile_stock_ageing import execute as ageing

BATCH='COV-DEMO-PK-20261029-A'
company=frappe.db.get_single_value('Global Defaults','default_company')
data=json.loads(frappe.db.get_global('covison_demo_dataset_'+BATCH) or '{}')
assert data.get('complete'),data
phones=frappe.get_all('Mobile Device',filters={'covison_demo_batch':BATCH},fields=['name','imei_1','imei_2','status','pta_status','pta_status_2','asking_price'])
assert len(phones)==36,len(phones)
identifiers=[value for d in phones for value in (d.imei_1,d.imei_2) if value]
assert len(identifiers)==46 and len(set(identifiers))==46
assert all(valid_imei(value) for value in identifiers)
assert all(d.pta_status=='Not Checked' and d.pta_status_2 in (None,'Not Checked') for d in phones)
assert all(d.asking_price>0 for d in phones)
assert sum(d.status=='Available' for d in phones)==31
assert sum(d.status=='Sold' for d in phones)==5
qty,value=frappe.db.sql("select sum(actual_qty),sum(stock_value) from `tabBin` where item_code in (select name from `tabItem` where covison_is_mobile=1)")[0]
assert flt(qty)==31 and abs(flt(value)-2426713)<0.01,(qty,value)
debit,credit=frappe.db.sql('select sum(debit),sum(credit) from `tabGL Entry` where is_cancelled=0')[0]
assert abs(flt(debit)-flt(credit))<0.01
counts={dt:frappe.db.count(dt,{'covison_demo_batch':BATCH,'docstatus':1}) for dt in ('Mobile Intake','Purchase Receipt','Purchase Invoice','Purchase Order','Sales Invoice','Payment Entry','Journal Entry','Mobile Exchange','POS Closing Entry')}
assert counts['Mobile Intake']==6 and counts['Purchase Receipt']==11 and counts['Purchase Invoice']==13
assert counts['Sales Invoice']==7 and counts['Payment Entry']==18 and counts['Journal Entry']==2
assert frappe.db.count('Mobile Exchange',{'covison_demo_batch':BATCH})==1
assert frappe.db.get_value('Mobile Exchange',data['exchange'],'settlement_status')=='Settled'
receivable=frappe.db.sql('select sum(outstanding_amount) from `tabSales Invoice` where docstatus=1 and covison_demo_batch=%s',(BATCH,))[0][0]
payable=frappe.db.sql('select sum(outstanding_amount) from `tabPurchase Invoice` where docstatus=1 and covison_demo_batch=%s',(BATCH,))[0][0]
assert abs(flt(receivable)-163000)<0.01,receivable
assert abs(flt(payable)-964954)<0.01,payable
_,margins=profit()
net_sales=sum(r['sold_price'] for r in margins);margin=sum(r['device_margin'] for r in margins)
assert abs(net_sales-572000)<0.01,net_sales
assert abs(margin-98351)<0.01,margin
_,staff_rows=summary({'company':company,'from_date':add_days(nowdate(),-60),'to_date':nowdate()})
assert len(staff_rows)==6
assert abs(sum(r['purchases'] for r in staff_rows)-2900362)<0.01
assert abs(sum(r['sales'] for r in staff_rows)-572000)<0.01
assert abs(sum(r['collected'] for r in staff_rows)-404000)<0.01
assert abs(sum(r['paid'] for r in staff_rows)-1934908)<0.01
_,aged=ageing({'review_only':1})
assert any((r.days_in_stock or 0)>=30 for r in aged)
assert dashboard()['demo_data'] is True
for name in data['sales_invoices']:
    html=frappe.get_print('Sales Invoice',name,print_format='Covison Mobile Receipt')
    assert 'DEMONSTRATION DATA' in html
assert frappe.db.get_value('POS Opening Entry',data['demo_pos_opening'],'status')=='Closed'
assert frappe.db.get_value('Mobile Daily Closing',data['closing_preview'],'docstatus')==0
result={'batch':BATCH,'device_records':36,'synthetic_imeis':46,'available_phones':31,'sold_phones':5,
        'stock_value_pkr':flt(value),'net_sales_pkr':net_sales,'gross_device_margin_pkr':margin,
        'customer_outstanding_pkr':flt(receivable),'supplier_outstanding_pkr':flt(payable),
        'counts':counts,'reports':data['reports'],'balanced_general_ledger':True,
        'pta_verification_not_claimed':True,'demo_receipts_labelled':True,'daily_closing_is_preview':True}
print(json.dumps(result,indent=2,default=str))
