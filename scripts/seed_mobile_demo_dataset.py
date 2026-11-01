"""Explicit, idempotent demo-data seed authorized by the project owner.

Posts native ERP documents and synthetic identifiers. Never runs on app install.
An initial guard requires the known 30-phone demo stock and no sales/payments.
"""
import json,math
from pathlib import Path
import frappe
from frappe.utils import add_days,flt,nowdate,now_datetime
from covison_erp.identifiers import valid_imei
from covison_erp.api import create_sale
from covison_erp.devices import serial_cost
from covison_erp.workflows import checkout,purchase_bill,post_payment,settle_exchange,return_phone,device_expense,payment_account

BATCH='COV-DEMO-PK-20261029-A'
STATE_KEY='covison_demo_dataset_'+BATCH
NOTICE='DEMO DATA: synthetic IMEIs and sample financial transactions. No actual devices, money movement or PTA verification is asserted.'
REPORTS=('Mobile Inventory','Mobile Stock Ageing','Device Profit','Mobile Device Economics','Staff Activity','Staff Summary',
         'Purchase Register','Sales Register','Payment Ledger','Accounts Receivable','Accounts Payable','General Ledger','Stock Balance')


def state():
    value=frappe.db.get_global(STATE_KEY)
    return json.loads(value) if value else None


def save_state(data,commit=False):
    frappe.db.set_global(STATE_KEY,json.dumps(data,default=str))
    if commit:frappe.db.commit()


def tagged(doc):
    doc.covison_demo_batch=BATCH
    if doc.meta.has_field('remarks'):
        doc.remarks=NOTICE+'\n'+(doc.get('remarks') or '')
    return doc


def tag_existing(doc):
    # Provenance metadata only: never rewrite posted quantities, prices or totals.
    doc.db_set('covison_demo_batch',BATCH,update_modified=False)
    return doc


def imei(number,slot=1):
    body=('99000010' if slot==1 else '99000011')+f'{number:06d}'
    return next(body+str(check) for check in range(10) if valid_imei(body+str(check)))


def staff(name):
    found=frappe.db.get_value('Mobile Staff',{'staff_name':name,'enabled':1},'name')
    if not found:frappe.throw('Add the sample staff member first: '+name)
    return found


def customer(name):
    title='Demo - '+name
    found=frappe.db.get_value('Customer',{'customer_name':title},'name')
    if found:return found
    return tagged(frappe.get_doc(dict(doctype='Customer',customer_name=title,customer_type='Individual',
        customer_group='Individual',territory='All Territories',default_currency='PKR'))).insert().name


def supplier(name):
    title='Demo - '+name
    found=frappe.db.get_value('Supplier',{'supplier_name':title},'name')
    if found:return found
    return tagged(frappe.get_doc(dict(doctype='Supplier',supplier_name=title,supplier_type='Individual',
        supplier_group='All Supplier Groups',default_currency='PKR'))).insert().name


def payment(invoice,mode,amount,handler,key):
    entry=post_payment(invoice,mode,amount,reference='DEMO-'+key,handled_by=handler)
    tag_existing(entry)
    return entry.name


def native_bill(receipt):
    doc=frappe.get_doc('Purchase Invoice',purchase_bill(receipt))
    tagged(doc);doc.bill_no='DEMO-'+receipt;doc.bill_date=nowdate();doc.save();doc.submit()
    return doc


def available(item_code,original_only=False):
    query={'item_code':item_code,'status':'Available','covison_demo_batch':BATCH}
    if original_only:query['name']=['<=','COV-PHN-000030']
    names=frappe.get_all('Mobile Device',filters=query,pluck='name',order_by='name asc')
    if not names:frappe.throw('No demo phone available for '+item_code)
    return frappe.get_doc('Mobile Device',names[0])


def sell(phone,buyer,rate,seller,collector,payments=None,credit=False):
    doc=frappe.get_doc('Sales Invoice',create_sale(phone.name,buyer,rate,sold_by=seller)['name'])
    tagged(doc);doc.covison_collected_by=collector
    doc.covison_receipt_terms='DEMO checking warranty: sample terms only; replace with the shop’s agreed terms.'
    doc.save()
    result=checkout(doc.name,payments=[] if credit else payments or [],collected_by=collector)
    for name in result['payments']:tag_existing(frappe.get_doc('Payment Entry',name))
    return frappe.get_doc('Sales Invoice',doc.name)


