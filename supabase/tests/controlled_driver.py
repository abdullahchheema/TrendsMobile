"""Run inside the isolated demo container using real HTTP and Supabase logins."""
import http.cookiejar,json,re,uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote,urlencode,urlsplit
from urllib.request import Request,build_opener,HTTPCookieProcessor
from django.db import connection
from django.db.models import Sum
from django.utils import timezone
from shop.models import *
from shop import services,reports,pricing

context=json.loads(Path('/tmp/demo-context.json').read_text())
assert connection.settings_dict['NAME']==context['database'] and context['database'].startswith('covison_demo_')
run_id=context['run_id'];checks=[];records={};findings=[]
today=str(timezone.localdate())
branch=Branch.objects.get(code='PK-001')
store=Warehouse.objects.get(code='Stores - TM')
counter=Warehouse.objects.get(code='Demo Display Counter - TM')
buyer=Staff.objects.get(name='Usman Khan');seller=Staff.objects.get(name='Ali Hassan');cashier=Staff.objects.get(name='Ayesha Malik')
customer=Party.objects.get(kind='Customer',name='Demo - Hassan Ali')
supplier=Party.objects.filter(kind='Supplier',enabled=True).order_by('name').first()
phone_product=Product.objects.get(code='DEMO-USED-S21-256')
service_product=Product.objects.get(code='COV-DEVICE-SERVICE')
accessory=Product.objects.get(code='DEMO-CHARGER-20W')
def check(label,condition,**evidence):
    checks.append(dict(check=label,passed=bool(condition),**evidence))
    Path('/tmp/controlled-demo-progress.json').write_text(json.dumps(dict(checks=checks,records=records,findings=findings),indent=2,default=str))
    if not condition:raise AssertionError('Controlled demo failed: '+label)

def balance():
    sums=LedgerEntry.objects.aggregate(d=Sum('debit'),c=Sum('credit'))
    return dict(debit=str(sums['d']),credit=str(sums['c']),balanced=sums['d']==sums['c'])
def totals():
    return dict(phones=Device.objects.count(),available=Device.objects.filter(status='Available').count(),
        stock=str(StockMovement.objects.aggregate(v=Sum('value'))['v']),
        ar=str(Document.objects.filter(kind='Sales Invoice',status='Submitted').aggregate(v=Sum('outstanding'))['v']),
        ap=str(Document.objects.filter(kind='Purchase Invoice',status='Submitted').aggregate(v=Sum('outstanding'))['v']))

class Http:
    def __init__(self,role=None):
        self.jar=http.cookiejar.CookieJar();self.client=build_opener(HTTPCookieProcessor(self.jar))
        self.base='http://127.0.0.1:8000'
        if role:
            self.get('/login/');account=context['accounts'][role]
            status,body,url=self.post('/login/',dict(username=account['email'],password=account['password']))
            check(role+' Supabase login',status==200 and url.endswith('/desk/mobile-shop'))
    def token(self):return next(c.value for c in self.jar if c.name=='csrftoken')
    def request(self,path,method='GET',body=None,headers=None):
        try:
            with self.client.open(Request(self.base+path,data=body,method=method,headers=headers or {}),timeout=30) as response:return response.status,response.read(),response.url
        except HTTPError as error:return error.code,error.read(),error.url
    def get(self,path):return self.request(path)
    def post(self,path,data):
        values={**data,'csrfmiddlewaretoken':self.token()}
        return self.request(path,'POST',urlencode(values,doseq=True).encode(),{'Referer':self.base+path})
    def json(self,path,data):return self.request(path,'POST',json.dumps(data).encode(),{'Content-Type':'application/json','X-CSRFToken':self.token(),'Referer':self.base+'/desk/point-of-sale'})
    def multipart(self,path,fields,filename,content):
        boundary='Covison'+uuid.uuid4().hex;parts=[]
        for key,value in {**fields,'csrfmiddlewaretoken':self.token()}.items():
            parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode())
        parts.append((f'--{boundary}\r\nContent-Disposition: form-data; name="evidence_1"; filename="{filename}"\r\nContent-Type: application/pdf\r\n\r\n').encode()+content+b'\r\n')
        parts.append(f'--{boundary}--\r\n'.encode())
        return self.request(path,'POST',b''.join(parts),{'Content-Type':'multipart/form-data; boundary='+boundary,'Referer':self.base+path})
    def logout(self):
        status,_,url=self.post('/logout/',{});return status==200 and url.endswith('/login/')

