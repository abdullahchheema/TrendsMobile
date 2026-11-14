"""QA audit, phase 6 and scenarios 3-5: the physical shop from the purchase to the daily closing.

Purchases (single, bulk, credit), IMEIs, the counter with a till shift, returns into Holding, supplier returns, transfers between shops with
a discrepancy, stock adjustments and the daily closing. After each workflow the stock, the ledger and the audit invariants must agree."""
import json
from datetime import timedelta
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import *
from . import bulk,easy_retail,exchange,imei as imei_pages,money,purchase_returns,returns_flow,shopdefaults,stock_adjust,transfers,services as s
from . import tests_audit_oracle as oracle
from .tests_audit_base import AuditMixin,P,imei
from .tests_audit_calculations import record


class ShopBase(AuditMixin,TestCase):
    def setUp(self):
        self.build();self.capital(self.a,'3000000');self.capital(self.b,'500000');self.me=shopdefaults.shop_person(self.a)
    def header(self,branch,**extra):
        me=shopdefaults.shop_person(branch)
        data=dict(branch=branch,party_name='Bulk Supplier',warehouse=shopdefaults.warehouse(branch),staff=me,handler=me,posting_date=self.today,notes='',payment_status='unpaid',amount_paid=None,method='Cash');data.update(extra);return data
    def movements(self,device):
        return [(m.warehouse.branch.code,int(m.quantity),P(m.value)) for m in StockMovement.objects.filter(device=device).select_related('warehouse__branch').order_by('pk')]