def seed_core():
    company=frappe.db.get_single_value('Global Defaults','default_company')
    c=frappe.get_doc('Company',company)
    if c.default_currency!='PKR':frappe.throw('The demo dataset requires PKR.')
    warehouse=frappe.db.get_value('Warehouse',{'company':company,'warehouse_name':'Stores','is_group':0},'name')
    phones=frappe.get_all('Mobile Device',fields=['name','item_code','imei_1','status','purchase_cost','purchase_receipt','supplier'],order_by='name asc')
    if len(phones)!=30 or any(d.imei_1 or d.status!='Available' for d in phones):
        frappe.throw('The initial seed requires the original 30 unsold demo phones with blank IMEIs. No data was changed.')
    for dt in ('Sales Invoice','POS Invoice','Purchase Invoice','Payment Entry','Journal Entry'):
        if frappe.db.count(dt,{'docstatus':1}):frappe.throw('Existing posted transactions require manual review before this demo seed: '+dt)
    receipts=frappe.get_all('Purchase Receipt',filters={'docstatus':1,'company':company},fields=['name','grand_total','currency'],order_by='name asc')
    if len(receipts)!=5 or sum(flt(r.grand_total) for r in receipts)!=2441862 or any(r.currency!='PKR' for r in receipts):
        frappe.throw('Existing purchase receipts do not match the approved demo inventory.')
    people={name:staff(name) for name in ('Ahmed Raza','Ali Hassan','Bilal Ahmed','Usman Khan','Muhammad Hamza','Ayesha Malik')}
    buyer=people['Usman Khan'];manager=people['Ahmed Raza'];cashier=people['Muhammad Hamza'];cashier2=people['Ayesha Malik']
    result={'batch':BATCH,'company':company,'warehouse':warehouse,'core_complete':False,'generated_on':nowdate(),'started_at':str(now_datetime()),
        'repost_baseline':frappe.get_all('Repost Item Valuation',pluck='name'),
        'original_devices':[],'intakes':[],'purchase_invoices':[],'sales_invoices':[],'payments':[],
        'expenses':[],'exchange':None,'opening_journal':None,'demo_pos_opening':None,'demo_pos_closing':None,'closing_preview':None}
    # The synthetic range is chosen for this demo only, not claimed as an officially reserved TAC.
    for number,row in enumerate(phones,1):
        doc=frappe.get_doc('Mobile Device',row.name)
        doc.imei_1=imei(number)
        if number%3==0:doc.imei_2=imei(number,2)
        doc.covison_demo_batch=BATCH
        doc.asking_price=math.ceil(flt(row.purchase_cost)*1.18/500)*500
        doc.minimum_price=math.ceil(flt(row.purchase_cost)*1.08/500)*500
        doc.wholesale_price=math.ceil(flt(row.purchase_cost)*1.12/500)*500
        doc.warranty_days=7
        doc.warranty_terms='DEMO sample checking warranty; not actual shop terms.'
        doc.pta_status='Not Checked';doc.pta_verified_on=None;doc.pta_verified_by=None;doc.pta_evidence=None
        doc.pta_status_2='Not Checked';doc.pta_verified_on_2=None;doc.pta_evidence_2=None
        doc.inspection_notes=NOTICE+'\nPhysical inspection remains pending. '+(doc.inspection_notes or '')
        doc.save();result['original_devices'].append(doc.name)
    # Sample starting funds prevent artificial negative wallet/cash balances.
    funding=[('Cash',2000000),('Bank Transfer',3000000),('Easypaisa',500000),('JazzCash',500000)]
    rows=[]
    for mode,amount in funding:
        account,_=payment_account(company,mode)
        rows.append(dict(account=account,debit_in_account_currency=amount))
    equity=frappe.db.get_value('Account',{'company':company,'account_name':'Common Stock','is_group':0},'name')
    if not equity:frappe.throw('Configure an equity account for the demo starting funds.')
    rows.append(dict(account=equity,credit_in_account_currency=sum(amount for _,amount in funding)))
    opening=tagged(frappe.get_doc(dict(doctype='Journal Entry',company=company,posting_date=nowdate(),
        voucher_type='Journal Entry',user_remark=NOTICE+' DEMO starting funds',accounts=rows)))
    opening.insert();opening.submit();result['opening_journal']=opening.name
    # Existing receipts become fully billed without receiving their 30 phones again.
    old_payments=[('Bank Transfer',None),('Cash',200000),('Bank Transfer',300000),('Cash',350000),('Bank Transfer',500000)]
    for index,receipt in enumerate(receipts):
        pr=tag_existing(frappe.get_doc('Purchase Receipt',receipt.name))
        pr.db_set({'covison_purchased_by':buyer,'covison_purchaser_name':'Usman Khan'},update_modified=False)
        for row in pr.items:
            if row.purchase_order:
                order=tag_existing(frappe.get_doc('Purchase Order',row.purchase_order))
                order.db_set({'covison_purchased_by':buyer,'covison_purchaser_name':'Usman Khan'},update_modified=False)
        bill=native_bill(pr.name);result['purchase_invoices'].append(bill.name)
        mode,amount=old_payments[index]
        result['payments'].append(payment(bill,mode,amount or bill.grand_total,cashier,'BUY-OLD-'+str(index+1)))
    intake_specs=[('USED-IP11-128',43000,52000,47000,-45,buyer),
                  ('USED-IP12-128',59000,69000,64000,-21,buyer),
                  ('USED-IP13-256',83500,96500,90000,-7,buyer),
                  ('USED-IP14-128',98000,115000,106000,-3,buyer),
                  ('USED-IP15-128',125000,149000,136000,-1,manager)]
    new_phones=[]
    for index,(item,cost,asking,minimum,age,purchaser) in enumerate(intake_specs,31):
        doc=tagged(frappe.get_doc(dict(doctype='Mobile Intake',company=company,
            supplier=phones[0].supplier,purchase_source='Distributor',posting_date=add_days(nowdate(),age),
            warehouse=warehouse,item_code=item,purchase_price=cost,imei_1=imei(index),
            device_type='Used',condition_grade='Very Good',inspection_status='Passed',inspection_date=add_days(nowdate(),age),
            inspection_notes=NOTICE+' Simulated inspection for demonstration only.',pta_status='Not Checked',
            asking_price=asking,minimum_price=minimum,wholesale_price=minimum+1000,warranty_days=7,
            warranty_terms='DEMO sample checking warranty.',covison_purchased_by=purchaser)))
        doc.insert();doc.submit();doc.reload()
        phone=tag_existing(frappe.get_doc('Mobile Device',doc.mobile_device));new_phones.append(phone.name)
        result['intakes'].append(doc.name)
        tag_existing(frappe.get_doc('Purchase Receipt',doc.purchase_receipt))
        bill=native_bill(doc.purchase_receipt);result['purchase_invoices'].append(bill.name)
        mode,amount=(('Cash',43000),('Easypaisa',40000),('Bank Transfer',83500),('Cash',50000),('Bank Transfer',80000))[index-31]
        result['payments'].append(payment(bill,mode,amount,cashier2,'BUY-NEW-'+str(index)))
    result['new_devices']=new_phones
    # Sample customer balances: fully paid, split payment, partial payment and credit.
    cash_sale=sell(available('USED-IP11-64',True),customer('Hassan Ali'),45000,people['Ali Hassan'],cashier,
        [dict(mode_of_payment='Cash',amount=45000)])
    result['sales_invoices'].append(cash_sale.name)
    split=sell(available('USED-IP12-128',True),customer('Zain Ahmed'),69000,people['Bilal Ahmed'],cashier2,
        [dict(mode_of_payment='Cash',amount=30000),dict(mode_of_payment='JazzCash',amount=39000,reference='DEMO-JC-SALE-02')])
    result['sales_invoices'].append(split.name)
    partial_phone=available('USED-IP13-256',True)
    partial=sell(partial_phone,customer('Bilal Sheikh'),97000,people['Ali Hassan'],cashier,
        [dict(mode_of_payment='Easypaisa',amount=60000,reference='DEMO-EP-SALE-03')])
    result['sales_invoices'].append(partial.name)
    credit=sell(available('USED-IP14-256',True),customer('Faizan Malik'),126000,people['Bilal Ahmed'],None,credit=True)
    result['sales_invoices'].append(credit.name)
    # A separate demo till preserves the shop's existing open POS shift.
    profile=frappe.copy_doc(frappe.get_doc('POS Profile','Covison Mobile Shop'),ignore_no_copy=False)
    profile.name='Covison Demo POS';profile.covison_demo_batch=BATCH
    profile.set('applicable_for_users',[])
    profile.insert(set_name='Covison Demo POS')
    pos_open=tagged(frappe.get_doc(dict(doctype='POS Opening Entry',company=company,pos_profile=profile.name,
        user='Administrator',period_start_date=now_datetime(),balance_details=[dict(mode_of_payment='Cash',opening_amount=0)])))
    pos_open.insert();pos_open.submit();result['demo_pos_opening']=pos_open.name
    pos_phone=available('USED-IP15-PRO-256',True)
    pos_sale=frappe.get_doc('Sales Invoice',create_sale(pos_phone.name,customer('Aamir Qureshi'),198000,sold_by=manager)['name'])
    tagged(pos_sale);pos_sale.is_pos=1;pos_sale.is_created_using_pos=1;pos_sale.pos_profile=profile.name
    pos_sale.covison_collected_by=cashier2
    pos_sale.append('payments',dict(mode_of_payment='Card',amount=198000))
    pos_sale.save();pos_sale.submit();result['sales_invoices'].append(pos_sale.name)
    # Customer trade-in with actual native offset and a sample difference receipt.
    trade_customer=customer('Umair Farooq');trade_supplier=supplier('Umair Farooq Trade-In')
    trade=tagged(frappe.get_doc(dict(doctype='Mobile Intake',company=company,supplier=trade_supplier,
        purchase_source='Customer Trade-In',trade_in_customer=trade_customer,posting_date=nowdate(),warehouse=warehouse,
        item_code='USED-IP12-64',purchase_price=50000,imei_1=imei(36),condition_grade='Good',inspection_status='Passed',
        inspection_date=nowdate(),inspection_notes=NOTICE+' Simulated trade-in assessment.',pta_status='Not Checked',
        asking_price=62000,minimum_price=55000,warranty_days=7,covison_purchased_by=buyer)))
    trade.insert();trade.submit();trade.reload();result['intakes'].append(trade.name)
    tag_existing(frappe.get_doc('Mobile Device',trade.mobile_device));result['new_devices'].append(trade.mobile_device)
    tag_existing(frappe.get_doc('Purchase Receipt',trade.purchase_receipt))
    trade_bill=native_bill(trade.purchase_receipt);result['purchase_invoices'].append(trade_bill.name)
    trade_sale=sell(available('USED-IP13-128',True),trade_customer,82000,people['Ali Hassan'],cashier,credit=True)
    result['sales_invoices'].append(trade_sale.name)
    exchange=tagged(frappe.get_doc(dict(doctype='Mobile Exchange',company=company,customer=trade_customer,
        purchase_receipt=trade.purchase_receipt,purchase_invoice=trade_bill.name,sales_invoice=trade_sale.name,notes=NOTICE)))
    exchange.insert()
    settled=settle_exchange(exchange.name,'Easypaisa',reference='DEMO-EXCHANGE-01',handled_by=cashier)
    for dt,key in (('Journal Entry','journal_entry'),('Payment Entry','payment_entry')):
        if settled.get(key):tag_existing(frappe.get_doc(dt,settled[key]))
    result['exchange']=exchange.name
    # Return/refund the first cash sale and leave a warranty example linked to it.
    returned=frappe.get_doc('Sales Invoice',return_phone(cash_sale.name,cash_sale.items[0].covison_device,
        'DEMO: same sample condition, all listed accessories returned','DEMO customer return'))
    tagged(returned);returned.covison_sold_by=people['Ali Hassan'];returned.covison_collected_by=cashier
    returned.save();returned.submit();result['return_invoice']=returned.name
    cash_sale.reload();returned.reload()
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    refund_target=returned if abs(flt(returned.outstanding_amount))>0.005 else cash_sale
    refund=get_payment_entry('Sales Invoice',refund_target.name,bank_account=payment_account(company,'Cash')[0])
    tagged(refund);refund.mode_of_payment='Cash';refund.covison_handled_by=cashier
    refund.insert();refund.submit();result['refund']=refund.name
    claim=tagged(frappe.get_doc(dict(doctype='Warranty Claim',company=company,customer=cash_sale.customer,
        serial_no=cash_sale.items[0].covison_device,covison_device=cash_sale.items[0].covison_device,
        covison_sales_invoice=cash_sale.name,covison_return_invoice=returned.name,
        complaint='DEMO checking-warranty return example',status='Closed',
        covison_received_condition='DEMO same condition',covison_resolution='DEMO return accepted and cash refunded.')))
    claim.insert();result['warranty_claim']=claim.name
    # Device repairs are expensed separately from native stock valuation.
    for index,(device,amount,mode) in enumerate(((new_phones[0],2500,'Cash'),(partial_phone.name,2000,'Easypaisa')),1):
        expense=frappe.get_doc('Purchase Invoice',device_expense(device,phones[0].supplier,amount,'DEMO repair / service cost'))
        tagged(expense);expense.covison_purchased_by=buyer;expense.save();expense.submit()
        result['expenses'].append(expense.name)
        result['payments'].append(payment(expense,mode,amount,cashier2,'REPAIR-'+str(index)))
    # One open replenishment order illustrates purchases awaiting receipt.
    po=tagged(frappe.get_doc(dict(doctype='Purchase Order',company=company,supplier=phones[0].supplier,
        transaction_date=nowdate(),schedule_date=add_days(nowdate(),5),currency='PKR',conversion_rate=1,
        covison_purchased_by=buyer,taxes_and_charges='',taxes=[],
        items=[dict(item_code='USED-IP14-256',qty=2,rate=109000,schedule_date=add_days(nowdate(),5),warehouse=warehouse)])))
    po.insert();po.submit();result['replenishment_order']=po.name
    # Native POS can show default model prices; exact units retain individual asking/minimum prices.
    for item in sorted({p.item_code for p in phones}):
        if not frappe.db.exists('Item Price',{'item_code':item,'price_list':'Standard Selling PKR','currency':'PKR'}):
            price=frappe.db.get_value('Mobile Device',{'item_code':item,'covison_demo_batch':BATCH},'asking_price')
            doc=frappe.get_doc(dict(doctype='Item Price',item_code=item,price_list='Standard Selling PKR',currency='PKR',price_list_rate=price))
            if doc.meta.has_field('covison_demo_batch'):tagged(doc)
            doc.insert()
    # Reconcile exact serial-level stock and native GL before committing the core dataset.
    qty,value=frappe.db.sql("select sum(actual_qty),sum(stock_value) from `tabBin` where item_code in (select name from `tabItem` where covison_is_mobile=1)")[0]
    assert flt(qty)==31,(qty,value)
    # Backdated receipts queue native revaluation of later stock entries. Verify
    # exact serial costs now, then reconcile Bin/GL through native reposts after commit.
    active=frappe.get_all('Mobile Device',filters={'status':'Available','covison_demo_batch':BATCH},pluck='serial_no')
    assert abs(sum(serial_cost(serial) for serial in active)-2426713)<0.01
    debit,credit=frappe.db.sql('select sum(debit),sum(credit) from `tabGL Entry` where is_cancelled=0')[0]
    assert abs(flt(debit)-flt(credit))<0.01
    result['expected_stock_qty']=31;result['expected_stock_value']=2426713
    result['core_complete']=True
    save_state(result,commit=True)
    return result