def line(product,rate,device=None,warehouse=store,quantity=1):
    return {'lines-TOTAL_FORMS':'1','lines-INITIAL_FORMS':'0','lines-MIN_NUM_FORMS':'0','lines-MAX_NUM_FORMS':'1000',
        'lines-0-product':str(product.pk),'lines-0-device':str(device.pk) if device else '',
        'lines-0-warehouse':str(warehouse.pk),'lines-0-quantity':str(quantity),'lines-0-rate':str(rate)}

def new(client,kind,values,items=None,allow_failure=False):
    path='/documents/'+quote(kind)+'/new/'
    client.get(path)
    status,body,url=client.post(path,{'branch':str(branch.pk),'posting_date':today,'notes':'DEMO controlled HTTP run '+run_id,**values,**(items or {})})
    match=re.search(r'/document/([0-9a-f-]+)/$',urlsplit(url).path)
    if not match:
        if allow_failure:return None,(status,body,url)
        errors=re.findall(r'<ul class="errorlist".*?</ul>',body.decode(errors='replace'),re.S)
        Path('/tmp/demo-form-errors.json').write_text(json.dumps({'kind':kind,'status':status,'errors':errors},indent=2))
        raise AssertionError('Could not save '+kind+' through its HTTP form; see isolated form errors.')
    doc=Document.objects.get(pk=match.group(1));records[doc.code]=dict(kind=doc.kind,id=str(doc.pk))
    return doc

def action(client,doc,kind,values=None,key=None):
    return client.post(f'/document/{doc.pk}/{kind}/',dict(request_key=str(key or uuid.uuid4()),**(values or {})))
def submit(client,doc):
    status,body,url=action(client,doc,'submit');doc.refresh_from_db()
    if doc.status!='Submitted':Path('/tmp/demo-form-errors.json').write_text(json.dumps({'kind':doc.kind,'status':status,'messages':re.findall(r'<div class="notice.*?</div>',body.decode(errors='replace'),re.S)},indent=2))
    check('Submit '+doc.kind,doc.status=='Submitted',document=doc.code,http_status=status)
    return doc
def payment(client,party,amount,direction,reference=None,mode='Cash'):
    values=dict(handler=str(cashier.pk),amount=str(amount),direction=direction,mode=mode,
        transaction_reference='DEMO-REFERENCE-'+run_id if mode!='Cash' else '',reference=str(reference.pk) if reference else '')
    values['customer' if party.kind=='Customer' else 'supplier']=str(party.pk)
    return submit(client,new(client,'Payment Entry',values))

baseline=totals()
owner=Http('owner');sales=Http('sales');cash=Http('cashier');outsider=Http('other')
check('Sales staff cannot read cost reports',sales.get('/desk/query-report/Device%20Profit')[0]==403)
check('Sales staff cannot open purchase forms',sales.get('/documents/Purchase%20Invoice/new/')[0]==403)
intake_values=dict(supplier=str(supplier.pk),warehouse=str(store.pk),buyer=str(buyer.pk),purchase_source='Distributor',
    device_type='Used',condition='Good',inspection_status='Passed',inspection_date=today,battery_checked='on',battery='91',
    asking_price='68000',minimum_price='60000',wholesale_price='65000',warranty_days='7',warranty_terms='DEMO seven-day checking warranty',
    inspection_notes='Synthetic assessment for isolated QA; not a real inspection.',pta_status='Non-PTA')
intake=submit(owner,new(owner,'Mobile Intake',intake_values,line(phone_product,51000)))
phone=Device.objects.get(pk=intake.data['device']);receipt=Document.objects.get(pk=intake.data['purchase_receipt'])
check('Phone received without IMEI using PTA status',phone.purchase_cost==51000 and not phone.imeis.exists() and phone.pta_status=='Non-PTA' and phone.status=='Available')
check('Posted staff attribution',receipt.staff_names['buyer']=='Usman Khan')
action(owner,receipt,'bill');bill=receipt.linked.get(kind='Purchase Invoice')
check('Purchase draft edit denies cost access',sales.get('/documents/Purchase%20Invoice/'+str(bill.pk)+'/edit/')[0]==403)
submit(owner,bill)
payment(cash,supplier,20000,'Pay',bill);bill.refresh_from_db();check('Supplier partial payment leaves correct balance',bill.outstanding==31000)

# Preserve access to a legacy private attachment without restoring IMEI entry UI.
file_bytes=b'%PDF-1.4\n% DEMO synthetic attachment for controlled testing only\n%%EOF\n'
device_fields=dict(device_type='Used',condition='Good',inspection_status='Passed',inspection_date=today,battery_checked='on',battery='91',
    asking_price='68000',minimum_price='60000',wholesale_price='65000',warranty_days='7',warranty_terms='DEMO seven-day checking warranty',
    pta_status='Non-PTA')