class Purchases(ShopBase):
    def test_a_bulk_purchase_records_every_phone_exactly_once_with_its_own_details(self):
        second=Product.objects.create(code='AUD-P2',name='Galaxy A99 256GB Blue',model=self.model,brand='Samsung',storage='256GB',colour='Blue',serialized=True,stock_item=True)
        raw=json.dumps([dict(product=str(self.phone_product.pk),quantity=3,cost='150000.01',cost_mode='total',condition='Good',device_type='Used',pta_status='PTA',asking_price='60000',imeis='\n'.join(imei(n) for n in (501,502,503))),
                        dict(product=str(second.pk),quantity=2,cost='70000',cost_mode='each',condition='Excellent',device_type='New',pta_status='Non-PTA',asking_price='85000',grade=9,imeis=f'{imei(504)} {imei(505)}\n{imei(506)}'),
                        dict(brand='Tecno',model='Spark 40',storage='128GB',colour='Gold',quantity=1,cost='25000.50',cost_mode='each',condition='Fair',device_type='Refurbished',pta_status='JV',asking_price='31000',imeis=imei(507))])
        rows=bulk.parse_rows(raw);bill=bulk.post(self.header(self.a,payment_status='part',amount_paid='100000'),rows,self.login_a)
        phones=list(Device.objects.filter(branch=self.a).select_related('product'));self.assertEqual(len(phones),6)
        total=P('150000.01')+2*P('70000')+P('25000.50');self.assertEqual(P(bill.total),total);self.assertEqual(sum(P(d.purchase_cost) for d in phones),total)
        record('Bulk purchase: bill total','3 phones for 150000.01 + 2 x 70000 + 25000.50',total,P(bill.total))
        shared=sorted(P(d.purchase_cost) for d in phones if d.product_id==self.phone_product.pk);self.assertEqual(shared,[5000000,5000000,5000001])           # 150000.01 over three phones, to the paisa
        self.assertEqual(P(bill.outstanding),total-P('100000'));self.assertEqual(self.balance(self.a,'inventory'),total);self.assertEqual(-self.balance(self.a,'payable'),total-P('100000'))
        blue=[d for d in phones if d.product_id==second.pk];self.assertEqual({(d.condition,d.device_type,d.pta_status,d.grade,P(d.asking_price)) for d in blue},{('Excellent','New','Non-PTA',9,8500000)})
        self.assertEqual(sorted(d.imeis.count() for d in blue),[1,2]);self.assertEqual(IMEI.objects.count(),7)
        tecno=Device.objects.get(product__name__icontains='Spark');self.assertEqual((tecno.pta_status,tecno.status,tecno.branch_id,P(tecno.purchase_cost)),('JV','Available',self.a.pk,2500050))
        self.assertEqual({d.status for d in phones},{'Available'});self.assertEqual(StockMovement.objects.filter(device__in=phones).count(),6)
        for d in phones:self.assertEqual(self.movements(d),[('AUDA',1,P(d.purchase_cost))])
        self.assert_books_clean()
    def test_an_imei_cannot_be_recorded_twice_in_any_spelling(self):
        phone,_=self.buy_phone(self.a,'50000','60000',pay='unpaid');number=phone.imeis.get().number;counts=(Device.objects.count(),Document.objects.count(),LedgerEntry.objects.count())
        for text in (number,' '+number+' ',number+','+number,number[:5]+' '+number[5:],number[:5]+'-'+number[5:],'0'+number,number+'0',number[:-1],number[:-1]+str((int(number[-1])+1)%10),'abcdefghijklmno','٣٥٦٩٣٨٠٣٠٠٠٠٠١٩',''):
            raw=json.dumps([dict(product=str(self.phone_product.pk),quantity=1,cost='50000',condition='Good',device_type='Used',pta_status='PTA',asking_price='60000',imeis=text)])
            with self.assertRaises(ValidationError,msg=repr(text)):bulk.post(self.header(self.b),bulk.parse_rows(raw),self.owner)       # not in this shop, not in any other shop
        self.assertEqual((Device.objects.count(),Document.objects.count(),LedgerEntry.objects.count()),counts)
        with self.assertRaises(ValidationError):s.update_imeis(self.buy_phone(self.b,'50000','60000')[0],[{'number':number},{}],self.owner)
        from django.db import IntegrityError,transaction
        with self.assertRaises(IntegrityError),transaction.atomic():IMEI.objects.create(number=number,device=phone,slot=2)              # and the database itself refuses a second row
        with self.assertRaises(IntegrityError),transaction.atomic():IMEI.objects.create(number=imei(900),device=phone,slot=3)
    def test_a_purchase_paid_from_an_empty_account_or_into_another_shop_is_refused(self):
        with self.assertRaises(PermissionDenied):self.buy_phone(self.b,'50000','60000',user=self.login_a)
        with self.assertRaises(ValidationError):self.buy_phone(self.a,'50000','60000',pay='full',method='JazzCash')        # not a payment method of this shop
        self.assertEqual(Device.objects.count(),0)
    def test_a_purchase_cannot_be_paid_from_an_account_that_does_not_hold_the_money(self):
        """Owner's decision D8 (14 Nov 2026): a purchase marked paid is refused, whole, when the account does not hold the money, as every other payment is."""
        before=(Device.objects.count(),Document.objects.count(),LedgerEntry.objects.count())
        with self.assertRaisesMessage(ValidationError,'Easypaisa has only PKR 0.00'):self.buy_phone(self.a,'50000','60000',pay='full',method='Easypaisa')
        with self.assertRaisesMessage(ValidationError,'has only'):self.buy_accessory(self.a,'Wallet cover',10,'100','250',pay='part',amount_paid='1',method='Easypaisa')
        self.assertEqual((Device.objects.count(),Document.objects.count(),LedgerEntry.objects.count()),before)              # nothing half-saved: no phone, no bill, no stock
        self.buy_phone(self.a,'50000','60000',pay='unpaid',method='Easypaisa')                                               # on credit it is fine
        money.move_money(self.owner,self.a,'Cash','Easypaisa','50000',self.today);self.buy_phone(self.a,'50000','60000',pay='full',method='Easypaisa')
        self.assertEqual(self.balance(self.a,'Easypaisa'),0);self.assert_books_clean()
        with self.assertRaisesMessage(ValidationError,'has only'):money.record_voucher(self.owner,self.a,'expense',Account.objects.get(code='COA-6100'),'10','Easypaisa',self.today,'Rent')


