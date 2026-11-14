"""QA audit, phases 7 and 9: the Online Store end to end, across the website's API, the online team's screens, the shops' stock and
both sets of books.

Every step is driven the way it happens for real (the order arrives through the API with the shared key; the online login presses the
buttons on the order page) and after every step the stock status, the catalogue the website reads, the two ledgers and the screens are
checked against figures worked out by the oracle."""
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase,override_settings
from django.urls import reverse
from .models import *
from . import online_accounts,online_orders as oo,online_store,shopdefaults,stock_adjust,services as s
from . import tests_audit_oracle as oracle
from .tests_audit_base import AuditMixin,P
from .tests_audit_calculations import record,shown

KEY='audit-shared-key-not-a-real-secret'
SPECS={'Display':{'Screen size':'6.6 inch'},'Performance':{'Chipset':'Audit chip','RAM':'8 GB','Operating system':'Android'},'Camera':{'Main camera':'50 MP'},'Battery':{'Battery':'5000 mAh'}}


class OnlineBase(AuditMixin,TestCase):
    def setUp(self):
        self.build();self.capital(self.a,'1000000');self.capital(self.b,'1000000')
        Product.objects.filter(pk=self.phone_product.pk).update(specs=SPECS,slug='galaxy-a99-128-black')
        ProductImage.objects.create(product=self.phone_product,path='audit/a99.jpg',url='https://img.invalid/a99.jpg');self.phone_product.refresh_from_db();online_store.refresh_live(self.phone_product);self.phone_product.refresh_from_db()
        OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('250'),free_delivery_from=None,cod_enabled=True))
        OnlineDeliveryRule.objects.create(province='Sindh',fee=Decimal('400'))
        self.online=oo.online_branch();self.n=0
        self.team=self.as_user(self.online_login)
    # ---- the website's side
    def body(self,phone_price='100000',qty=1,province='Punjab',fee='250',method='Cash on Delivery',**extra):
        self.n+=1
        data=dict(external_ref=f'TM-AUDIT{self.n:04d}-ABCDEF',customer=dict(name='Sara Ahmed',phone='+923211234567',city='Lahore',address='House 9, Street 2',province=province),payment_method=method,delivery_fee=fee,
                  items=[dict(product_id=str(self.phone_product.pk),condition='Good',pta_status='PTA',device_type='Used',price=phone_price,quantity=qty)])
        data.update(extra);return data
    def api(self,body,key=KEY,raw=None):
        with override_settings(ONLINE_API_KEY=KEY):
            headers={'HTTP_X_API_KEY':key} if key is not None else {}
            return self.client.post(reverse('api_online_order'),raw if raw is not None else json.dumps(body),content_type='application/json',**headers)
    def track(self,ref,key=KEY):
        with override_settings(ONLINE_API_KEY=KEY):return self.client.get(reverse('api_online_order_status',args=[ref]),**({'HTTP_X_API_KEY':key} if key else {}))
    def catalogue(self):
        """What the website reads: (price in paisa, quantity) rows of the public variants view for the phone."""
        with connection.cursor() as cursor:
            cursor.execute('SELECT price, quantity FROM web_variants WHERE product_id=%s ORDER BY price',[self.phone_product.pk]);return [(P(p),int(q)) for p,q in cursor.fetchall()]
    # ---- the online team's side
    def press(self,order,action,**fields):
        response=self.team.post(reverse('online_order',args=[order.pk]),dict(action=action,**fields),follow=True);order.refresh_from_db()
        return [str(m) for m in response.context['messages']] if response.context else []
    def clearing(self,branch):
        account=online_accounts._clearing(branch)
        return sum(P(e.debit)-P(e.credit) for e in LedgerEntry.objects.filter(branch=branch,account=account)) if account else 0
    def ledger_size(self):return (LedgerEntry.objects.count(),StockMovement.objects.count(),Document.objects.filter(status='Submitted').count())