owner.get('/device/'+str(phone.pk)+'/')
status,_,_=owner.post('/device/'+str(phone.pk)+'/',device_fields)
from django.core.files.base import ContentFile
attachment=IMEI.objects.create(number=context['imei'],device=phone,slot=1)
attachment.evidence.save('qa-'+run_id+'.pdf',ContentFile(file_bytes))
check('PTA-only edit and archived private attachment',status==200 and bool(attachment.evidence.name))
file_path='/attachments/'+quote(attachment.evidence.name,safe='/')
check('Authorized private download',owner.get(file_path)[1]==file_bytes)
check('Cross-shop file denied',outsider.get(file_path)[0]==404)

low=new(owner,'Sales Invoice',dict(customer=str(customer.pk),seller=str(seller.pk),warehouse=str(store.pk)),line(phone_product,59000,phone))
before=LedgerEntry.objects.count();status,_,_=action(cash,low,'checkout',{'handler':str(cashier.pk)})
phone.refresh_from_db();check('Minimum-price restriction',status==403 and phone.status=='Available' and LedgerEntry.objects.count()==before)
sale=new(sales,'Sales Invoice',dict(customer=str(customer.pk),seller=str(seller.pk),warehouse=str(store.pk)),line(phone_product,68000,phone))
checkout=dict(handler=str(cashier.pk),payment_mode=['Cash','Bank Transfer'],payment_amount=['20000','18000'],payment_reference=['','DEMO-BANK-'+run_id])
checkout_key=uuid.uuid4();action(cash,sale,'checkout',checkout,checkout_key);sale.refresh_from_db()
check('Cashier posts split payment with separate seller',sale.status=='Submitted' and sale.outstanding==30000 and sale.staff_names['seller']=='Ali Hassan')
count=Document.objects.filter(kind='Payment Entry').count();action(cash,sale,'checkout',checkout,checkout_key)
check('Duplicate checkout is idempotent',Document.objects.filter(kind='Payment Entry').count()==count)
check('Other shop cannot open sale',outsider.get('/document/'+str(sale.pk)+'/')[0]==404)
advance=payment(cash,customer,5000,'Receive')
action(cash,advance,'allocate',{'invoice':str(sale.pk),'amount':'5000'});advance.refresh_from_db();sale.refresh_from_db()
check('Advance allocation does not receive money twice',sale.outstanding==25000 and services.money(advance.data['unallocated'])==0)
payment(cash,customer,25000,'Receive',sale);sale.refresh_from_db();check('Customer settlement',sale.outstanding==0)
status,printed,_=cash.get('/receipt/'+str(sale.pk)+'/')
check('Receipt contains PTA staff and demo label without IMEI',status==200 and b'Non-PTA' in printed and context['imei'].encode() not in printed and b'Ali Hassan' in printed and b'DEMONSTRATION DATA' in printed)
claim=submit(owner,new(owner,'Warranty Claim',dict(customer=str(customer.pk),reference=str(sale.pk),device=str(phone.pk),claim_status='Closed',resolution='DEMO cable replaced under checking warranty')))
check('Warranty keeps original sale and customer',claim.reference_id==sale.pk and claim.customer_id==customer.pk and claim.state=='Closed')

expense_result=new(owner,'Purchase Invoice',dict(supplier=str(supplier.pk),buyer=str(buyer.pk),warehouse=str(store.pk)),line(service_product,2000,phone),allow_failure=True)
if isinstance(expense_result,tuple):
    errors=re.findall(r'<ul class="errorlist".*?</ul>',expense_result[1][1].decode(errors='replace'),re.S)
    findings.append(dict(severity='blocking',feature='Repair expense on sold device',description='The HTTP form rejects a sold device on a service supplier bill.',errors=errors))
    check('Repair expense can reference sold device',False)
expense=submit(owner,expense_result)
phone.refresh_from_db()
check('Repair expense does not receive stock',expense.total==2000 and phone.status=='Sold')

action(cash,sale,'return',dict(devices=[str(phone.pk)],condition='DEMO cable returned; sample condition',reason='Controlled return test'))
returned=sale.linked.get(is_return=True);submit(cash,returned)
payment(cash,customer,68000,'Pay',returned);returned.refresh_from_db();phone.refresh_from_db()
check('Exact-phone return and refund',returned.outstanding==0 and phone.status=='Available' and phone.inspection_status=='Pending')
transfer=submit(owner,new(owner,'Stock Entry',dict(warehouse=str(store.pk),target_warehouse=str(counter.pk)),line(phone_product,0,phone)))
phone.refresh_from_db();check('Transfer retains phone identity and cost',phone.warehouse_id==counter.pk and phone.purchase_cost==51000)