class CounterDay(ShopBase):
    def test_scenario_3_a_day_at_the_counter_from_opening_float_to_daily_closing(self):
        a=self.a;user=self.login_a;me=shopdefaults.person(user,a)
        phone,bill=self.buy_phone(a,'80000','100000',pay='unpaid',supplier='Day Supplier',user=user)                                      # 1
        money.record_party_payment(user,bill.supplier,'30000','Cash',self.today);bill.refresh_from_db();self.assertEqual(P(bill.outstanding),P('50000'))     # 2
        cash_before=self.balance(a,'Cash')
        shift=s.submit(s.new_doc('POS Opening Entry',a,handler=me,till=shopdefaults.till(a),data=dict(opening_float='5000')),user)       # 4
        with self.assertRaises(Exception):s.submit(s.new_doc('POS Opening Entry',a,handler=me,till=shopdefaults.till(a),data=dict(opening_float='0')),user)      # one open shift per till and cashier
        response=self.pos(user,a,[(phone,1,'100000')],[dict(mode='Cash',amount='60000.00',cash_tendered='60000.00'),dict(mode='Bank Transfer',amount='40000.00',reference='TRX-9')],customer='Day Customer')     # 5-7
        self.assertEqual(response.status_code,200,response.content);sale=Document.objects.get(pk=response.json()['id']);self.assertEqual((sale.kind,P(sale.outstanding)),('POS Invoice',0))
        money.record_voucher(user,a,'expense',Account.objects.get(code='COA-6120'),'1500','Cash',self.today,'Tea and supplies')            # a cash expense from the till
        want=P('5000')+P('60000')-P('1500');shift.refresh_from_db();till=s.till_balance(shift)
        self.assertEqual(P(till['expected']),want);record('Till cash expected at closing','float 5000 + cash sale 60000 - expense 1500',want,P(till['expected']))
        with self.assertRaises(ValidationError):s.submit(s.new_doc('Mobile Daily Closing',a,handler=me,data=dict(counts={})),user)          # not while a shift is open
        close=lambda counted,note='':s.submit(s.new_doc('POS Closing Entry',a,handler=me,reference=shift,posting_date=shift.posting_date,data=dict(counted_cash=counted,difference_notes=note)),user)
        with self.assertRaisesMessage(ValidationError,'Explain'):close('63000')                                                             # short by 500 with no explanation
        with self.assertRaises(ValidationError):close('')                                                                                  # no count at all
        closing=close('63000','Rs 500 given as change by mistake');self.assertEqual(P(closing.data['reconciliation']['difference']),-P('500'))             # 10
        record('Till difference','counted 63000 against 63500',-P('500'),P(closing.data['reconciliation']['difference']))
        with self.assertRaises(ValidationError):close('63500')                                                                              # the shift is closed: not twice
        self.assertEqual(self.pos(user,a,[(phone,1,'100000')]).status_code,400)                                                             # 5b. and the sold phone cannot be sold again
        # 11. daily closing: every cash and bank account counted; a difference must be explained and is booked
        expected={r['account']:P(r['expected']) for r in s.balances(a,self.today)};cash_account=str(self.account_of(a,'Cash').pk)
        self.assertEqual(expected[cash_account],cash_before+P('60000')-P('1500'))
        counts={pk:dict(counted=oracle.rupees(value-(50000 if pk==cash_account else 0)),notes='Till short by 500' if pk==cash_account else '') for pk,value in expected.items()}
        daily=s.submit(s.new_doc('Mobile Daily Closing',a,handler=me,data=dict(counts=counts)),user)
        self.assertEqual(self.balance(a,'Cash'),expected[cash_account]-50000)                                                               # the books now show the cash that was really counted
        self.assertEqual(sum(P(e.debit)-P(e.credit) for e in LedgerEntry.objects.filter(document=daily,account__code='COA-6950')),50000)
        with self.assertRaises(Exception):s.submit(s.new_doc('Mobile Daily Closing',a,handler=me,data=dict(counts=counts)),user)             # one closing a day
        with self.assertRaisesMessage(ValidationError,'already been closed'):money.record_voucher(user,a,'expense',Account.objects.get(code='COA-6120'),'10','Cash',self.today,'After closing')
        self.assertEqual(self.pos(user,a,[(phone,1,'1')]).status_code,400)
        # 12. reports for the day
        page=self.as_user(user).get(reverse('report',args=['Daily Sales']),{'branch':str(a.pk),'from_date':str(self.today),'to_date':str(self.today)});self.assertEqual(page.status_code,200)
        self.assert_books_clean()
    def test_a_phone_in_any_state_other_than_available_cannot_be_sold(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='unpaid');other,_=self.buy_phone(self.b,'80000','100000',pay='unpaid')
        for status in ('Sold','Reserved','Holding','In transit','Written off','Unavailable','Returned to supplier'):
            Device.objects.filter(pk=phone.pk).update(status=status);self.assertEqual(self.pos(self.login_a,self.a,[(phone,1,'100000')]).status_code,400,status)
        Device.objects.filter(pk=phone.pk).update(status='Available')
        wrong=Product.objects.create(code='AUD-P9',name='Other model 64GB',model=self.model,storage='64GB',serialized=True,stock_item=True)
        body=dict(branch=str(self.a.pk),customer_name='X',payments=[],request_key='00000000-0000-4000-8000-000000000001',items=[dict(product=str(wrong.pk),device=str(phone.pk),warehouse=str(shopdefaults.warehouse(self.a).pk),quantity='1',rate='100000')])
        self.assertEqual(self.as_user(self.login_a).post(reverse('pos_checkout'),json.dumps(body),content_type='application/json').status_code,400)        # the phone under another model's name
        for items in ([(phone,2,'100000')],[(phone,1,'100000'),(phone,1,'100000')],[(phone,'0.5','100000')],[(phone,1,'-1')],[(other,1,'100000')]):
            self.assertEqual(self.pos(self.login_a,self.a,items).status_code,400,items)
        phone.refresh_from_db();self.assertEqual(phone.status,'Available');self.assertEqual(Document.objects.filter(kind__in=s.SELL).count(),0)