def finish(data):
    if not data.get('stock_reconciled'):
        from erpnext.stock.doctype.repost_item_valuation.repost_item_valuation import execute_reposting_entry
        vouchers=[]
        for dt in ('Purchase Receipt','Purchase Invoice','Sales Invoice'):
            vouchers+=frappe.get_all(dt,filters={'covison_demo_batch':BATCH,'docstatus':1},pluck='name')
        pending=frappe.get_all('Repost Item Valuation',filters={'docstatus':1,'company':data['company'],
            'status':['in',['Queued','In Progress']]},fields=['name','voucher_no','item_code','warehouse','creation'],
            order_by='posting_date asc, creation asc')
        demo_items={frappe.db.get_value('Mobile Device',name,'item_code') for name in data['new_devices']}
        baseline=set(data.get('repost_baseline') or [])
        from frappe.utils import get_datetime
        started=get_datetime(data.get('started_at') or (data['generated_on']+' 00:00:00'))
        reposts=[r for r in pending if r.name not in baseline and (r.voucher_no in vouchers or
            (r.item_code in demo_items and r.warehouse==data['warehouse'] and r.creation>=started))]
        prior_flag=frappe.flags.through_repost_item_valuation
        try:
            for row in reposts:
                execute_reposting_entry(row.name)
                status=frappe.db.get_value('Repost Item Valuation',row.name,'status')
                if status not in ('Completed','Skipped'):
                    frappe.throw('Review native demo stock revaluation '+row.name+': '+str(status))
        finally:
            frappe.flags.through_repost_item_valuation=prior_flag
        qty,value=frappe.db.sql("select sum(actual_qty),sum(stock_value) from `tabBin` where item_code in (select name from `tabItem` where covison_is_mobile=1)")[0]
        assert flt(qty)==31 and abs(flt(value)-2426713)<0.01,(qty,value)
        data['stock_reconciled']=True;data['stock_reposts']=[r.name for r in reposts];save_state(data,commit=True)
    if not data.get('demo_pos_closing'):
        from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import make_closing_entry_from_opening
        opening=frappe.get_doc('POS Opening Entry',data['demo_pos_opening'])
        closing=make_closing_entry_from_opening(opening)
        tagged(closing)
        for row in closing.payment_reconciliation:row.closing_amount=row.expected_amount
        closing.submit()
        data['demo_pos_closing']=closing.name;save_state(data,commit=True)
    if not data.get('closing_preview'):
        from covison_erp.mobile_shop.doctype.mobile_daily_closing.mobile_daily_closing import balances
        rows=balances(data['company'],nowdate())
        closing=tagged(frappe.get_doc(dict(doctype='Mobile Daily Closing',company=data['company'],closing_date=nowdate(),
            covison_closed_by=staff('Muhammad Hamza'),notes=NOTICE+' Draft preview only; the shop’s existing POS shift remains open.',
            balances=[dict(row,counted_balance=row['expected_balance'],notes='DEMO reconciliation preview') for row in rows])))
        closing.insert();data['closing_preview']=closing.name;save_state(data,commit=True)
    # These reports remain live and recalculate from the native transaction ledgers.
    from frappe.desk.query_report import run
    filters={'company':data['company'],'from_date':add_days(nowdate(),-60),'to_date':nowdate(),
             'report_date':nowdate(),'ageing_based_on':'Posting Date','range':'30, 60, 90, 120'}
    reports={}
    for name in REPORTS:
        result=run(name,filters=filters,ignore_prepared_report=True)
        reports[name]={'rows':len(result.get('result') or []),'columns':len(result.get('columns') or [])}
    data['reports']=reports
    data['payments']=frappe.get_all('Payment Entry',filters={'covison_demo_batch':BATCH,'docstatus':1},pluck='name',order_by='name asc')
    data['posted_counts']={dt:frappe.db.count(dt,{'covison_demo_batch':BATCH,'docstatus':1}) for dt in ('Mobile Intake','Purchase Receipt','Purchase Invoice','Purchase Order','Sales Invoice','Payment Entry','Journal Entry','Mobile Exchange')}
    data['complete']=True;save_state(data,commit=True)
    print(json.dumps(data,indent=2,default=str))


def seed():
    if frappe.session.user!='Administrator':frappe.throw('Run this explicit sample seed as Administrator.')
    if not frappe.get_meta('Mobile Device').has_field('covison_demo_batch'):
        frappe.throw('Migrate the demo provenance fields before running this seed.')
    data=state()
    if data and data.get('complete'):
        print(json.dumps({'already_complete':True,'dataset':data},indent=2));return
    try:
        data=data or seed_core()
        finish(data)
    except Exception:
        frappe.db.rollback()
        raise

seed()