# Set up a second location only inside this disposable demo database.
from shop import setup as shop_setup
owner_user=SupabaseIdentity.objects.get(auth_id=context['accounts']['owner']['id']).user
target_shop=shop_setup.create_shop(dict(code='QA-TRANSFER',name='QA transfer destination',company=branch.company,warehouse_name='Stock room',till_name='Till',payment_methods=['Cash']),owner_user)
target_store=Warehouse.objects.get(branch=target_shop)
for shop in (branch,target_shop):
    configuration=ShopSettings.objects.get(branch=shop)
    clearing=Account.objects.create(code=shop.code+'-QA-CLEARING',name=shop.name+' QA clearing',account_type='Current Asset')
    configuration.accounts['inter_shop']=str(clearing.pk);configuration.save()
transfer_lines=line(phone_product,0,phone);transfer_lines['lines-0-warehouse']=str(counter.pk)
inter=submit(owner,new(owner,'Stock Entry',dict(warehouse=str(counter.pk),target_warehouse=str(target_store.pk)),transfer_lines))
phone.refresh_from_db()
check('Inter-shop transfer keeps IMEI and purchase cost',phone.branch_id==target_shop.pk and phone.warehouse_id==target_store.pk and phone.purchase_cost==51000 and phone.imeis.get(slot=1).number==context['imei'])
status,body,_=owner.get('/document/'+str(inter.pk)+'/')
check('Transfer detail shows both locations and PTA',status==200 and b'QA transfer destination' in body and b'Non-PTA' in body and context['imei'].encode() not in body and b'Stock transfer' in body)
shop_balances=[inter.ledger.filter(branch=shop).aggregate(d=Sum('debit'),c=Sum('credit')) for shop in (branch,target_shop)]
check('Each shop balances inventory and clearing',inter.ledger.count()==4 and all(row['d']==row['c']==51000 for row in shop_balances))
status,body,_=owner.get('/desk/query-report/General%20Ledger?branch='+str(target_shop.pk))
check('Receiving-shop ledger shows transfer and shop',status==200 and inter.code.encode() in body and b'QA-TRANSFER' in body)
reverse_lines=line(phone_product,0,phone);reverse_lines['lines-0-warehouse']=str(target_store.pk)
back=submit(owner,new(owner,'Stock Entry',dict(branch=str(target_shop.pk),warehouse=str(target_store.pk),target_warehouse=str(counter.pk),notes='DEMO reverse physical transfer '+inter.code),reverse_lines))
phone.refresh_from_db();check('Reverse transfer preserves history and restores ownership',phone.branch_id==branch.pk and phone.warehouse_id==counter.pk and inter.status=='Submitted' and back.ledger.count()==4)

for legacy in Document.objects.filter(branch=branch,kind='POS Opening Entry',status='Submitted',state='Open',till__isnull=True):
    reviewed=submit(owner,new(owner,'POS Closing Entry',dict(handler=str(cashier.pk),reference=str(legacy.pk),counted_cash='0',difference_notes='DEMO legacy review: synthetic count only; opening float and historical till movements unavailable.'),allow_failure=False))
    check('Legacy closing does not infer missing opening cash',reviewed.data['reconciliation']['expected'] is None)
# Synthetic rule exists only inside this disposable demo database.
tax_account_code='QA-OUTPUT-'+run_id
owner.get('/master/Account/new/')
status,_,_=owner.post('/master/Account/new/',dict(code=tax_account_code,name='DEMO output tax',account_type='Tax',enabled='on'))
output_tax=Account.objects.get(code=tax_account_code)
check('Owner creates separate output-tax account through form',status==200 and output_tax.account_type=='Tax')
tax_path='/setup/tax/new/?branch='+str(branch.pk);owner.get(tax_path)
tax_values=dict(name='DEMO synthetic 5 percent',rate='5',account=str(output_tax.pk),reference='Disposable QA instruction, not a statutory rate',enabled='on')
status,_,_=owner.post(tax_path,tax_values)
tax_rule=SalesTaxRule.objects.get(branch=branch,name=tax_values['name'])
check('Owner configures explicit sales-tax rule',status==200 and tax_rule.rate==5 and tax_rule.reference==tax_values['reference'])
check('Cashier cannot configure sales tax',cash.get('/setup/tax/')[0]==403)
till=Till.objects.get(branch=branch,name='Main till')
opening=submit(cash,new(cash,'POS Opening Entry',dict(handler=str(cashier.pk),till=str(till.pk),opening_float='1000')))
check('Opening cash does not post a new receipt',not opening.ledger.exists() and services.till_balance(opening)['expected']=='1000.00')
pos_data=dict(request_key=str(uuid.uuid4()),branch=str(branch.pk),customer=str(customer.pk),seller=str(seller.pk),handler=str(cashier.pk),
    items=[dict(product=str(accessory.pk),device='',warehouse=str(store.pk),quantity=1,rate='2300')],payments=[dict(mode='Cash',amount='2000',cash_tendered='2500.50'),dict(mode='Bank Transfer',amount='300',reference='DEMO-POS-BANK-'+run_id)])
