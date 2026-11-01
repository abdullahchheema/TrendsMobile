"""Fill remaining visible sections with linked, explicitly labelled demo examples.

Extends the previous mobile dataset. Existing live POS shifts are never closed.
"""
import json
import frappe
from frappe.utils import flt,nowdate,nowtime,getdate,add_days
from covison_erp.identifiers import valid_imei
from covison_erp.api import create_sale
from covison_erp.workflows import checkout,purchase_bill,post_payment,payment_account
from covison_erp.mobile_shop.doctype.mobile_daily_closing.mobile_daily_closing import balances

BATCH='COV-DEMO-SECTIONS-20261029-B'
KEY='covison_demo_sections_'+BATCH
NOTICE='DEMO: synthetic sample data for workflow testing; no real device inspection, contacts, payments or PTA verification is asserted.'


def tagged(doc):
    doc.covison_demo_batch=BATCH
    if doc.meta.has_field('remarks'):doc.remarks=NOTICE+'\n'+(doc.get('remarks') or '')
    return doc


def person(name):
    found=frappe.db.get_value('Mobile Staff',{'staff_name':name,'enabled':1},'name')
    if not found:frappe.throw('Missing sample staff: '+name)
    return found


def make_imei(number,slot=1):
    body=('99000012' if slot==1 else '99000013')+f'{number:06d}'
    return next(body+str(check) for check in range(10) if valid_imei(body+str(check)))


def new_customer(name,city='Lahore'):
    title='Demo - '+name
    exists=frappe.db.get_value('Customer',{'customer_name':title},'name')
    if exists:return exists
    return tagged(frappe.get_doc(dict(doctype='Customer',customer_name=title,customer_type='Individual',
        customer_group='Individual',territory=city,default_currency='PKR'))).insert().name


def new_supplier(title):
    name='Demo - '+title
    exists=frappe.db.get_value('Supplier',{'supplier_name':name},'name')
    if exists:return exists
    return tagged(frappe.get_doc(dict(doctype='Supplier',supplier_name=name,supplier_type='Company',
        supplier_group='All Supplier Groups',default_currency='PKR'))).insert().name


def bill_for(receipt):
    doc=frappe.get_doc('Purchase Invoice',purchase_bill(receipt))
    tagged(doc);doc.bill_no='DEMO-SECTIONS-'+receipt;doc.bill_date=nowdate();doc.save();doc.submit()
    return doc


def pay(doc,mode,amount,handler,reference):
    result=post_payment(doc,mode,amount,reference='DEMO-'+reference,handled_by=handler)
    if not result.get('covison_demo_batch'):frappe.throw('Demo label was not inherited by a payment.')
    return result.name


def item(code,label,group,rate,**extra):
    if frappe.db.exists('Item',code):return frappe.get_doc('Item',code)
    values=dict(doctype='Item',item_code=code,item_name=label,item_group=group,stock_uom='Nos',
        is_stock_item=1,is_sales_item=1,is_purchase_item=1,description=NOTICE)
    values.update(extra)
    doc=tagged(frappe.get_doc(values));doc.insert()
    tagged(frappe.get_doc(dict(doctype='Item Price',item_code=code,price_list='Standard Selling PKR',
        currency='PKR',price_list_rate=rate))).insert()
    return doc


def add_contact_address(doctype,name,city,index):
    contact=frappe.db.get_value('Dynamic Link',{'parenttype':'Contact','link_doctype':doctype,'link_name':name},'parent')
    if not contact:
        contact=tagged(frappe.get_doc(dict(doctype='Contact',first_name=name.replace('Demo - ','Demo '),
            is_primary_contact=1,email_ids=[dict(email_id=f'demo.{doctype.lower()}.{index}@example.invalid',is_primary=1)],
            links=[dict(link_doctype=doctype,link_name=name)]))).insert().name
    address=frappe.db.get_value('Dynamic Link',{'parenttype':'Address','link_doctype':doctype,'link_name':name},'parent')
    if not address:
        address=tagged(frappe.get_doc(dict(doctype='Address',address_title=name+' - Demo',address_type='Billing',
            address_line1='DEMO ADDRESS — Sample Mobile Market',address_line2='Illustrative address, not a physical contact location',
            city=city,country='Pakistan',is_primary_address=1,links=[dict(link_doctype=doctype,link_name=name)]))).insert().name
    master=frappe.get_doc(doctype,name)
    fields={'customer_primary_contact':contact,'customer_primary_address':address} if doctype=='Customer' else {'supplier_primary_contact':contact,'supplier_primary_address':address}
    for field,value in fields.items():
        if master.meta.has_field(field):master.set(field,value)
    if doctype=='Customer':master.territory=city
    master.save()
    return contact,address