class NormalSale(OnlineBase):
    def test_scenario_1_purchase_to_settlement(self):
        a=self.a;phone,bill=self.buy_phone(a,'80000','100000',pay='unpaid',supplier='Phone Wholesaler')
        self.assertEqual(self.catalogue(),[(P('100000'),1)])                                  # 6. the website shows it
        want=oracle.online_order([('100000',1,'80000')],oracle.delivery_fee('100000','250',None,'Punjab',{'Sindh':'400'}))
        before=self.ledger_size()
        response=self.api(self.body());self.assertEqual(response.status_code,201,response.content)          # 7-8. the customer orders; the ERP takes it
        answer=response.json();order=OnlineOrder.objects.get(code=answer['order'])
        self.assertEqual((P(answer['subtotal']),P(answer['delivery_fee']),P(answer['total'])),(want['subtotal'],want['delivery'],want['total']))
        record('Online order total','100000 phone + 250 delivery',want['total'],P(order.total))
        phone.refresh_from_db();self.assertEqual(phone.status,'Reserved');self.assertEqual(self.catalogue(),[])              # 9. the exact phone is held and off the website
        self.assertEqual(self.ledger_size(),before)                                           # a reservation is not a sale: nothing in any ledger
        self.assertEqual(self.pos(self.login_a,a,[(phone,1,'100000')]).status_code,400)       # the shop cannot sell a held phone at the counter
        # the shop does not see an order nobody has confirmed, and never sees the customer or the money
        shop=self.as_user(self.login_a);self.assertNotIn(order.code,shop.get(reverse('shop_online_orders')).content.decode())
        self.assertIn('Call the customer',' '.join(self.press(order,'dispatch',courier='TCS',tracking='T-1')))                # not before the confirmation call
        self.press(order,'confirm');self.assertIsNotNone(order.confirmed_at)                 # 10
        page=shop.get(reverse('shop_online_orders')).content.decode();self.assertIn(order.code,page);self.assertIn(phone.imeis.get().number,page)        # 11. the pack list
        self.assertIn('House 9',page)                                                          # where to send it (by design)
        for money_word in ('100,250','100250','100,000','100000','80,000','80000','Cash on Delivery','Unpaid'):self.assertNotIn(money_word,page)       # never a price, a cost or a payment
        self.assertNotIn(order.code,self.as_user(self.login_b).get(reverse('shop_online_orders')).content.decode())           # another shop sees nothing
        self.press(order,'dispatch',courier='TCS',tracking='T-1');self.assertEqual(order.status,'Dispatched')                   # 12
        # with the courier: nothing is sold before delivery (D4). The phone is still the shop's stock, held for this order; no money yet
        phone.refresh_from_db();self.assertEqual((phone.status,phone.branch_id,self.ledger_size()),('Reserved',a.pk,before))
        self.assertEqual((self.balance(a,'inventory'),self.clearing(a),self.balance(self.online,'Cash')),(P('80000'),0,0))
        self.assertEqual(P(online_accounts.summary()['pending']),want['total'])                                                # shown as cash on delivery still to collect
        self.assertEqual(self.track(order.external_ref).json()['order_status'],'Confirmed')
        self.assertIn('Only a new order',' '.join(self.press(order,'dispatch',courier='TCS',tracking='T-2')))                  # a second dispatch does nothing
        self.press(order,'deliver',method='Cash');self.assertEqual((order.status,order.payment_status),('Delivered','Paid'))    # 13
        invoice=Document.objects.get(online_order=order,kind__in=s.SELL,is_return=False)
        # 14. the Online Store's books, at delivery: the goods are income, the cost is owed to the shop, the cash is in
        self.assertEqual((invoice.branch_id,P(invoice.total),P(invoice.outstanding),invoice.posting_date),(self.online.pk,want['total'],0,self.today))
        self.assertEqual(self.balance(self.online,'receivable'),0);self.assertEqual(-self.balance(self.online,'sales'),want['subtotal']+want['delivery'])
        self.assertEqual(self.balance(self.online,'cogs'),want['shop_payable']);self.assertEqual(-self.clearing(self.online),want['shop_payable'])
        self.assertEqual(self.balance(self.online,'inventory'),0)                              # the Online Store never owns the phone
        # 15. the shop's books: the phone left at cost and the Online Store owes it that cost
        self.assertEqual(self.clearing(a),want['shop_payable']);self.assertEqual(self.balance(a,'inventory'),0);self.assertEqual(-self.balance(a,'sales'),0)
        record('Shop payable on an online sale','phone cost 80000',want['shop_payable'],self.clearing(a))
        phone.refresh_from_db();self.assertEqual((phone.status,phone.sale_id),('Sold',invoice.pk))
        self.assertEqual(self.balance(self.online,'Cash'),want['total'])
        self.press(order,'deliver',method='Cash');self.assertEqual(self.balance(self.online,'Cash'),want['total'])              # a repeated delivery does not collect or post twice
        self.assertEqual((Document.objects.filter(online_order=order,kind='Payment Entry').count(),Document.objects.filter(online_order=order,kind__in=s.SELL).count()),(1,1))
        # 16. the Online Dashboard figures
        data=online_accounts.summary()
        figures=[('Online goods sales',P(data['goods_sales']),want['subtotal']),('Online profit on goods',P(data['product_profit']),want['goods_profit']),('Delivery charges kept',P(data['delivery_kept']),want['delivery']),
                 ('Owed to shops',P(data['owed_total']),want['shop_payable']),('COD still to collect',P(data['pending']),0)]
        for name,actual,expected in figures:
            record(name,'one phone sold online at 100000, cost 80000',expected,actual);self.assertEqual(actual,expected,name)
        self.assertTrue(data['books_agree'])
        dashboard=self.team.get(reverse('online_dashboard'));self.assertEqual(dashboard.status_code,200);self.assertIn('20,000',dashboard.content.decode())            # the profit on the goods, as the online team sees it
        # 17. the Online Store settles with the shop: a part, the rest, and not a paisa more
        pay=lambda amount:self.team.post(reverse('online_accounts'),dict(action='pay_shop',shop=str(a.pk),amount=amount,method='Cash',shop_method='Cash',date=str(self.today)),follow=True)
        shop_cash=self.balance(a,'Cash');pay('30000');self.assertEqual((self.clearing(a),-self.clearing(self.online)),(P('50000'),P('50000')))
        pay('50000.01');self.assertEqual(self.clearing(a),P('50000'))                          # more than is owed: refused
        pay('50000');self.assertEqual((self.clearing(a),self.clearing(self.online)),(0,0));self.assertEqual(self.balance(a,'Cash')-shop_cash,P('80000'))
        pay('0.01');self.assertEqual(self.clearing(a),0)                                      # nothing left to settle
        record('Remaining shop payable after settlement','80000 - 30000 - 50000',0,self.clearing(a))
        self.assertEqual(self.balance(self.online,'Cash'),want['total']-P('80000'))
        # the shop's supplier is still owed by the shop: an online sale never touches the supplier's bill
        bill.refresh_from_db();self.assertEqual(P(bill.outstanding),P('80000'))
        self.assert_books_clean()                                                              # 18
    def test_an_order_from_two_shops_owes_each_shop_its_own_cost(self):
        one,_=self.buy_phone(self.a,'80000','100000',pay='full');two,_=self.buy_phone(self.b,'77000.50','100000',pay='full')
        order=OnlineOrder.objects.get(code=self.api(self.body(qty=2,fee='250')).json()['order'])
        self.press(order,'confirm');self.press(order,'dispatch',courier='Leopards',tracking='L-9');self.assertEqual((self.clearing(self.a),self.clearing(self.b)),(0,0))
        self.press(order,'deliver',method='Cash')
        self.assertEqual((self.clearing(self.a),self.clearing(self.b),-self.clearing(self.online)),(P('80000'),P('77000.50'),P('157000.50')))
        record('Payable to shop B for its own phone','cost 77000.50',P('77000.50'),self.clearing(self.b))
        self.assertEqual(Document.objects.filter(online_order=order,kind__in=s.SELL).count(),1);self.assert_books_clean()
    def test_settlement_cannot_be_made_from_money_the_online_store_does_not_hold(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');order=OnlineOrder.objects.get(code=self.api(self.body()).json()['order'])
        self.press(order,'confirm');self.press(order,'dispatch',courier='TCS',tracking='T-5')
        with self.assertRaisesMessage(ValidationError,'owes'):online_accounts.pay_shop(self.owner,self.a,'1','Cash','Cash')           # on its way: nothing is owed to the shop before delivery
        self.press(order,'deliver',method='Bank Transfer')                                                                       # the customer paid into the bank
        with self.assertRaisesMessage(ValidationError,'holds only'):online_accounts.pay_shop(self.owner,self.a,'80000','Cash','Cash')
        with self.assertRaises(ValidationError):online_accounts.pay_shop(self.login_a,self.a,'1','Cash','Cash')                 # a shop cannot pay itself from the Online Store
        self.assertEqual(self.clearing(self.a),P('80000'))


class FailedDelivery(OnlineBase):
    def dispatched(self,method='Cash on Delivery',**extra):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
        order=OnlineOrder.objects.get(code=self.api(self.body(method=method,**extra)).json()['order'])
        if method!='Cash on Delivery':self.press(order,'paid',reference='BANK-777')
        self.press(order,'confirm');self.press(order,'dispatch',courier='TCS',tracking='T-7');return phone,order
    def test_scenario_2_refused_cash_on_delivery_leaves_no_sale_no_debt_and_the_phone_at_its_original_cost(self):
        phone,order=self.dispatched();inventory=self.balance(self.a,'inventory')
        self.assertIn('Record the failed delivery',' '.join(self.press(order,'receive_returned',warehouse=str(shopdefaults.warehouse(self.a).pk),condition='Sealed',resale='hold')))
        self.press(order,'delivery_failed',reason='Customer refused the parcel');phone.refresh_from_db();self.assertEqual(phone.status,'Reserved')    # nothing moves until it is physically back
        self.assertEqual(self.balance(self.a,'inventory'),inventory)
        self.assertIn('failed',' '.join(self.press(order,'deliver',method='Cash')).lower());self.assertEqual(order.status,'Dispatched')
        self.assertIn('goes back to the shop that sent it',' '.join(self.press(order,'receive_returned',warehouse=str(shopdefaults.warehouse(self.b).pk),condition='Sealed box',resale='hold')))
        self.press(order,'receive_returned',condition='Sealed box',resale='hold');self.assertEqual(order.status,'Returned')
        phone.refresh_from_db();self.assertEqual((phone.status,phone.branch_id,P(phone.purchase_cost)),('Holding',self.a.pk,P('80000')));self.assertEqual(self.catalogue(),[])
        self.assertEqual(self.balance(self.a,'inventory'),inventory);self.assertEqual(inventory,P('80000'))          # it never left the books: still at what it cost
        self.assertEqual(Document.objects.filter(online_order=order).count(),0)                                     # nothing was sold, nothing reversed
        for name,value in (('Online receivable after a refused parcel',self.balance(self.online,'receivable')),('Online revenue after a refused parcel',-self.balance(self.online,'sales')),
                           ('Online cost of goods after a refused parcel',self.balance(self.online,'cogs')),('Owed to the shop after a refused parcel',self.clearing(self.a)),('Online cash after a refused parcel',self.balance(self.online,'Cash'))):
            record(name,'COD order of 100250 refused',0,value);self.assertEqual(value,0,name)
        self.assertEqual(self.clearing(self.online),0)
        self.assertEqual(self.pos(self.login_a,self.a,[(phone,1,'100000')]).status_code,400)   # a phone in Holding cannot be sold
        self.press(order,'receive_returned',condition='Again',resale='hold')              # a repeat does nothing
        self.assertEqual(Document.objects.filter(online_order=order).count(),0)
        self.press(order,'accept_returned');phone.refresh_from_db();self.assertEqual(phone.status,'Available');self.assertEqual(self.catalogue(),[(P('100000'),1)])
        self.assertEqual(self.track(order.external_ref).json()['order_status'],'Returned');self.assert_books_clean()
    def test_a_failed_prepaid_order_owes_the_customer_the_goods_and_keeps_the_delivery_as_documented(self):
        phone,order=self.dispatched(method='Bank Transfer');self.assertEqual(self.balance(self.online,'Bank Transfer'),P('100250'))
        self.assertEqual(-self.balance(self.online,'receivable'),P('100250'))                                      # the customer's advance (D5)
        self.press(order,'delivery_failed',reason='Wrong address');self.press(order,'receive_returned',condition='Opened',resale='hold')
        owed=sum(P(d.data['unallocated']) for d in oo.advances(order));self.assertEqual(owed,P('100000'))          # the price of the goods is owed back
        self.assertEqual(-self.balance(self.online,'receivable'),P('100000'));self.assertEqual(self.balance(self.online,'cogs'),0)
        self.assertEqual(-self.balance(self.online,'sales'),P('250'));record('Delivery kept on a failed prepaid order (documented policy)','fee 250',P('250'),-self.balance(self.online,'sales'))
        self.assertEqual(self.clearing(self.a),0);self.assert_books_clean()
    def test_a_prepaid_order_cannot_be_confirmed_or_shipped_before_the_payment_is_confirmed(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');order=OnlineOrder.objects.get(code=self.api(self.body(method='Easypaisa')).json()['order'])
        self.assertIn('payment has arrived first',' '.join(self.press(order,'confirm')));self.assertIsNone(order.confirmed_at)
        self.press(order,'dispatch',courier='TCS',tracking='T-8');self.assertEqual(order.status,'New')
        self.press(order,'paid',reference='EP-123');self.press(order,'paid',reference='EP-123')
        self.assertEqual((self.balance(self.online,'Easypaisa'),-self.balance(self.online,'receivable'),-self.balance(self.online,'sales')),(P('100250'),P('100250'),0))      # booked at once, as an advance, not income (D5)
        self.press(order,'confirm');self.press(order,'dispatch',courier='TCS',tracking='T-8')
        self.assertEqual(order.status,'Dispatched');self.assertEqual(Document.objects.filter(online_order=order,kind='Payment Entry').count(),1)      # the money is recorded once
        self.assert_books_clean()
        self.press(order,'deliver');self.assertEqual((self.balance(self.online,'Easypaisa'),self.balance(self.online,'receivable'),-self.balance(self.online,'sales')),(P('100250'),0,P('100250')))
        self.assertEqual(oo.advances(order),[]);self.assert_books_clean()
    def test_a_paid_order_that_is_cancelled_leaves_the_money_owed_back_until_it_is_refunded(self):
        from . import money
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');order=OnlineOrder.objects.get(code=self.api(self.body(method='JazzCash')).json()['order'])
        self.press(order,'paid',reference='JC-9');self.assertEqual(self.balance(self.online,'JazzCash'),P('100250'))
        self.press(order,'cancel',reason_choice='Customer changed their mind');self.assertEqual(order.status,'Cancelled')
        self.assertIn('Refund owed',[e.kind for e in order.events.all()]);self.assertEqual(-self.balance(self.online,'receivable'),P('100250'))
        customer=oo.advances(order)[0].customer;money.record_party_cash_back(self.owner,customer,'100250','JazzCash',self.today)
        self.assertEqual((self.balance(self.online,'JazzCash'),self.balance(self.online,'receivable'),oo.advances(order)),(0,0,[]));self.assert_books_clean()
    def test_a_damaged_returned_phone_can_be_written_off(self):
        """Marked damaged at receipt, the phone is not for sale but is still worth its cost in the shop. The owner must be able to write it off."""
        phone,order=self.dispatched();self.press(order,'delivery_failed',reason='Dropped by the courier')
        self.press(order,'receive_returned',condition='Screen broken',resale='damaged')
        phone.refresh_from_db();self.assertEqual(phone.status,'Unavailable');self.assertEqual(self.catalogue(),[])
        self.assertEqual(self.pos(self.login_a,self.a,[(phone,1,'100000')]).status_code,400)
        self.assert_books_clean()                                                              # a damaged phone waiting to be written off is not an error in the books
        inventory=self.balance(self.a,'inventory')
        stock_adjust.post(dict(branch=self.a,warehouse=phone.warehouse,posting_date=self.today,notes='Broken in transit',reason='Damaged',phone=phone,product=None,change=None,unit_cost=None),self.owner)
        phone.refresh_from_db();self.assertEqual(phone.status,'Written off');self.assertEqual(inventory-self.balance(self.a,'inventory'),P('80000'))
        self.assert_books_clean()
    def test_the_write_off_screen_offers_phones_that_are_in_stock_but_not_for_sale(self):
        phone,order=self.dispatched();self.press(order,'delivery_failed',reason='Dropped');self.press(order,'receive_returned',condition='Broken',resale='damaged')
        page=self.as_user(self.owner).get(reverse('stock_adjust'),{'branch':str(self.a.pk)});self.assertEqual(page.status_code,200);self.assertIn(str(phone.pk),page.content.decode())


def invoice_of(order):return Document.objects.get(online_order=order,kind__in=s.SELL,is_return=False)


class CustomerReturn(OnlineBase):
    def test_a_delivered_phone_returned_by_the_customer_goes_to_holding_and_the_money_is_owed_back(self):
        from . import returns_flow
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');order=OnlineOrder.objects.get(code=self.api(self.body()).json()['order'])
        self.press(order,'confirm');self.press(order,'dispatch',courier='TCS',tracking='T-3');self.press(order,'deliver',method='Cash')
        online_accounts.pay_shop(self.owner,self.a,'80000','Cash','Cash')                       # the shop was already settled
        with self.assertRaisesMessage(ValidationError,'has only'):                               # the Online Store cannot refund money it has already paid away
            returns_flow.settle(self.owner,invoice_of(order),[str(phone.pk)],None,returns_flow.CONDITIONS[0],returns_flow.REASONS[0],'refund','Cash','',shopdefaults.warehouse(self.b))
        phone.refresh_from_db();self.assertEqual(phone.status,'Sold')                           # and the refused refund took the whole return back with it
        from . import money
        money.record_voucher(self.owner,self.online,'capital',Account.objects.get(code='COA-3100'),'100000','Cash',self.today,'Owner tops up the Online Store')
        invoice=Document.objects.get(online_order=order,kind__in=s.SELL,is_return=False)
        credit=returns_flow.settle(self.online_login_user(),invoice,[str(phone.pk)],None,returns_flow.CONDITIONS[0],returns_flow.REASONS[0],'refund','Cash','',shopdefaults.warehouse(self.b))
        phone.refresh_from_db();self.assertEqual((phone.status,phone.branch_id,P(phone.purchase_cost)),('Holding',self.b.pk,P('80000')))   # it may come back into any shop, at its original cost
        self.assertEqual(self.balance(self.online,'Cash'),P('100250')-P('80000')+P('100000')-P('100000'))
        self.assertEqual((self.clearing(self.a),self.clearing(self.b)),(0,-P('80000')))        # shop B now holds a phone it has not paid for: it owes the Online Store its cost
        order.refresh_from_db();self.assertIsNotNone(order.returned_on);self.assertEqual(oo.order_status(order),'Returned')
        with self.assertRaises(ValidationError):returns_flow.settle(self.owner,invoice,[str(phone.pk)],None,returns_flow.CONDITIONS[0],returns_flow.REASONS[0],'refund','Cash','')       # the same phone cannot come back twice
        self.assert_books_clean()
    def online_login_user(self):return self.owner


class ApiRules(OnlineBase):
    """The order API works everything out again from the ERP's own data. Whatever the caller sends, a wrong order changes nothing."""
    def setUp(self):
        super().setUp();self.phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
    def untouched(self):
        self.phone.refresh_from_db();self.assertEqual(self.phone.status,'Available');self.assertEqual(OnlineOrder.objects.count(),0);self.assertEqual(OnlineAllocation.objects.count(),0)
    def test_the_key_is_required_and_compared_exactly(self):
        for key in (None,'','wrong',KEY+' ',KEY[:-1],KEY.upper()):self.assertEqual(self.api(self.body(),key=key).status_code,401,key)
        with override_settings(ONLINE_API_KEY=''):self.assertEqual(self.client.post(reverse('api_online_order'),json.dumps(self.body()),content_type='application/json',HTTP_X_API_KEY='').status_code,401)   # no key configured = switched off
        self.assertEqual(self.track('anything',key=None).status_code,401)
        with override_settings(ONLINE_API_KEY=KEY):
            self.assertEqual(self.client.get(reverse('api_online_config')).status_code,401);self.assertEqual(self.client.get(reverse('api_online_order'),HTTP_X_API_KEY=KEY).status_code,405)
        self.untouched()
    def test_tampered_prices_fees_and_totals_are_refused(self):
        cases=[('lower price',self.body(phone_price='1')),('one paisa less',self.body(phone_price='99999.99')),('no delivery fee',self.body(fee='0')),('wrong province fee',self.body(province='Sindh',fee='250')),
               ('negative fee',self.body(fee='-250')),('wrong total',self.body(expected_total='250')),('more than in stock',self.body(qty=2)),('zero quantity',self.body(qty=0)),('negative quantity',self.body(qty=-1)),
               ('unknown payment method',self.body(method='Bitcoin')),('no name',self.body(customer=dict(name='',phone='+923211234567'))),('short phone',self.body(customer=dict(name='A B',phone='12345'))),
               ('discount above the items',self.body(discount=dict(amount='100000.01',code='X'))),('negative discount',self.body(discount='-1'))]
        for name,body in cases:
            status=self.api(body).status_code;self.assertIn(status,(400,409),name)
        self.untouched()
    def test_the_fee_follows_the_province_and_the_free_delivery_amount(self):
        cfg=OnlineSettings.get();cfg.free_delivery_from=Decimal('100000');cfg.save()
        self.assertEqual(self.api(self.body(fee='250')).status_code,409)                        # exactly at the threshold: delivery is free, so 250 is wrong
        ok=self.api(self.body(fee='0'));self.assertEqual(ok.status_code,201);record('Free delivery at the threshold','items 100000, free from 100000',0,P(ok.json()['delivery_fee']))
        cfg.free_delivery_from=Decimal('100000.01');cfg.save();other,_=self.buy_phone(self.a,'80000','100000',pay='full')
        self.assertEqual(self.api(self.body(fee='0')).status_code,409)                          # one paisa below the threshold: the fee applies
        sindh=self.api(self.body(province='sindh',fee='400'));self.assertEqual(sindh.status_code,201);self.assertEqual(P(sindh.json()['delivery_fee']),oracle.delivery_fee('100000','250','100000.01','sindh',{'Sindh':'400'}))
    def test_unpublished_sold_held_and_holding_phones_cannot_be_ordered(self):
        Product.objects.filter(pk=self.phone_product.pk).update(online_visible=False);self.assertEqual(self.api(self.body()).status_code,409)
        Product.objects.filter(pk=self.phone_product.pk).update(online_visible=True)
        for status in ('Sold','Reserved','Holding','In transit','Written off','Unavailable'):
            Device.objects.filter(pk=self.phone.pk).update(status=status);self.assertEqual(self.api(self.body()).status_code,409,status)
        Device.objects.filter(pk=self.phone.pk).update(status='Available');self.untouched()
    def test_cash_on_delivery_switched_off_is_refused(self):
        cfg=OnlineSettings.get();cfg.cod_enabled=False;cfg.save();self.assertEqual(self.api(self.body()).status_code,409);self.untouched()
    def test_the_same_reference_is_the_same_order(self):
        body=self.body();first=self.api(body);again=self.api(body);self.assertEqual((first.status_code,again.status_code),(201,200))
        self.assertEqual(first.json()['order'],again.json()['order']);self.assertFalse(again.json()['created']);self.assertEqual(OnlineOrder.objects.count(),1);self.assertEqual(OnlineAllocation.objects.count(),1)
        changed=dict(body,items=[dict(body['items'][0],quantity=1,price='5')]);third=self.api(changed);self.assertEqual(third.status_code,200);self.assertEqual(OnlineOrder.objects.count(),1)
    def test_tracking_gives_status_only(self):
        ref=self.api(self.body()).json()['external_ref'];answer=self.track(ref);self.assertEqual(answer.status_code,200);text=json.dumps(answer.json())
        for private in ('Sara','3211234567','House 9','Lahore','80000'):self.assertNotIn(private,text)
        self.assertEqual(self.track('TM-NOSUCHORDER-ABCDEF').status_code,404)
    def test_an_order_too_fast_from_one_phone_number_is_slowed_down(self):
        for _ in range(3):self.buy_phone(self.a,'80000','100000',pay='full')
        codes=[self.api(self.body()).status_code for _ in range(4)];self.assertEqual(codes,[201,201,201,429])
    def test_what_the_api_trusts_from_whoever_holds_the_key(self):
        """A prepaid order from the website always starts unpaid: its money is booked only when someone confirms it arrived (D5). A coupon discount
        sent with the key is still accepted (documented behaviour, safe only while the key stays on the website's server)."""
        paid=self.api(self.body(method='Bank Transfer',payment_status='Paid'));self.assertEqual(paid.status_code,201)
        self.assertEqual((OnlineOrder.objects.get().payment_status,Document.objects.filter(kind='Payment Entry',online_order__isnull=False).count()),('Unpaid',0))
        self.buy_phone(self.a,'80000','100000',pay='full')
        cheap=self.api(self.body(discount=dict(amount='99000',code='STAFF'),expected_total='1250'));self.assertEqual(cheap.status_code,201)
    def test_the_public_catalogue_views_show_nothing_private(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_name IN ('web_families','web_listings','web_variants','web_images') ORDER BY 1,2");columns=cursor.fetchall()
        self.assertEqual({t for t,_ in columns},{'web_families','web_listings','web_variants','web_images'})
        for table,column in columns:
            for word in ('cost','purchase','supplier','customer','imei','branch','shop','warehouse','minimum','wholesale','margin','profit'):self.assertNotIn(word,column,f'{table}.{column}')