status,body,_=cash.json('/api/pos/checkout',pos_data);check('POS accessory checkout',status==200)
pos=Document.objects.get(pk=json.loads(body)['id']);check('POS invoice settled and stock reduced',pos.outstanding==0 and services.on_hand(accessory,store)[0]==8)
cash_payment=pos.linked.get(kind='Payment Entry',data__mode='Cash')
check('Cash tender/change retains only net cash',cash_payment.data['cash_tendered']=='2500.50' and cash_payment.data['change_returned']=='500.50' and cash_payment.total==2000 and cash_payment.data['till_cash']=='2000.00')
posted_count=Document.objects.filter(kind='POS Invoice',status='Submitted').count();status,replayed,_=cash.json('/api/pos/checkout',pos_data)
check('POS retry returns original sale without duplicate stock or money',status==200 and json.loads(replayed)['id']==str(pos.pk) and Document.objects.filter(kind='POS Invoice',status='Submitted').count()==posted_count and services.on_hand(accessory,store)[0]==8)
check('POS receipt renders',cash.get('/receipt/'+str(pos.pk)+'/')[0]==200)
status,body,_=cash.get('/receipt/'+str(pos.pk)+'/?paper=58')
check('Thermal receipt shows cash handed and change',status==200 and b'Cash handed PKR 2500.50' in body and b'Change returned PKR 500.50' in body)
invalid=json.loads(json.dumps(pos_data));invalid['request_key']=str(uuid.uuid4());invalid['payments'][0]['cash_tendered']='1000'
status,_,_=cash.json('/api/pos/checkout',invalid)
check('Under-tendered cash rolls back HTTP checkout',status==400 and Document.objects.filter(kind='POS Invoice',status='Submitted').count()==posted_count and services.on_hand(accessory,store)[0]==8)

# Return two halves of the exact accessory line, with explicit cash refunds.
original_line=pos.lines.get();returned_value=services.ZERO
for index in (1,2):
    values=dict(return_warehouse=str(store.pk),condition='DEMO accessory inspected',reason='Controlled partial accessory return',**{'return_qty_'+str(original_line.pk):'0.5'})
    key=uuid.uuid4();_,_,url=action(cash,pos,'return',values,key)
    credit=Document.objects.get(pk=urlsplit(url).path.strip('/').split('/')[-1])
    _,_,retry_url=action(cash,pos,'return',values,key)
    check('Accessory return draft retry preserves one credit '+str(index),retry_url==url)
    submit(cash,credit);check('Partial accessory return retains price and cost '+str(index),credit.total==-1150 and credit.lines.get().snapshot['return_line']==str(original_line.pk) and credit.lines.get().cost==original_line.cost)
    payment(cash,customer,1150,'Pay',credit);returned_value+=credit.stock.aggregate(v=Sum('value'))['v']
check('Accessory fully returned without duplicate quantity',services.on_hand(accessory,store)[0]==9)
before_returns=pos.linked.filter(is_return=True).count()
action(cash,pos,'return',dict(condition='Good',reason='Attempt excess',return_warehouse=str(store.pk),**{'return_qty_'+str(original_line.pk):'0.1'}))
check('Excess accessory return rejected through HTTP',pos.linked.filter(is_return=True).count()==before_returns)

# A return on an unpaid invoice first settles the original debt without cash.
unpaid=submit(cash,new(cash,'Sales Invoice',dict(customer=str(customer.pk),seller=str(seller.pk),warehouse=str(store.pk)),line(accessory,2300)))
unpaid_line=unpaid.lines.get()
_,_,url=action(cash,unpaid,'return',dict(condition='DEMO unopened',reason='Credit sale reversed',return_warehouse=str(store.pk),**{'return_qty_'+str(unpaid_line.pk):'1'}))
credit=submit(cash,Document.objects.get(pk=urlsplit(url).path.strip('/').split('/')[-1]))
blocked=new(cash,'Payment Entry',dict(customer=str(customer.pk),handler=str(cashier.pk),direction='Pay',amount='2300',mode='Cash',reference=str(credit.pk)))
action(cash,blocked,'submit');blocked.refresh_from_db()
check('Refund blocked until unpaid invoice is credited',blocked.status=='Draft' and not blocked.ledger.exists())
key=uuid.uuid4();_,_,url=action(cash,credit,'apply-return-credit',{'amount':'2300'},key)
_,_,retry_url=action(cash,credit,'apply-return-credit',{'amount':'2300'},key)
credit.refresh_from_db();unpaid.refresh_from_db()
check('Return credit clears debt once without cash',credit.outstanding==0 and unpaid.outstanding==0 and retry_url==url and services.till_balance(opening)['expected']=='700.00')
status,body,_=cash.get('/receipt/'+str(credit.pk)+'/?paper=58')
check('Credit receipt distinguishes debt application from cash refund',status==200 and b'Credit applied PKR 2300.00' in body and unpaid.code.encode() in body and b'Refund Cash' not in body)