def advance(company,party_type,party,amount,posting_date,mode,handler):
    c=frappe.get_doc('Company',company);bank,_=payment_account(company,mode)
    receive=party_type=='Customer'
    doc=tagged(frappe.get_doc(dict(doctype='Payment Entry',company=company,payment_type='Receive' if receive else 'Pay',
        party_type=party_type,party=party,posting_date=posting_date,mode_of_payment=mode,
        paid_from=c.default_receivable_account if receive else bank,paid_to=bank if receive else c.default_payable_account,
        paid_from_account_currency='PKR',paid_to_account_currency='PKR',source_exchange_rate=1,target_exchange_rate=1,
        paid_amount=amount,received_amount=amount,cost_center=c.cost_center,reference_no='DEMO-ADVANCE-'+party_type,
        reference_date=posting_date,covison_handled_by=handler)))
    doc.insert();doc.submit()
    assert getdate(doc.posting_date)==getdate(posting_date)
    assert abs(flt(doc.unallocated_amount)-amount)<0.01
    return doc.name


def fill():
    if frappe.session.user!='Administrator':frappe.throw('Run this explicit demonstration fill as Administrator.')
    existing=frappe.db.get_global(KEY)
    if existing:
        print(json.dumps({'already_complete':True,'dataset':json.loads(existing)},indent=2));return
    original=frappe.db.get_global('covison_demo_dataset_COV-DEMO-PK-20261029-A')
    if not original or not json.loads(original).get('complete'):frappe.throw('Complete the first demo dataset before filling remaining sections.')
    if frappe.db.count('Mobile Device')!=36 or frappe.db.count('Sales Invoice',{'docstatus':1})!=7:
        frappe.throw('The demo data has changed; review it before adding these exact section examples.')
    company=frappe.db.get_single_value('Global Defaults','default_company')
    warehouse=frappe.db.get_value('Warehouse',{'company':company,'warehouse_name':'Stores','is_group':0},'name')
    people={name:person(name) for name in ('Ahmed Raza','Ali Hassan','Bilal Ahmed','Usman Khan','Muhammad Hamza','Ayesha Malik')}
    buyer=people['Usman Khan'];cashier=people['Ayesha Malik']
    result={'batch':BATCH,'company':company,'new_items':[],'intakes':[],'bills':[],'sales':[],'payments':[],
            'contacts':[],'addresses':[],'warranty_claims':[]}
    # Demo defaults for receipt/settings previews; preserve any nonblank client values.
    settings=frappe.get_single('Mobile Shop Settings')
    for key,value in dict(shop_phone='DEMO contact — no live phone number',
        shop_address='DEMO Store, Sample Mobile Market, Lahore, Pakistan',
        receipt_terms='DEMO checking warranty: 7 days for the sample workflow only. Inspect condition and accessories before accepting a return. Replace with the shop’s agreed terms.',
        urdu_receipt_note='یہ صرف ڈیمو رسید ہے۔ اصل لین دین کے لیے دکان کی تصدیق شدہ تفصیلات درج کریں۔').items():
        if not settings.get(key):settings.set(key,value)
    settings.covison_demo_batch=BATCH;settings.save()
    # Catalog groups and Pakistani customer territories.
    if not frappe.db.exists('Territory','Pakistan'):
        frappe.get_doc(dict(doctype='Territory',territory_name='Pakistan',parent_territory='All Territories',is_group=1)).insert()
    cities=['Lahore','Karachi','Islamabad','Rawalpindi','Faisalabad','Multan']
    for city in cities:
        if not frappe.db.exists('Territory',city):
            frappe.get_doc(dict(doctype='Territory',territory_name=city,parent_territory='Pakistan',is_group=0)).insert()
    regional=new_supplier('Regional Mobile Distributor');accessory_supplier=new_supplier('Accessories and Spare Parts Supply')
    advance_customer=new_customer('Adnan Advance','Karachi');phone_customer=new_customer('Samina Ahmed','Lahore')
    for index,doctype in enumerate(('Customer','Supplier')):
        for number,row in enumerate(frappe.get_all(doctype,filters={'name':['like','Demo - %']},fields=['name'],order_by='name asc'),1):
            contact,address=add_contact_address(doctype,row.name,cities[(number+index)%len(cities)],number)
            result['contacts'].append(contact);result['addresses'].append(address)
    if not frappe.db.exists('Address',{'address_title':'Covison Demo Store'}):
        address=tagged(frappe.get_doc(dict(doctype='Address',address_title='Covison Demo Store',address_type='Shop',
            address_line1='DEMO Store — Sample Mobile Market',address_line2='Illustrative location only',city='Lahore',country='Pakistan',
            links=[dict(link_doctype='Company',link_name=company)]))).insert()
        result['addresses'].append(address.name)
    if not frappe.db.exists('Mobile Staff',{'staff_name':'Danish Iqbal','company':company}):
        tagged(frappe.get_doc(dict(doctype='Mobile Staff',staff_name='Danish Iqbal',company=company,warehouse=warehouse,
            designation='Technician',enabled=1,can_sell=0,can_buy=0,can_handle_payments=0))).insert()
    for row in frappe.get_all('Mobile Staff',filters={'company':company},pluck='name'):
        frappe.db.set_value('Mobile Staff',row,'covison_demo_batch',BATCH,update_modified=False)
    # New, used Android and refurbished phone examples.
    for model in ('Samsung Galaxy A54 5G','Samsung Galaxy S21 5G'):
        if not frappe.db.exists('Mobile Model',model):
            tagged(frappe.get_doc(dict(doctype='Mobile Model',model_name=model,brand='Samsung',description=NOTICE))).insert()
    for model in frappe.get_all('Mobile Model',pluck='name'):
        doc=frappe.get_doc('Mobile Model',model)
        if not doc.description:doc.description='Demo catalog model for mobile-shop workflow testing.'
        doc.covison_demo_batch=BATCH;doc.save()
    phone_specs=[('DEMO-NEW-A54-128','New Samsung Galaxy A54 5G 128GB Black','New Phones','Samsung Galaxy A54 5G','Samsung','128GB','Black','New',78000,92000,85000),
                 ('DEMO-USED-S21-256','Used Samsung Galaxy S21 5G 256GB Violet','Used Phones','Samsung Galaxy S21 5G','Samsung','256GB','Violet','Used',65000,78000,70000),
                 ('DEMO-REF-IP12PRO-128','Refurbished iPhone 12 Pro 128GB Gold','Refurbished Phones','Apple iPhone 12 Pro','Apple','128GB','Gold','Refurbished',72000,86000,79000)]
    new_devices=[]
    for index,(code,label,group,model,brand,storage,color,kind,cost,asking,minimum) in enumerate(phone_specs,1):
        product=item(code,label,group,asking,brand=brand,covison_is_mobile=1,covison_model=model,covison_storage=storage,
            covison_ram='8GB' if brand=='Samsung' else '6GB',covison_color=color,has_serial_no=1,use_serial_no_wise_valuation=1,serial_no_series='COV-PHN-.######')
        result['new_items'].append(product.name)
        intake=tagged(frappe.get_doc(dict(doctype='Mobile Intake',company=company,supplier=regional,purchase_source='Distributor',
            posting_date=nowdate(),warehouse=warehouse,item_code=code,purchase_price=cost,imei_1=make_imei(index),imei_2=make_imei(index,2),
            device_type=kind,condition_grade='Excellent' if kind=='New' else 'Very Good',inspection_status='Passed',inspection_date=nowdate(),
            inspection_notes=NOTICE+' Simulated intake inspection.',asking_price=asking,minimum_price=minimum,wholesale_price=minimum+1000,
            warranty_days=7,warranty_terms='DEMO shop checking warranty only.',pta_status='Not Checked',covison_purchased_by=buyer)))
        intake.insert();intake.submit();intake.reload();result['intakes'].append(intake.name);new_devices.append(intake.mobile_device)
        bill=bill_for(intake.purchase_receipt);result['bills'].append(bill.name)
        result['payments'].append(pay(bill,'Bank Transfer',(48000,32000,20000)[index-1],cashier,'PHONE-BUY-'+str(index)))
    result['new_devices']=new_devices
    # Bulk accessories and spare parts, including internal barcode examples.
    accessories=[('DEMO-CHARGER-20W','Demo 20W USB-C Charger','Accessories',10,1200,2300),
                 ('DEMO-CABLE-USBC','Demo USB-C Charging Cable','Accessories',10,350,650),
                 ('DEMO-CASE-IP13','Demo iPhone 13 Protective Case','Accessories',10,500,1200),
                 ('DEMO-BATTERY-IP11','Demo iPhone 11 Replacement Battery','Spare Parts',5,2200,3500)]
    for code,label,group,qty,cost,rate in accessories:
        product=item(code,label,group,rate,barcodes=[dict(barcode=code,uom='Nos')]);result['new_items'].append(product.name)
    po=tagged(frappe.get_doc(dict(doctype='Purchase Order',company=company,supplier=accessory_supplier,currency='PKR',conversion_rate=1,
        transaction_date=nowdate(),schedule_date=nowdate(),covison_purchased_by=buyer,taxes_and_charges='',taxes=[],
        items=[dict(item_code=code,qty=qty,rate=cost,warehouse=warehouse,schedule_date=nowdate()) for code,_,_,qty,cost,_ in accessories])))
    po.insert();po.submit();result['accessory_order']=po.name
    from erpnext.buying.doctype.purchase_order.purchase_order import make_purchase_receipt
    receipt=make_purchase_receipt(po.name);receipt.covison_purchased_by=buyer;receipt.insert();receipt.submit()
    assert receipt.covison_demo_batch
    result['accessory_receipt']=receipt.name
    bill=bill_for(receipt.name);result['bills'].append(bill.name)
    result['payments'].append(pay(bill,'Cash',10000,cashier,'ACCESSORY-BUY'))
    sale=frappe.get_doc('Sales Invoice',create_sale(new_devices[0],phone_customer,92000,sold_by=people['Bilal Ahmed'])['name'])
    assert sale.covison_demo_batch
    result['sales'].append(sale.name)
    checked=checkout(sale.name,payments=[dict(mode_of_payment='Cash',amount=50000)],collected_by=people['Muhammad Hamza'])
    result['payments']+=checked['payments']
    accessory_customer=frappe.db.get_value('Customer',{'customer_name':'Demo - Hassan Ali'},'name')
    sale=frappe.get_doc(dict(doctype='Sales Invoice',company=company,customer=accessory_customer,currency='PKR',conversion_rate=1,
        update_stock=1,ignore_pricing_rule=1,selling_price_list='Standard Selling PKR',taxes_and_charges='',taxes=[],
        covison_sold_by=people['Ali Hassan'],items=[dict(item_code=code,qty=1,rate=rate,warehouse=warehouse) for code,_,_,_,_,rate in accessories[:3]]))
    sale.insert();sale.submit();assert sale.covison_demo_batch
    result['sales'].append(sale.name);result['payments'].append(pay(sale,'Cash',4150,cashier,'ACCESSORY-SALE'))
    # An additional stock location and exact-phone transfer exercise warehouse filters.
    counter=frappe.db.get_value('Warehouse',{'warehouse_name':'Demo Display Counter','company':company},'name')
    if not counter:
        parent=frappe.db.get_value('Warehouse',{'company':company,'warehouse_name':'All Warehouses','is_group':1},'name')
        counter=frappe.get_doc(dict(doctype='Warehouse',warehouse_name='Demo Display Counter',company=company,parent_warehouse=parent,is_group=0)).insert().name
    phone=frappe.get_doc('Mobile Device',new_devices[1])
    transfer=frappe.get_doc(dict(doctype='Stock Entry',company=company,stock_entry_type='Material Transfer',
        items=[dict(item_code=phone.item_code,qty=1,s_warehouse=warehouse,t_warehouse=counter,serial_no=phone.serial_no,use_serial_batch_fields=1)]))
    transfer.insert();transfer.submit();assert transfer.covison_demo_batch
    result['stock_transfer']=transfer.name;result['counter_warehouse']=counter
    # Completed historical closing is dated before every current open POS shift.
    shifts=frappe.get_all('POS Opening Entry',filters={'docstatus':1,'status':'Open','company':company},fields=['period_start_date'])
    close_date=min([getdate(add_days(nowdate(),-1))]+[getdate(add_days(getdate(r.period_start_date),-1)) for r in shifts])
    result['customer_advance']=advance(company,'Customer',advance_customer,5000,close_date,'Cash',cashier)
    result['supplier_advance']=advance(company,'Supplier',regional,10000,nowdate(),'Bank Transfer',cashier)
    closing_rows=balances(company,close_date)
    closing=tagged(frappe.get_doc(dict(doctype='Mobile Daily Closing',company=company,closing_date=close_date,covison_closed_by=cashier,
        notes=NOTICE+' Historical closing before the shop’s open POS shift.',
        balances=[dict(row,counted_balance=row['expected_balance'],notes='Simulated matching count') for row in closing_rows])))
    closing.insert();closing.submit();result['submitted_closing']=closing.name
    # Filled inspection fields are simulated; PTA is deliberately never claimed as verified.
    for index,name in enumerate(frappe.get_all('Mobile Device',filters={'covison_demo_batch':['is','set']},pluck='name',order_by='name asc'),1):
        doc=frappe.get_doc('Mobile Device',name)
        repair=index%11==0 and doc.status=='Available'
        doc.manufacturer_serial='DEMO-SN-'+doc.name
        doc.regional_version='Demo international variant';doc.market_description='Demo stock for screen and workflow testing'
        doc.battery_health_checked=1;doc.battery_health=85+(index%15)
        doc.inspection_status='Repair Needed' if repair else 'Passed';doc.inspection_date=nowdate()
        doc.display_condition='Replaced / Working' if index%7==0 else 'Original / Working'
        doc.biometrics='Working';doc.camera_test='Failed' if repair else 'Passed';doc.charging_test='Passed'
        doc.audio_test='Passed';doc.network_test='Not Checked';doc.carrier_lock='Factory Unlocked';doc.activation_lock='Off'
        doc.sim_configuration='Physical SIM + eSIM';doc.included_accessories='DEMO charging cable; box where listed'
        doc.repair_history='DEMO: camera assessment assigned to Danish Iqbal' if repair else 'DEMO: no repair history recorded'
        doc.inspection_notes=NOTICE+' Inspection results are simulated, not a physical assessment.'
        doc.disclosed_faults='DEMO: sample camera issue awaiting assessment' if repair else 'DEMO: simulated cosmetic wear only'
        doc.pta_status='Not Checked';doc.pta_status_2='Not Checked';doc.pta_notes='DEMO: no official DIRBS lookup performed.'
        doc.save()
    sold=frappe.get_all('Mobile Device',filters={'status':'Sold','covison_demo_batch':['is','set']},fields=['name','customer','sales_invoice'],order_by='name asc')
    for index,(phone,status) in enumerate(zip(sold[:2],('Open','Work In Progress')),1):
        claim=tagged(frappe.get_doc(dict(doctype='Warranty Claim',company=company,customer=phone.customer,serial_no=phone.name,
            covison_device=phone.name,covison_sales_invoice=phone.sales_invoice,status=status,complaint_date=nowdate(),
            complaint='DEMO charging / camera issue for assessment',covison_received_condition='DEMO same sample condition; cable received',
            covison_resolution='DEMO technician Danish Iqbal: assessment pending, no final resolution agreed.')))
        claim.insert();result['warranty_claims'].append(claim.name)
    # A fully prepared draft lets the client test receiving without pre-posting another phone.
    draft=tagged(frappe.get_doc(dict(doctype='Mobile Intake',company=company,supplier=regional,purchase_source='Distributor',
        posting_date=nowdate(),warehouse=warehouse,item_code=phone_specs[1][0],purchase_price=64000,imei_1=make_imei(4),imei_2=make_imei(4,2),
        device_type='Used',condition_grade='Good',inspection_status='Passed',inspection_date=nowdate(),inspection_notes=NOTICE+' Draft: submit to receive stock.',
        asking_price=77000,minimum_price=70000,warranty_days=7,covison_purchased_by=buyer)))
    draft.insert();result['draft_intake']=draft.name
    for name in frappe.get_all('Mobile Daily Closing',filters={'docstatus':0,'covison_demo_batch':['is','set']},pluck='name'):
        preview=frappe.get_doc('Mobile Daily Closing',name);preview.load_balances()
        for row in preview.balances:row.counted_balance=row.expected_balance
        preview.save()
    # Reconcile quantities and ledgers before committing the new examples.
    phone_qty,phone_value=frappe.db.sql("select sum(actual_qty),sum(stock_value) from `tabBin` where item_code in (select name from `tabItem` where covison_is_mobile=1)")[0]
    assert flt(phone_qty)==33 and abs(flt(phone_value)-2563713)<0.01,(phone_qty,phone_value)
    total_value=frappe.db.sql('select sum(stock_value) from `tabBin`')[0][0]
    assert abs(flt(total_value)-2593163)<0.01,total_value
    debit,credit=frappe.db.sql('select sum(debit),sum(credit) from `tabGL Entry` where is_cancelled=0')[0]
    assert abs(flt(debit)-flt(credit))<0.01
    result['complete']=True;result['phone_stock_qty']=33;result['phone_stock_value']=2563713;result['total_stock_value']=2593163
    frappe.db.set_global(KEY,json.dumps(result,default=str));frappe.db.commit()
    print(json.dumps(result,indent=2,default=str))

try:fill()
except Exception:
    frappe.db.rollback();raise