class ReturnsAndExchanges(ShopBase):
    def sold(self,price='100000',paid='100000.00'):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
        sale=Document.objects.get(pk=self.pos(self.login_a,self.a,[(phone,1,price)],[dict(mode='Cash',amount=paid)] if paid else [],customer='Returning Customer').json()['id']);return phone,sale
    def test_scenario_4_a_returned_phone_waits_in_holding_until_it_is_accepted(self):
        phone,sale=self.sold();cash=self.balance(self.a,'Cash');inventory=self.balance(self.a,'inventory')
        credit=returns_flow.settle(self.login_a,sale,[str(phone.pk)],None,returns_flow.CONDITIONS[1],returns_flow.REASONS[0],'refund','Cash','60000')       # a part refund now
        phone.refresh_from_db();self.assertEqual((phone.status,phone.sale_id,phone.customer_id),('Holding',None,None))
        self.assertEqual(cash-self.balance(self.a,'Cash'),P('60000'));self.assertEqual(-P(credit.outstanding),P('40000'))                     # the rest stays owed to the customer
        self.assertEqual(self.balance(self.a,'inventory')-inventory,P('80000'));self.assertEqual(-self.balance(self.a,'sales'),0);self.assertEqual(self.balance(self.a,'cogs'),0)
        record('Stock value restored by a return','phone cost 80000, sold for 100000',P('80000'),self.balance(self.a,'inventory')-inventory)
        self.assertEqual(self.pos(self.login_a,self.a,[(phone,1,'100000')]).status_code,400)                                                  # not for sale while in Holding
        with self.assertRaises(ValidationError):returns_flow.settle(self.login_a,sale,[str(phone.pk)],None,returns_flow.CONDITIONS[1],returns_flow.REASONS[0],'credit')     # not twice
        with self.assertRaisesMessage(ValidationError,'more than'):money.record_party_cash_back(self.login_a,sale.customer,'40000.01','Cash',self.today)         # never more than is owed
        self.assertEqual(self.as_user(self.login_b).post(reverse('accept_holding',args=[phone.pk])).status_code,404)                         # another shop cannot accept it
        self.assertEqual(self.as_user(self.login_a).post(reverse('accept_holding',args=[phone.pk])).status_code,302);phone.refresh_from_db();self.assertEqual(phone.status,'Available')
        self.assertTrue(Audit.objects.filter(action='Accept phone into stock').exists())
        money.record_party_cash_back(self.login_a,sale.customer,'40000','Cash',self.today);credit.refresh_from_db();self.assertEqual(P(credit.outstanding),0);self.assert_books_clean()
    def test_an_unpaid_sale_returned_is_credited_against_the_debt_never_refunded(self):
        phone,sale=self.sold(paid=None);cash=self.balance(self.a,'Cash')
        credit=returns_flow.settle(self.login_a,sale,[str(phone.pk)],None,returns_flow.CONDITIONS[0],returns_flow.REASONS[1],'refund','Cash','')
        sale.refresh_from_db();self.assertEqual((P(sale.outstanding),P(credit.outstanding),self.balance(self.a,'Cash')),(0,0,cash));self.assert_books_clean()
    def test_one_item_of_a_mixed_invoice_and_part_of_a_quantity(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');cover,_=self.buy_accessory(self.a,'Return cover',20,'100','300',pay='full');rule=self.tax_rule(self.a,'17')
        want=oracle.invoice([(1,'100000'),(6,'300')],'percent','4.9','17')
        sale=Document.objects.get(pk=self.pos(self.login_a,self.a,[(phone,1,'100000'),(cover,6,'300')],[dict(mode='Cash',amount=oracle.rupees(want['total']))],discount_type='percent',discount_value='4.9',rule=rule).json()['id'])
        self.assertEqual(P(sale.total),want['total'])
        line=sale.lines.get(product=cover);credit=returns_flow.settle(self.login_a,sale,[],{str(line.pk):'2'},returns_flow.CONDITIONS[0],returns_flow.REASONS[1],'credit')
        two=oracle.to_paisa(oracle.Fraction(want['lines'][1]['net'],100)*2/6)+oracle.to_paisa(oracle.Fraction(want['lines'][1]['tax'],100)*2/6)
        self.assertEqual(-P(credit.total),two);record('Credit for 2 of 6 discounted, taxed items','6 x 300 less 4.9% plus 17%',two,-P(credit.total))
        rest=returns_flow.settle(self.login_a,sale,[],{str(line.pk):'4'},returns_flow.CONDITIONS[0],returns_flow.REASONS[1],'credit')
        self.assertEqual(-P(credit.total)-P(rest.total),want['lines'][1]['total'])                                                             # the two parts give back the line exactly, to the paisa
        with self.assertRaises(ValidationError):returns_flow.settle(self.login_a,sale,[],{str(line.pk):'1'},returns_flow.CONDITIONS[0],returns_flow.REASONS[1],'credit')
        self.assertEqual(s.on_hand(cover,shopdefaults.warehouse(self.a))[0],20);self.assert_books_clean()
    def test_an_exchange_for_a_dearer_and_a_cheaper_phone(self):
        new,_=self.buy_phone(self.a,'80000','100000',pay='full');cheap,_=self.buy_phone(self.a,'20000','30000',pay='full');cash=self.balance(self.a,'Cash')
        data=lambda phone,price:dict(branch=self.a,phone=phone,price=price,party_name='Trade Customer',pta_status='PTA',staff=self.me,buyer=self.me,handler=self.me,warehouse=shopdefaults.warehouse(self.a),posting_date=self.today,method='Cash',notes='')
        old=lambda n,value:[dict(product=self.phone_product,value=Decimal(value),imeis=[imei(n)],condition='Good',grade=7,pta='PTA',notes='')]
        exchange.post(data(new,'100000'),old(801,'45000'),self.login_a);self.assertEqual(self.balance(self.a,'Cash')-cash,P('55000'))            # the customer pays the difference
        record('Exchange difference received','new phone 100000, old phone valued 45000',P('55000'),self.balance(self.a,'Cash')-cash)
        exchange.post(data(cheap,'30000'),old(802,'45000'),self.login_a);self.assertEqual(self.balance(self.a,'Cash')-cash,P('55000')-P('15000'))  # the shop pays the difference
        traded=Device.objects.filter(imeis__number__in=[imei(801),imei(802)]);self.assertEqual({d.status for d in traded},{'Holding'});self.assertEqual({P(d.purchase_cost) for d in traded},{4500000})
        self.assertEqual(self.balance(self.a,'receivable'),0);self.assertEqual(self.balance(self.a,'payable'),0);self.assert_books_clean()
    def test_stock_goes_back_to_the_supplier_against_its_own_bill(self):
        one,bill=self.buy_phone(self.a,'50000','60000',pay='part',amount_paid='20000',supplier='Return Supplier');other,other_bill=self.buy_phone(self.a,'40000','50000',pay='unpaid',supplier='Different Supplier')
        with self.assertRaises(ValidationError):purchase_returns.create(bill,self.login_a,[str(other.pk)],{},'Wrong phone')                    # a phone from another supplier's bill
        with self.assertRaises(ValidationError):purchase_returns.create(bill,self.login_a,[str(one.pk)],{},'')                                 # a reason is required
        with self.assertRaises(PermissionDenied):purchase_returns.create(bill,self.login_b,[str(one.pk)],{},'Not my shop')
        purchase_returns.create(bill,self.login_a,[str(one.pk)],{},'Faulty charging port');bill.refresh_from_db();other_bill.refresh_from_db();one.refresh_from_db()
        self.assertEqual((one.status,P(bill.outstanding),P(other_bill.outstanding)),('Returned to supplier',-P('20000'),P('40000')))            # the supplier now owes the 20,000 already paid
        with self.assertRaises(ValidationError):purchase_returns.create(bill,self.login_a,[str(one.pk)],{},'Again')
        self.assert_books_clean()                                                                                                             # a bill left in credit with the supplier is a correct state, not an error
        cash=self.balance(self.a,'Cash');purchase_returns.settle(bill,self.login_a,'refund','Cash','',self.today);bill.refresh_from_db()
        self.assertEqual((P(bill.outstanding),self.balance(self.a,'Cash')-cash),(0,P('20000')));self.assertFalse(IMEI.objects.filter(device=one).exists());self.assert_books_clean()


class Transfers(ShopBase):
    def test_scenario_5_a_transfer_with_a_missing_phone(self):
        phones=[self.buy_phone(self.a,cost,'90000',pay='full')[0] for cost in ('70000','71000.50','72000')];a_stock=self.balance(self.a,'inventory')
        for user in (self.login_a,self.login_b,self.online_login):
            with self.assertRaises(PermissionDenied):transfers.create(user,self.a,self.b,phones)                                              # only the owner
        doc=transfers.create(self.owner,self.a,self.b,phones,'Stock for the new branch')                                                      # 1. pending: held, still in A
        self.assertEqual({d.status for d in Device.objects.filter(pk__in=[p.pk for p in phones])},{'Reserved'});self.assertEqual(self.balance(self.a,'inventory'),a_stock)
        self.assertEqual(self.pos(self.login_a,self.a,[(phones[0],1,'90000')]).status_code,400)
        with self.assertRaises(ValidationError):transfers.create(self.owner,self.a,self.b,phones[:1])                                         # a phone is in one transfer at a time
        transfers.dispatch(self.owner,doc)                                                                                                    # 2. in transit: in neither shop
        with self.assertRaises(ValidationError):transfers.dispatch(self.owner,doc)
        total=P('70000')+P('71000.50')+P('72000')
        self.assertEqual({(d.status,d.warehouse_id) for d in Device.objects.filter(pk__in=[p.pk for p in phones])},{('In transit',None)})
        self.assertEqual((a_stock-self.balance(self.a,'inventory'),self.balance(self.b,'inventory')),(total,0))
        for user,shop in ((self.login_a,self.a),(self.login_b,self.b)):self.assertEqual(self.pos(user,shop,[(phones[0],1,'90000')]).status_code,400)
        transfers.receive(self.owner,doc,[str(phones[0].pk),str(phones[1].pk)]);doc.refresh_from_db()                                          # 3-4. two arrive, one is missing
        self.assertEqual(transfers.status_of(doc),transfers.DISCREPANCY);self.assertEqual(self.balance(self.b,'inventory'),P('70000')+P('71000.50'))
        record('Stock value received by shop B','two of three phones arrived',P('141000.50'),self.balance(self.b,'inventory'))
        with self.assertRaises(ValidationError):transfers.receive(self.owner,doc,[str(phones[0].pk)])                                          # not received twice
        with self.assertRaises(ValidationError):transfers.cancel(self.owner,doc)                                                               # part of it has arrived
        for d in Device.objects.filter(pk__in=[p.pk for p in phones]):                                                                        # never in two shops
            self.assertEqual(sum(q for _,q,_ in self.movements(d)),1 if d.status=='Available' else 0,d.code)
        self.assertEqual(self.pos(self.login_b,self.b,[(Device.objects.get(pk=phones[0].pk),1,'90000')],[dict(mode='Cash',amount='90000.00')]).status_code,200)       # shop B can now sell what it received
        transfers.resolve(self.owner,doc,'write_off','Courier lost it');doc.refresh_from_db()                                                  # 5. the owner resolves it
        lost=Device.objects.get(pk=phones[2].pk);self.assertEqual((lost.status,transfers.status_of(doc)),('Written off',transfers.RECEIVED))
        self.assertEqual(sum(P(e.debit)-P(e.credit) for e in LedgerEntry.objects.filter(document=doc,account__code='COA-5200')),P('72000'))
        clearing=lambda shop:sum(P(e.debit)-P(e.credit) for e in LedgerEntry.objects.filter(branch=shop,account_id=ShopSettings.objects.get(branch=shop).accounts['inter_shop'],document=doc))
        self.assertEqual(clearing(self.a)+clearing(self.b),0)                                                                                  # 6. nothing left in transit between the two sets of books
        self.assert_books_clean()
    def test_a_transfer_cancelled_before_and_after_dispatch_puts_the_phones_back(self):
        one,_=self.buy_phone(self.a,'70000','90000',pay='full');two,_=self.buy_phone(self.a,'60000','80000',pay='full');stock=self.balance(self.a,'inventory')
        transfers.cancel(self.owner,transfers.create(self.owner,self.a,self.b,[one]),'Changed plan')
        doc=transfers.create(self.owner,self.a,self.b,[two]);transfers.dispatch(self.owner,doc);transfers.cancel(self.owner,doc,'Vehicle broke down')
        self.assertEqual({(d.status,d.branch_id) for d in Device.objects.filter(pk__in=[one.pk,two.pk])},{('Available',self.a.pk)});self.assertEqual(self.balance(self.a,'inventory'),stock)
        with self.assertRaises(ValidationError):transfers.receive(self.owner,doc);self.assert_books_clean()
    def test_a_missing_phone_can_be_returned_to_the_sender(self):
        one,_=self.buy_phone(self.a,'70000','90000',pay='full');two,_=self.buy_phone(self.a,'60000','80000',pay='full');stock=self.balance(self.a,'inventory')
        doc=transfers.create(self.owner,self.a,self.b,[one,two]);transfers.dispatch(self.owner,doc);transfers.receive(self.owner,doc,[str(one.pk)]);transfers.resolve(self.owner,doc,'return','Found at the sender')
        self.assertEqual(Device.objects.get(pk=two.pk).branch_id,self.a.pk);self.assertEqual((self.balance(self.a,'inventory'),self.balance(self.b,'inventory')),(stock-P('70000'),P('70000')));self.assert_books_clean()


class Adjustments(ShopBase):
    def post(self,user=None,branch=None,**data):
        base=dict(branch=branch or self.a,warehouse=shopdefaults.warehouse(branch or self.a),posting_date=self.today,notes='',reason='Count correction',phone=None,product=None,change=None,unit_cost=None);base.update(data)
        return stock_adjust.post(base,user or self.login_a)
    def test_write_offs_and_count_corrections_need_a_reason_and_keep_stock_and_ledger_in_step(self):
        cover,_=self.buy_accessory(self.a,'Adjust cover',10,'100','250',pay='full');phone,_=self.buy_phone(self.a,'80000','100000',pay='full');stock=self.balance(self.a,'inventory')
        for bad in (dict(product=cover,change=Decimal('-11')),dict(product=cover,change=Decimal('0')),dict(product=cover,change=Decimal('-1'),reason=''),dict(product=cover,change=Decimal('-1'),reason='Because'),dict(phone=phone,reason='')):
            with self.assertRaises((ValidationError,Exception)):self.post(**bad)
        with self.assertRaises(PermissionDenied):self.post(user=self.login_b,product=cover,change=Decimal('-1'))
        self.assertEqual(self.balance(self.a,'inventory'),stock)
        self.post(product=cover,change=Decimal('-3'),reason='Damaged');self.post(product=cover,change=Decimal('2'));self.post(phone=phone,reason='Lost / stolen')
        self.assertEqual(s.on_hand(cover,shopdefaults.warehouse(self.a)),(9,Decimal('900.00')));phone.refresh_from_db();self.assertEqual(phone.status,'Written off')
        self.assertEqual(stock-self.balance(self.a,'inventory'),P('100')+P('80000'))
        with self.assertRaises(ValidationError):self.post(phone=phone,reason='Lost / stolen')                                                 # not twice
        self.assertEqual(self.pos(self.login_a,self.a,[(phone,1,'100000')]).status_code,400);self.assert_books_clean()
    def test_stock_held_for_an_order_or_a_transfer_cannot_be_adjusted_away(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');transfers.create(self.owner,self.a,self.b,[phone])
        with self.assertRaises(ValidationError):self.post(phone=Device.objects.get(pk=phone.pk),reason='Lost / stolen')
        sold,_=self.buy_phone(self.a,'80000','100000',pay='full');self.pos(self.login_a,self.a,[(sold,1,'100000')],[dict(mode='Cash',amount='100000.00')])
        with self.assertRaises(ValidationError):self.post(phone=Device.objects.get(pk=sold.pk),reason='Lost / stolen')
    def test_a_cancelled_write_off_of_a_phone_in_holding_goes_back_to_holding(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
        sale=Document.objects.get(pk=self.pos(self.login_a,self.a,[(phone,1,'100000')],[dict(mode='Cash',amount='100000.00')]).json()['id'])
        returns_flow.settle(self.login_a,sale,[str(phone.pk)],None,returns_flow.CONDITIONS[2],returns_flow.REASONS[0],'credit')
        doc=self.post(phone=Device.objects.get(pk=phone.pk),reason='Damaged');phone.refresh_from_db();self.assertEqual(phone.status,'Written off')
        s.cancel(doc,self.login_a);phone.refresh_from_db();self.assertEqual(phone.status,'Holding')                                            # not straight back on sale
        self.assert_books_clean()