_,staff_before=reports.run('Staff Summary',owner_user,{'staff':str(cashier.pk)})
collection_before=sum(row['Money Collected'] for row in staff_before);payout_before=sum(row['Money Paid / Refunded'] for row in staff_before)
def fund_draft(source,target,amount,scope='till',reference='DEMO-BANK-MOVE'):
    path='/fund-transfers/new/';cash.get(path)
    payload=dict(request_key=str(uuid.uuid4()),branch=str(branch.pk),posting_date=today,handler=str(cashier.pk),source_method=source,target_method=target,
        amount=str(amount),cash_scope=scope,transfer_reference=reference,notes='DEMO completed account movement '+run_id)
    status,body,url=cash.post(path,payload)
    match=re.search(r'/document/([0-9a-f-]+)/$',urlsplit(url).path)
    if not match:raise AssertionError('Internal-transfer draft did not save: '+str(status))
    doc=Document.objects.get(pk=match.group(1));_,_,again=cash.post(path,payload)
    check('Internal-transfer draft is idempotent '+reference,again==url)
    return doc
deposit=submit(cash,fund_draft('Cash','Bank Transfer',500))
check('Till deposit moves cash without an invoice or sale',deposit.ledger.count()==2 and deposit.customer_id is None and not deposit.stock.exists() and services.till_balance(opening)['expected']=='200.00')
action(cash,deposit,'reverse-funds')
reverse_transfer=deposit.linked.get(kind='Payment Entry',data__direction='Internal Transfer')
path='/fund-transfers/'+str(reverse_transfer.pk)+'/edit/';cash.get(path)
cash.post(path,dict(request_key=str(uuid.uuid4()),branch=str(branch.pk),posting_date=today,handler=str(cashier.pk),source_method='Bank Transfer',target_method='Cash',amount='500',cash_scope='till',transfer_reference='DEMO-REVERSE-MOVE',notes='DEMO actual reverse movement'))
reverse_transfer=submit(cash,reverse_transfer)
check('Reverse movement restores till without changing original',services.till_balance(opening)['expected']=='700.00' and deposit.status=='Submitted' and reverse_transfer.reference_id==deposit.pk)
wallet_move=submit(cash,fund_draft('Bank Transfer','JazzCash',100,'bank','DEMO-WALLET-MOVE'))
check('Bank-to-wallet transfer stays outside till cash',wallet_move.pos_shift_id is None and services.till_balance(opening)['expected']=='700.00')
_,staff_after=reports.run('Staff Summary',owner_user,{'staff':str(cashier.pk)})
check('Internal movements do not inflate collections or payouts',sum(row['Money Collected'] for row in staff_after)==collection_before and sum(row['Money Paid / Refunded'] for row in staff_after)==payout_before and sum(row['Internal Transfers Handled'] for row in staff_after)>=1100)
check('Transfer history shows recorded movements',deposit.code.encode() in cash.get('/fund-transfers/')[1] and wallet_move.code.encode() in cash.get('/fund-transfers/')[1])

# Exercise both ordinary sales forms and POS, then fully reverse the test stock and debt.
discounted=submit(owner,new(owner,'Sales Invoice',dict(customer=str(customer.pk),seller=str(seller.pk),warehouse=str(store.pk),invoice_discount_type='percent',invoice_discount_value='10',sales_tax_rule=str(tax_rule.pk)),line(accessory,'100',quantity=2)))
check('Discounted invoice posts net revenue and exclusive tax',discounted.total==189 and discounted.ledger.get(account=output_tax).credit==9 and discounted.data['pricing']['net_total']=='180.00')
action(owner,discounted,'return',dict(condition='DEMO original packaging',reason='QA round trip',return_warehouse=str(store.pk),**{'return_qty_'+str(discounted.lines.get().pk):'2'}))
form_credit=discounted.linked.get(kind='Sales Invoice',is_return=True);submit(owner,form_credit)
check('Form invoice return reverses frozen discount and tax',form_credit.total==-189 and form_credit.ledger.get(account=output_tax).debit==9)
action(cash,form_credit,'apply-return-credit',{'amount':'189'});discounted.refresh_from_db();form_credit.refresh_from_db()
check('Discounted credit clears gross debt without cash',discounted.outstanding==0 and form_credit.outstanding==0 and services.till_balance(opening)['expected']=='700.00')
priced=dict(request_key=str(uuid.uuid4()),branch=str(branch.pk),customer=str(customer.pk),seller=str(seller.pk),handler=str(cashier.pk),
    items=[dict(product=str(accessory.pk),warehouse=str(store.pk),quantity=2,rate='100')],adjustments=dict(discount_type='amount',discount_value='20',tax_rule=str(tax_rule.pk),tax_version=pricing.tax_version(tax_rule)),payments=[dict(mode='Cash',amount='189',cash_tendered='200')])
status,body,_=cash.json('/api/pos/checkout',priced);check('POS accepts captured discount and tax',status==200)
priced_sale=Document.objects.get(pk=json.loads(body)['id']);posted_payments=priced_sale.linked.count()
status,retry,_=cash.json('/api/pos/checkout',priced)
check('Priced POS retry posts once with exact change',status==200 and json.loads(retry)['id']==str(priced_sale.pk) and priced_sale.linked.count()==posted_payments and services.cash_summary(priced_sale)['change_returned']==11)
check('Till receives gross amount after change',services.till_balance(opening)['expected']=='889.00')
for quantity in ('0.5','1.5'):
    action(owner,priced_sale,'return',dict(condition='DEMO good',reason='QA partial return',return_warehouse=str(store.pk),**{'return_qty_'+str(priced_sale.lines.get().pk):quantity}))
    partial=priced_sale.linked.filter(kind='Sales Invoice',is_return=True,status='Draft').get();submit(owner,partial)
    payment(cash,customer,-partial.total,'Pay',partial)
check('Partial taxed returns restore original stock and till',services.till_balance(opening)['expected']=='700.00' and sum(credit.total for credit in priced_sale.linked.filter(is_return=True,status='Submitted'))==-priced_sale.total)
status,body,_=owner.get('/receipt/'+str(priced_sale.pk)+'/?paper=58')
check('Receipt explains subtotal discount and tax',status==200 and b'Item subtotal PKR 200.00' in body and b'Invoice discount PKR 20.00' in body and b'DEMO synthetic 5 percent (5%)' in body and b'Total PKR 189.00' in body)
_,register=reports.run('Sales Register',cashier.user,{})
priced_row=next(row for row in register if row['Invoice']==priced_sale.code)
check('Sales register distinguishes revenue tax and customer total',priced_row['Net sales before tax']==180 and priced_row['Sales tax']==9 and priced_row['Total']==189)
owner.get('/setup/tax/'+str(tax_rule.pk)+'/');status,body,_=owner.post('/setup/tax/'+str(tax_rule.pk)+'/',{**tax_values,'rate':'7'})
tax_rule.refresh_from_db();check('Open till prevents tax-rule changes',status==200 and b'Close this shop' in body and tax_rule.rate==5)

# Owner journal corrections use only independent expense accounts, not retail controls.
journal_accounts=[]
for suffix in ('RENT','REPAIR'):
    account_code='QA-JOURNAL-'+suffix+'-'+run_id;owner.get('/master/Account/new/')
    status,_,_=owner.post('/master/Account/new/',dict(code=account_code,name='DEMO journal '+suffix,account_type='Expense Account',enabled='on'))
    journal_accounts.append(Account.objects.get(code=account_code))
def journal_fields(amount='125',doc=None):
    data=dict(request_key=str(uuid.uuid4()),branch=str(branch.pk),posting_date=today,supporting_reference='DEMO-OWNER-REFERENCE',notes='DEMO expense reclassification',**{'rows-TOTAL_FORMS':'2','rows-INITIAL_FORMS':'2' if doc else '0','rows-MIN_NUM_FORMS':'0','rows-MAX_NUM_FORMS':'30'})
    rows=doc.data['manual_journal']['lines'] if doc else [dict(account=str(journal_accounts[0].pk),debit=amount,credit='0'),dict(account=str(journal_accounts[1].pk),debit='0',credit=amount)]
    for index,row in enumerate(rows):
        for key in ('account','debit','credit','memo'):data['rows-'+str(index)+'-'+key]=row.get(key,'')
    return data
check('Payment staff cannot access owner journals',cash.get('/journals/')[0]==403 and sales.get('/journals/new/')[0]==403)
owner.get('/journals/new/');fields=journal_fields();status,body,url=owner.post('/journals/new/',fields)
journal=Document.objects.get(code=re.search(r'<h1>([^<]+)</h1>',body.decode()).group(1))
check('Owner saves balanced journal through its form',status==200 and journal.status=='Draft' and journal.total==125)
status,_,replayed=owner.post('/journals/new/',fields)
check('Journal draft retry preserves one document',replayed==url)
key=uuid.uuid4();action(owner,journal,'submit',key=key);action(owner,journal,'submit',key=key);journal.refresh_from_db()
check('Owner posts journal once without stock or party effects',journal.status=='Submitted' and journal.ledger.count()==2 and not journal.stock.exists() and journal.outstanding==0 and journal.customer_id is None and journal.till_id is None)
original_evidence=Document.objects.values().get(pk=journal.pk)
check('Cashier cannot read or reverse owner journal',cash.get('/document/'+str(journal.pk)+'/')[0]==403 and action(cash,journal,'reverse-journal')[0]==403)
status,_,_=action(owner,journal,'reverse-journal');reversal=journal.linked.get(data__manual_journal__reversal_of=str(journal.pk))
reverse_path='/journals/'+str(reversal.pk)+'/edit/';owner.get(reverse_path);status,_,_=owner.post(reverse_path,journal_fields(doc=reversal));submit(owner,reversal)
check('Journal reversal uses exact original debit and credit',reversal.ledger.get(account=journal_accounts[0]).credit==125 and reversal.reference_id==journal.pk)
action(owner,journal,'replace-journal');replacement=journal.linked.get(data__manual_journal__replacement_for=str(journal.pk))
replacement_fields=journal_fields(doc=replacement);replacement_fields['rows-0-debit']='175';replacement_fields['rows-1-credit']='175'
replacement_path='/journals/'+str(replacement.pk)+'/edit/';owner.get(replacement_path);owner.post(replacement_path,replacement_fields);submit(owner,replacement)
check('Journal correction preserves original and posts linked replacement',Document.objects.values().get(pk=journal.pk)==original_evidence and replacement.total==175 and replacement.reference_id==journal.pk)
net=LedgerEntry.objects.filter(account=journal_accounts[0]).aggregate(d=Sum('debit'),c=Sum('credit'))
check('Correction chain leaves only replacement accounting effect',net['d']-net['c']==175 and services.till_balance(opening)['expected']=='700.00')
invalid=journal_fields();invalid['rows-1-credit']='124';count=Document.objects.filter(kind='Journal Entry').count();owner.get('/journals/new/');status,body,_=owner.post('/journals/new/',invalid)
check('Unbalanced HTTP journal creates no draft or ledger',status==200 and b'Journal debits and credits must balance' in body and Document.objects.filter(kind='Journal Entry').count()==count)

rows=services.balances(branch,today)
closing_values=dict(handler=str(cashier.pk))
for row in rows:closing_values['count_'+row['account']]=row['expected'];closing_values['note_'+row['account']]='DEMO matching count'
closing=new(owner,'Mobile Daily Closing',closing_values)
action(owner,closing,'submit');closing.refresh_from_db();check('Daily closing blocks open shifts',closing.status=='Draft')
for shift in Document.objects.filter(branch=branch,kind='POS Opening Entry',status='Submitted',state='Open'):
    counted=services.till_balance(shift)['expected']
    pos_closing=submit(owner,new(owner,'POS Closing Entry',dict(handler=str(cashier.pk),reference=str(shift.pk),counted_cash=counted)))
    check('Till count excludes bank payments and company balances',pos_closing.data['reconciliation']['expected']=='700.00' and pos_closing.data['reconciliation']['difference']=='0.00' and pos_closing.data['reconciliation']['bank_received']=='300.00')
    check('Till closing renders physical cash evidence',b'Till cash reconciliation' in owner.get('/document/'+str(pos_closing.pk)+'/')[1])
submit(owner,closing);check('Daily closing preserves balance evidence',all(services.money(row['difference'])==0 for row in closing.data['balances']))
for name in reports.REPORTS:
    status,_,_=owner.get('/desk/query-report/'+quote(name));check('Report '+name,status==200)
ending=totals();check('Stock and balances reconcile',services.money(ending['stock'])==services.money(baseline['stock'])+49800+returned_value and services.money(ending['ar'])==services.money(baseline['ar']) and services.money(ending['ap'])==services.money(baseline['ap'])+33000,baseline=baseline,ending=ending)
check('General ledger balances',balance()['balanced'],**balance())
for client in (owner,sales,cash,outsider):check('Demo login signed out',client.logout())
result=dict(run_id=run_id,checks=checks,records=records,findings=findings,baseline=baseline,ending=ending,ledger=balance(),
    real_http=True,real_supabase_auth=True,isolated_database=context['database'],visual_browser_testing=False,physical_printing_tested=False)
Path('/tmp/controlled-demo-result.json').write_text(json.dumps(result,indent=2,default=str))
print(json.dumps({'completed':True,'checks':len(checks),'handover_findings':findings},indent=2))
