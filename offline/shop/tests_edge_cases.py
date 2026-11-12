"""Edge cases across the ERP and the website's order API: malformed requests, money that arrives as a JSON float, failed deliveries with a
delivery charge, one customer written two ways, stock held for online orders, and the consistency checks that must stay clean throughout."""
import json
from decimal import Decimal
from django.core.exceptions import ValidationError
from django.test import override_settings
from django.urls import reverse
from .models import *
from .tests_online_orders import OrderBase
from . import tests_online_money as real_stock      # imported as a module so its own tests are not collected a second time here
from . import books_check,online_accounts,online_check,online_orders as oo,shopdefaults,services as s

MONEY_CHECKS=(books_check.ledger_balanced_per_document,books_check.ledger_balanced_per_shop,books_check.trial_balance_balanced,books_check.party_balances_vs_invoices,books_check.document_totals_vs_ledger,
              books_check.cancelled_documents_net_zero,books_check.reports_vs_ledger,books_check.cash_and_bank_vs_history,books_check.dues_vs_balances)


def money_problems():
    """The accounting checks that do not depend on how the test stock was created."""
    return [p for check in MONEY_CHECKS for p in check()]


def all_problems():
    """Every books check plus the online order check: only meaningful when the stock came from real purchase documents."""
    return [f'{name}: {p}' for name,found in books_check.run_all() for p in found]+online_check.problems()


class ApiBase(OrderBase):
    def body(self,**extra):
        b=dict(external_ref='TM-EDGE0001-ABCDEF',customer=dict(name='Sara Ahmed',phone='+923211234567',city='Karachi',address='Street 1',province='Sindh'),payment_method='Cash on Delivery',delivery_fee='0',
               items=[dict(product_id=str(self.red.pk),condition='New',pta_status='PTA',device_type='New',price='100000',quantity=1)])
        b.update(extra);return b
    def send(self,body=None,raw=None):
        with override_settings(ONLINE_API_KEY='secret-key'):
            return self.client.post(reverse('api_online_order'),raw if raw is not None else json.dumps(body),content_type='application/json',HTTP_X_API_KEY='secret-key')
    def setUp(self):
        super().setUp();OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('0'),free_delivery_from=None,cod_enabled=True))
    def nothing_happened(self):
        self.assertEqual(OnlineOrder.objects.count(),0);self.assertEqual(Device.objects.filter(status='Reserved').count(),0);self.assertEqual(OnlineAllocation.objects.count(),0)


class MalformedRequests(ApiBase):
    """Whatever the website (or anyone holding the key) sends, the answer is a clear 4xx and nothing is reserved: never a server error."""
    def test_a_customer_that_is_not_an_object_is_refused(self):
        self.phone(self.branch)
        for bad in ('Sara',['Sara'],5,True):
            r=self.send(self.body(customer=bad));self.assertEqual(r.status_code,400,bad);self.assertIn('error',r.json())
        self.nothing_happened()
    def test_a_quantity_that_is_not_a_whole_number_is_refused(self):
        self.phone(self.branch);self.phone(self.branch)
        for bad in (1.5,True,'two','',None,[2],{'n':2},-1,0,51,'1.0'):
            b=self.body();b['items'][0]['quantity']=bad;r=self.send(b);self.assertEqual(r.status_code,400,bad)
        raw=json.dumps(self.body()).replace('"quantity": 1','"quantity": Infinity');self.assertIn('Infinity',raw);self.assertEqual(self.send(raw=raw).status_code,400)
        raw=json.dumps(self.body()).replace('"quantity": 1','"quantity": NaN');self.assertEqual(self.send(raw=raw).status_code,400)
        raw=json.dumps(self.body()).replace('"quantity": 1','"quantity": 1e400');self.assertEqual(self.send(raw=raw).status_code,400)
        self.nothing_happened()
    def test_a_whole_number_quantity_sent_as_text_or_as_a_whole_float_is_accepted(self):
        self.phone(self.branch);self.phone(self.branch)
        b=self.body();b['items'][0]['quantity']='1';self.assertEqual(self.send(b).status_code,201)
        b=self.body(external_ref='TM-EDGE0002-ABCDEF');b['items'][0]['quantity']=1.0;self.assertEqual(self.send(b).status_code,201)
    def test_bad_shapes_everywhere_are_refused_not_crashed(self):
        self.phone(self.branch)
        cases=[self.body(items='x'),self.body(items=[]),self.body(items=['x']),self.body(items=[[]]),self.body(items=None),self.body(shipping='TCS'),self.body(discount=[1]),self.body(discount={'amount':'abc'}),
               self.body(discount={'amount':-5}),self.body(expected_total={'x':1}),self.body(expected_total='abc'),self.body(delivery_fee='abc'),self.body(delivery_fee=-1),self.body(delivery_fee=[1]),
               self.body(payment_method={'a':1}),self.body(payment_method='Cheque'),self.body(customer=dict(name='',phone='+923211234567')),self.body(customer=dict(name='A',phone='123')),
               self.body(customer=dict(name={'x':1},phone=['+923211234567']))]
        for i,b in enumerate(cases):
            if b.get('shipping')=='TCS':continue                       # a plain-text shipping value is simply ignored (covered below)
            r=self.send(b);self.assertIn(r.status_code,(400,409),(i,r.content[:200]))
        import uuid
        for product_id in (None,'',5,'not-a-uuid',['x'],{'a':1},str(uuid.uuid4())):
            b=self.body();b['items'][0]['product_id']=product_id;r=self.send(b);self.assertIn(r.status_code,(400,409),product_id)
        for price in ('abc',None,'',[1],'NaN','Infinity','-100000','1e99','100000.001'):
            b=self.body();b['items'][0]['price']=price;r=self.send(b);self.assertIn(r.status_code,(400,409),price)
        self.nothing_happened()
    def test_a_body_that_is_not_json_or_is_absurdly_nested_is_refused(self):
        for raw in ('','not json','[1,2]','"text"','null','{"items":'+'['*5000+']'*5000+'}',b'\xff\xfe'):
            r=self.send(raw=raw);self.assertEqual(r.status_code,400,raw[:30])
        self.nothing_happened()
    def test_text_with_a_nul_character_or_of_any_length_never_breaks_the_order(self):
        self.phone(self.branch)
        r=self.send(self.body(customer=dict(name='Sara\u0000Ahmed',phone='+923211234567')));self.assertEqual(r.status_code,400);self.nothing_happened()
        long='x'*5000
        r=self.send(self.body(customer=dict(name=long,phone='+923211234567',email=long,address=long,city=long,postal_code=long,alt_phone=long),notes=long,payment_reference=long,shipping=dict(method=long)))
        self.assertEqual(r.status_code,201);o=OnlineOrder.objects.get();self.assertEqual((len(o.customer_name),len(o.city),len(o.postal_code),len(o.shipping_method)),(180,100,20,100))
    def test_a_wrong_or_missing_key_and_a_switched_off_api_answer_401(self):
        self.phone(self.branch)
        self.assertEqual(self.client.post(reverse('api_online_order'),json.dumps(self.body()),content_type='application/json').status_code,401)
        with override_settings(ONLINE_API_KEY='secret-key'):
            self.assertEqual(self.client.post(reverse('api_online_order'),json.dumps(self.body()),content_type='application/json',HTTP_X_API_KEY='secret-kez').status_code,401)
            self.assertEqual(self.client.post(reverse('api_online_order'),json.dumps(self.body()),content_type='application/json',HTTP_X_API_KEY='ключ').status_code,401)
            self.assertEqual(self.client.get(reverse('api_online_order')).status_code,405)
        with override_settings(ONLINE_API_KEY=''):
            self.assertEqual(self.client.post(reverse('api_online_order'),json.dumps(self.body()),content_type='application/json',HTTP_X_API_KEY='').status_code,401)
        self.nothing_happened()


class MoneyAsJsonNumbers(ApiBase):
    """JavaScript adds prices as binary floats, so a total can arrive as 269997.30000000005. That is the same money and must not be refused;
    a real third decimal still is."""
    def test_float_noise_in_the_total_the_fee_and_the_price_is_the_same_money(self):
        self.red.discount_percent=Decimal('10');self.red.save()
        for _ in range(3):self.phone(self.branch,price='99999')                       # 89,999.10 each on the website
        self.assertEqual(89999.1*3,269997.30000000005)                                # what the storefront's arithmetic produces
        b=self.body(delivery_fee=0.0,expected_total=89999.1*3);b['items'][0].update(price=89999.1,quantity=3)
        r=self.send(b);self.assertEqual(r.status_code,201,r.content);o=OnlineOrder.objects.get();self.assertEqual((o.subtotal,o.total),(Decimal('269997.30'),Decimal('269997.30')))
    def test_a_real_third_decimal_or_a_different_total_is_still_refused(self):
        self.phone(self.branch)
        r=self.send(self.body(expected_total=100000.005));self.assertIn(r.status_code,(400,409))
        r=self.send(self.body(expected_total=100000.01));self.assertEqual(r.status_code,409)
        r=self.send(self.body(expected_total=99999.99));self.assertEqual(r.status_code,409);self.nothing_happened()
    def test_the_answer_always_carries_two_decimals(self):
        self.phone(self.branch);r=self.send(self.body(expected_total=100000)).json();self.assertEqual((r['subtotal'],r['delivery_fee'],r['total']),('100000.00','0.00','100000.00'))


class FailedDeliveryWithACharge(OrderBase):
    """A refused cash-on-delivery parcel: the customer owes nothing, not even the delivery charge that was on the invoice."""
    def run_failed(self,**extra):
        d=self.phone(self.branch);o=self.order([self.item()],delivery_fee='500',**extra);inv=oo.dispatch(o,self.user,'TCS','TRK-1')[0]
        oo.delivery_failed(o,self.user,'Customer refused');credit=oo.receive_returned(o,self.user,shopdefaults.warehouse(self.branch),'Sealed box','approve')
        o.refresh_from_db();inv.refresh_from_db();credit.refresh_from_db();d.refresh_from_db();return d,o,inv,credit
    def test_a_refused_cash_on_delivery_parcel_leaves_no_debt_and_no_profit(self):
        d,o,inv,credit=self.run_failed()
        self.assertEqual((o.status,d.status,d.branch_id),('Returned','Available',self.branch.pk))
        self.assertEqual((inv.outstanding,credit.outstanding),(Decimal('0.00'),Decimal('0.00')))          # nothing is owed either way
        a=online_accounts.summary();self.assertEqual((a['pending'],a['credit_owed'],a['revenue'],a['cogs'],a['gross_profit']),(0,0,0,0,0));self.assertTrue(a['books_agree'])
        online=oo.online_branch();receivable=Account.objects.get(pk=ShopSettings.objects.get(branch=online).accounts['receivable'])
        self.assertEqual(s.account_balance(online,receivable),Decimal('0.00'))                           # the customer's account is clear in the ledger too
        self.assertEqual(online_check.problems(),[]);self.assertEqual(money_problems(),[])
    def test_a_refused_parcel_with_a_coupon_and_an_accessory_also_clears_to_zero(self):
        self.phone(self.branch);self.accessories(self.branch,4)
        o=self.order([self.item(),dict(product_id=str(self.cover.pk),quantity=2)],delivery_fee='350',discount=dict(amount='999',code='EID'));inv=oo.dispatch(o,self.user,'TCS','TRK-2')[0]
        self.assertEqual(inv.total,Decimal('100000')+Decimal('1600')-Decimal('999')+Decimal('350'))
        oo.delivery_failed(o,self.user,'Not reachable');oo.receive_returned(o,self.user,shopdefaults.warehouse(self.branch),'Sealed box','approve');inv.refresh_from_db()
        self.assertEqual(inv.outstanding,Decimal('0.00'));a=online_accounts.summary();self.assertEqual((a['pending'],a['credit_owed'],a['revenue'],a['cogs'],a['net_sales']),(0,0,0,0,0))
        self.assertEqual(s.on_hand(self.cover,shopdefaults.warehouse(self.branch))[0],4);self.assertEqual(online_check.problems(),[])
        self.assertEqual(money_problems(),[])
    def test_a_refused_prepaid_parcel_leaves_the_price_of_the_goods_as_a_credit_to_refund(self):
        d,o,inv,credit=self.run_failed(payment_method='Bank Transfer',payment_status='Paid',payment_reference='TXN-1')
        self.assertEqual((o.status,inv.outstanding,credit.outstanding),('Returned',Decimal('0.00'),Decimal('-100000.00')))   # the goods are owed back; the delivery that was paid for and attempted is not reversed
        a=online_accounts.summary();self.assertEqual((a['credit_owed'],a['pending'],a['revenue']),(Decimal('100000.00'),0,Decimal('500.00')))
        self.assertFalse(Document.objects.filter(kind='Journal Entry',data__has_key='charge_cancelled').exists());self.assertEqual(online_check.problems(),[])
    def test_a_delivered_order_never_has_its_delivery_charge_cancelled(self):
        self.phone(self.branch);o=self.order([self.item()],delivery_fee='500');inv=oo.dispatch(o,self.user,'TCS','T')[0];oo.deliver(o,self.user);inv.refresh_from_db()
        self.assertEqual(oo.cancel_uncollected_charges(inv,o,self.user),None);self.assertEqual(online_accounts.summary()['revenue'],Decimal('100500.00'))


class OneCustomerTwoWays(OrderBase):
    def test_the_same_mobile_number_written_two_ways_is_one_online_customer(self):
        self.phone(self.branch);self.phone(self.branch);online=oo.online_branch()
        a=self.order([self.item()],customer=dict(name='Ali Khan',phone='+92 300 1234567',city='Lahore'));b=self.order([self.item()],customer=dict(name='Ali Khan',phone='0300-1234567',city='Lahore'))
        oo.dispatch(a,self.user,'TCS','1');oo.dispatch(b,self.user,'TCS','2')
        self.assertEqual(Party.objects.filter(branch=online,kind='Customer').count(),1)
        self.assertEqual(Document.objects.filter(branch=online,kind='Sales Invoice').values('customer').distinct().count(),1)
    def test_different_numbers_stay_different_customers_even_with_one_name(self):
        self.phone(self.branch);self.phone(self.branch);online=oo.online_branch()
        a=self.order([self.item()],customer=dict(name='Ali Khan',phone='03001234567'));b=self.order([self.item()],customer=dict(name='Ali Khan',phone='03011234567'))
        oo.dispatch(a,self.user,'TCS','1');oo.dispatch(b,self.user,'TCS','2');self.assertEqual(Party.objects.filter(branch=online,kind='Customer').count(),2)


class HeldStock(OrderBase):
    def test_an_orphaned_reserved_phone_is_reported_by_the_check(self):
        d=self.phone(self.branch);self.assertEqual(online_check.problems(),[])
        Device.objects.filter(pk=d.pk).update(status='Reserved')                     # reserved by nothing: neither an order nor a transfer
        self.assertTrue(any(d.code in p for p in online_check.problems()))
    def test_a_phone_held_by_a_pending_transfer_is_not_reported(self):
        from . import transfers as tr
        d=self.phone(self.branch);tr.create(self.user,self.branch,self.other,[d]);self.assertEqual(online_check.problems(),[])
    def test_every_step_of_a_mixed_order_keeps_both_checks_clean(self):
        clean=lambda:(self.assertEqual(online_check.problems(),[]),self.assertEqual(money_problems(),[]))
        self.phone(self.branch,cost='80000');self.phone(self.other,cost='70000');self.accessories(self.branch,5);clean()
        o=self.order([self.item(qty=2),dict(product_id=str(self.cover.pk),quantity=3)],delivery_fee='400',discount=dict(amount='1000',code='EID'));clean()
        self.assertEqual(o.total,Decimal('200000')+Decimal('2400')-Decimal('1000')+Decimal('400'))
        inv=oo.dispatch(o,self.user,'TCS','T-1')[0];clean();self.assertEqual(inv.total,o.total)
        oo.set_stage(o,self.user,'In Transit');oo.deliver(o,self.user,'Cash');clean();inv.refresh_from_db();self.assertEqual(inv.outstanding,0)
        a=online_accounts.summary();self.assertTrue(a['books_agree']);self.assertEqual(a['owed_total'],Decimal('80000')+Decimal('70000')+Decimal('900'))
    def test_cancelling_after_a_reassign_releases_everything_once(self):
        self.phone(self.branch);self.phone(self.other);o=self.order([self.item()])
        target=self.other if OnlineAllocation.objects.get().branch_id==self.branch.pk else self.branch
        oo.reassign(o,self.user,target);oo.reassign(o,self.user,target);self.assertEqual(Device.objects.filter(status='Reserved').count(),1)
        oo.cancel(o,self.user,'Duplicate order');self.assertEqual((Device.objects.filter(status='Available').count(),OnlineAllocation.objects.count()),(2,0))
        with self.assertRaises(ValidationError):oo.cancel(o,self.user,'again')
        with self.assertRaises(ValidationError):oo.dispatch(o,self.user,'TCS','T')
        with self.assertRaises(ValidationError):oo.confirm(o,self.user)
        self.assertEqual(online_check.problems(),[])
    def test_actions_in_the_wrong_order_are_refused_and_change_nothing(self):
        self.phone(self.branch);o=self.order([self.item()],confirm=False)
        with self.assertRaises(ValidationError):oo.dispatch(o,self.user,'TCS','T')                 # not confirmed by phone yet
        for action in (lambda:oo.deliver(o,self.user),lambda:oo.delivery_failed(o,self.user,'x'),lambda:oo.set_stage(o,self.user,'In Transit'),lambda:oo.update_shipment(o,self.user,'TCS','T'),
                       lambda:oo.request_return(o,self.user,'x'),lambda:oo.receive_returned(o,self.user,shopdefaults.warehouse(self.branch),'ok','approve')):
            with self.assertRaises(ValidationError):action()
        o.refresh_from_db();self.assertEqual((o.status,oo.order_status(o),Document.objects.filter(online_order=o).count()),('New','Unconfirmed',0))
        oo.confirm(o,self.user);oo.dispatch(o,self.user,'TCS','T');oo.deliver(o,self.user)
        for action in (lambda:oo.deliver(o,self.user),lambda:oo.cancel(o,self.user,'x'),lambda:oo.dispatch(o,self.user,'TCS','T'),lambda:oo.delivery_failed(o,self.user,'x'),lambda:oo.mark_paid(o,self.user)):
            with self.assertRaises(ValidationError):action()
        self.assertEqual(Document.objects.filter(online_order=o,kind='Sales Invoice').count(),1);self.assertEqual(online_check.problems(),[])


class RealStockBooks(OrderBase):
    """The same journeys on stock that was really bought (receipt, supplier bill), so every one of the books checks applies, not only the money ones."""
    bought=real_stock.Money.bought;sell=real_stock.Money.sell
    def test_a_refused_cash_on_delivery_parcel_with_a_delivery_charge_keeps_every_check_clean(self):
        d,bill=self.bought();self.assertEqual(all_problems(),[])
        o=self.sell(delivery_fee='450');self.assertEqual(all_problems(),[])
        inv=oo.dispatch(o,self.user,'TCS','TRK-7')[0];self.assertEqual(all_problems(),[])
        oo.delivery_failed(o,self.user,'Customer refused');self.assertEqual(all_problems(),[])
        oo.receive_returned(o,self.user,shopdefaults.warehouse(self.branch),'Sealed box','approve');self.assertEqual(all_problems(),[])
        inv.refresh_from_db();d.refresh_from_db();self.assertEqual((inv.outstanding,d.status,d.purchase_cost),(Decimal('0.00'),'Available',Decimal('120000')))
        again=self.sell(delivery_fee='450');oo.dispatch(again,self.user,'TCS','TRK-8');oo.deliver(again,self.user);self.assertEqual(all_problems(),[])      # the same phone sells again, cleanly
        a=online_accounts.summary();self.assertEqual((a['revenue'],a['cogs'],a['pending']),(Decimal('135450.00'),Decimal('120000.00'),0))
    def test_a_delivered_order_returned_by_the_customer_and_refunded_keeps_every_check_clean(self):
        from . import returns
        d,bill=self.bought();o=self.sell(delivery_fee='450');inv=oo.dispatch(o,self.user,'TCS','TRK-9')[0];oo.deliver(o,self.user);oo.request_return(o,self.user,'Changed mind');self.assertEqual(all_problems(),[])
        credit=s.submit(returns.create(inv,self.user,[str(d.pk)],'Opened box','Changed mind',warehouse=shopdefaults.warehouse(self.other)),self.user);self.assertEqual(all_problems(),[])
        d.refresh_from_db();self.assertEqual((d.status,d.branch_id,credit.outstanding),('Holding',self.other.pk,Decimal('-135000.00')))      # came back into the other shop; only the goods are credited
        o.refresh_from_db();self.assertEqual(oo.display_stage(o),'Returned')


class ShopStockBase(OrderBase):
    """Accessories bought on real supplier bills, so the stock value, the supplier balance and the ledger can all be checked."""
    def buy(self,qty,rate,product=None):
        receipt=s.new_doc('Purchase Receipt',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer)
        Line.objects.create(document=receipt,product=product or self.cover,warehouse=self.warehouse,quantity=qty,rate=Decimal(rate))
        receipt=s.submit(receipt,self.user);return receipt,s.submit(s.supplier_bill(receipt,self.user),self.user)
    def sell(self,qty,rate='800',product=None):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=doc,product=product or self.cover,warehouse=self.warehouse,quantity=qty,rate=Decimal(rate));return s.submit(doc,self.user)
    def stock(self):return s.on_hand(self.cover,self.warehouse)
    def setUp(self):
        super().setUp();Party.objects.filter(pk__in=[self.customer.pk,self.supplier.pk]).update(branch=self.branch)


class AccessoriesHeldForOnlineOrders(ShopStockBase):
    """Units promised to a new online order are only counted, so nothing else may take the shelf below that count."""
    def setUp(self):
        super().setUp();self.receipt,self.bill=self.buy(5,'300');self.order_=self.order([dict(product_id=str(self.cover.pk),quantity=3)])
    def test_the_shop_can_sell_only_what_is_not_held(self):
        with self.assertRaises(ValidationError):self.sell(3)
        self.sell(2);self.assertEqual(self.stock()[0],3);self.assertEqual(all_problems(),[])
    def test_held_units_cannot_be_written_off_until_the_order_is_cancelled(self):
        from . import stock_adjust
        data=dict(branch=self.branch,warehouse=self.warehouse,posting_date=s.timezone.localdate(),reason='Damaged',product=self.cover,change=Decimal('-3'),notes='Water damage')
        with self.assertRaises(ValidationError) as caught:stock_adjust.post(data,self.user)
        self.assertIn('held for online orders',caught.exception.messages[0]);self.assertEqual(self.stock()[0],5)
        stock_adjust.post(dict(data,change=Decimal('-2')),self.user);self.assertEqual(self.stock()[0],3);self.assertEqual(all_problems(),[])
        oo.cancel(self.order_,self.user,'Item no longer available (stock problem)');stock_adjust.post(dict(data,change=Decimal('-3')),self.user);self.assertEqual(self.stock(),(0,0));self.assertEqual(all_problems(),[])
    def test_held_units_cannot_go_back_to_the_supplier(self):
        from . import purchase_returns
        line=self.receipt.lines.get()
        with self.assertRaises(ValidationError):purchase_returns.create(self.bill,self.user,[],{str(line.pk):'3'},'Wrong colour')
        self.assertEqual(self.stock()[0],5);purchase_returns.create(self.bill,self.user,[],{str(line.pk):'2'},'Wrong colour');self.assertEqual(self.stock()[0],3);self.assertEqual(all_problems(),[])
    def test_the_receipt_cannot_be_cancelled_while_its_units_are_held(self):
        with self.assertRaises(ValidationError):s.cancel(self.bill,self.user) if False else s.cancel(self.receipt,self.user)
        self.assertEqual(self.stock()[0],5);self.assertEqual(online_check.problems(),[])
    def test_the_order_still_ships_after_all_of_that(self):
        self.sell(2);inv=oo.dispatch(self.order_,self.user,'TCS','T-5')[0];oo.deliver(self.order_,self.user);self.assertEqual(self.stock(),(0,0));self.assertEqual(all_problems(),[])


class AverageCostAccessories(ShopStockBase):
    """Accessories are valued at their average cost. Stock that leaves at a different price than the average (back to a supplier, or a
    cancelled receipt) must not leave value behind on an empty shelf, or take the shelf's value below zero."""
    def test_returning_the_dearer_batch_to_its_supplier_leaves_no_value_on_an_empty_shelf(self):
        from . import purchase_returns
        a,bill_a=self.buy(5,'100');b,bill_b=self.buy(5,'200');self.assertEqual(self.stock(),(10,1500))
        self.sell(5);self.assertEqual(self.stock(),(5,750))                                  # sold at the average, 150 each
        purchase_returns.create(bill_b,self.user,[],{str(b.lines.get().pk):'5'},'Faulty batch');bill_b.refresh_from_db()
        self.assertEqual(self.stock(),(0,0));self.assertEqual(bill_b.outstanding,0)          # the supplier is credited what was paid (1,000); the shelf gives up what it held (750)
        self.assertEqual(all_problems(),[])
    def test_returning_part_of_a_batch_keeps_the_rest_at_the_average(self):
        from . import purchase_returns
        a,bill_a=self.buy(5,'100');b,bill_b=self.buy(5,'200');self.sell(3)                   # 7 left, worth 1,050
        purchase_returns.create(bill_b,self.user,[],{str(b.lines.get().pk):'5'},'Faulty batch');self.assertEqual(self.stock(),(2,300));self.assertEqual(all_problems(),[])
    def test_an_empty_shelf_never_carries_value(self):
        a,bill_a=self.buy(3,'100.10');self.sell(1);self.sell(1);self.sell(1);self.assertEqual(self.stock(),(0,0));self.assertEqual(all_problems(),[])
    def test_selling_the_last_units_takes_exactly_what_is_left(self):
        self.buy(1,'100.00');self.buy(2,'100.01');self.assertEqual(self.stock(),(3,Decimal('300.02')))       # 100.006666… each: three at the rounded average would take 300.03
        inv=self.sell(3);self.assertEqual(self.stock(),(0,0));self.assertEqual(inv.stock.get().value,Decimal('-300.02'));self.assertEqual(all_problems(),[])
    def test_cancelling_a_receipt_after_the_average_moved_leaves_no_value_behind(self):
        self.buy(5,'100');receipt=s.new_doc('Purchase Receipt',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer)
        Line.objects.create(document=receipt,product=self.cover,warehouse=self.warehouse,quantity=5,rate=Decimal('200'));receipt=s.submit(receipt,self.user)
        self.sell(5);self.assertEqual(self.stock(),(5,750));s.cancel(receipt,self.user);self.assertEqual(self.stock(),(0,0));self.assertEqual(all_problems(),[])
    def test_cancelling_a_receipt_at_the_average_books_no_difference(self):
        receipt=s.new_doc('Purchase Receipt',self.branch,supplier=self.supplier,warehouse=self.warehouse,buyer=self.buyer)
        Line.objects.create(document=receipt,product=self.cover,warehouse=self.warehouse,quantity=4,rate=Decimal('125'));receipt=s.submit(receipt,self.user)
        s.cancel(receipt,self.user);self.assertEqual(self.stock(),(0,0));self.assertFalse(Document.objects.filter(data__has_key='average_cost_difference').exists());self.assertEqual(all_problems(),[])
    def test_cancelling_a_stock_gain_after_sales_leaves_no_value_behind(self):
        from . import stock_adjust
        self.buy(4,'100');gain=stock_adjust.post(dict(branch=self.branch,warehouse=self.warehouse,posting_date=s.timezone.localdate(),reason='Count correction',product=self.cover,change=Decimal('2'),notes='Found two'),self.user)
        self.buy(2,'400');self.sell(6);self.assertEqual(self.stock()[0],2)                                  # the average is now 150, not the 100 the gain came in at
        s.cancel(gain,self.user);self.assertEqual(self.stock(),(0,0));self.assertEqual(all_problems(),[])


class ShopJourneys(OrderBase):
    """Counter journeys on really purchased phones, with every books check run after each step."""
    bought=real_stock.Money.bought
    def setUp(self):
        super().setUp();Party.objects.filter(pk__in=[self.customer.pk,self.supplier.pk]).update(branch=self.branch);self.customer.refresh_from_db();self.supplier.refresh_from_db()
    def draft(self,device,rate='135000'):
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.seller,warehouse=self.warehouse)
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=self.warehouse,quantity=1,rate=Decimal(rate));return doc
    def pay(self,invoice,amount,direction='Receive',mode='Cash'):
        return s.submit(s.new_doc('Payment Entry',self.branch,customer=self.customer,handler=self.handler,reference=invoice,data=dict(amount=str(amount),direction=direction,mode=mode)),self.user)
    def test_paying_more_than_the_bill_is_refused_and_the_phone_is_not_sold(self):
        d,_=self.bought();doc=self.draft(d)
        with self.assertRaises(ValidationError):s.checkout(doc,self.user,[dict(mode='Cash',amount='135000.01')],self.handler)
        d.refresh_from_db();doc.refresh_from_db();self.assertEqual((d.status,doc.status),('Available','Draft'));self.assertEqual(all_problems(),[])
        for bad in ([dict(mode='Cash',amount='-5')],[dict(mode='Cash',amount='0')],[dict(mode='Cheque',amount='10')],[dict(mode='Cash',amount='abc')],[dict(mode='Cash',amount='10.005')],'cash',[['Cash',5]]):
            with self.assertRaises(ValidationError):s.checkout(doc,self.user,bad,self.handler)
        d.refresh_from_db();self.assertEqual(d.status,'Available');self.assertEqual(all_problems(),[])
    def test_a_split_payment_a_balance_on_credit_and_its_settlement(self):
        d,_=self.bought();sale=s.checkout(self.draft(d),self.user,[dict(mode='Cash',amount='50000'),dict(mode='Bank Transfer',amount='35000')],self.handler)
        self.assertEqual(sale.outstanding,Decimal('50000'));self.assertEqual(all_problems(),[])
        with self.assertRaises(ValidationError):self.pay(sale,'50000.01')
        self.pay(sale,'20000');self.pay(sale,'30000','Receive','Bank Transfer');sale.refresh_from_db();self.assertEqual(sale.outstanding,0)
        with self.assertRaises(ValidationError):self.pay(sale,'1')
        self.assertEqual(all_problems(),[])
    def test_the_same_phone_cannot_be_sold_twice(self):
        d,_=self.bought();first=self.draft(d);second=self.draft(d);s.submit(first,self.user)
        with self.assertRaises(ValidationError):s.submit(second,self.user)
        twice=self.draft(d);Line.objects.create(document=twice,product=d.product,device=d,warehouse=self.warehouse,quantity=1,rate=Decimal('1'))
        with self.assertRaises(ValidationError):s.submit(twice,self.user)
        self.assertEqual(Document.objects.filter(kind='Sales Invoice',status='Submitted').count(),1);self.assertEqual(all_problems(),[])
    def test_a_paid_sale_returned_refunded_and_sold_again(self):
        from . import returns
        d,_=self.bought();sale=s.checkout(self.draft(d),self.user,[dict(mode='Cash',amount='135000')],self.handler)
        credit=s.submit(returns.create(sale,self.user,[str(d.pk)],'Like new','Changed mind'),self.user);self.assertEqual(credit.outstanding,Decimal('-135000'));self.assertEqual(all_problems(),[])
        with self.assertRaises(ValidationError):s.submit(returns.create(sale,self.user,[str(d.pk)],'Like new','Again'),self.user)          # it is already back
        with self.assertRaises(ValidationError):self.pay(credit,'135000.01','Pay')
        with self.assertRaises(ValidationError):self.pay(credit,'1000','Receive')                                                       # the shop owes the customer, not the other way round
        self.pay(credit,'100000','Pay');self.pay(credit,'35000','Pay');credit.refresh_from_db();self.assertEqual(credit.outstanding,0);self.assertEqual(all_problems(),[])
        d.refresh_from_db();self.assertEqual(d.status,'Holding')
        with self.assertRaises(ValidationError):s.submit(self.draft(d),self.user)                                                       # not for sale until it is inspected
        Device.objects.filter(pk=d.pk).update(status='Available');d.refresh_from_db();s.checkout(self.draft(d),self.user,[dict(mode='Cash',amount='135000')],self.handler);self.assertEqual(all_problems(),[])
    def test_an_unpaid_sale_returned_clears_the_debt_instead_of_paying_cash(self):
        from . import returns
        d,_=self.bought();sale=s.submit(self.draft(d),self.user);credit=s.submit(returns.create(sale,self.user,[str(d.pk)],'Sealed','Changed mind'),self.user)
        with self.assertRaises(ValidationError):self.pay(credit,'135000','Pay')                                                         # no refund while the original bill is unpaid
        returns.apply_credit(credit,'135000',self.user);sale.refresh_from_db();credit.refresh_from_db();self.assertEqual((sale.outstanding,credit.outstanding),(0,0));self.assertEqual(all_problems(),[])
        with self.assertRaises(ValidationError):returns.apply_credit(credit,'1',self.user)
    def test_cancelling_a_sale_needs_its_payment_reversed_first_and_then_frees_the_phone(self):
        d,_=self.bought();sale=s.checkout(self.draft(d),self.user,[dict(mode='Cash',amount='135000')],self.handler);payment=Document.objects.get(kind='Payment Entry',reference=sale)
        with self.assertRaises(ValidationError):s.cancel(sale,self.user)
        s.cancel(payment,self.user);s.cancel(sale,self.user);d.refresh_from_db();self.assertEqual((d.status,d.sale_id,d.customer_id),('Available',None,None));self.assertEqual(all_problems(),[])
        with self.assertRaises(ValidationError):s.cancel(sale,self.user)
    def test_a_locked_period_refuses_every_posting_and_changes_nothing(self):
        d,bill=self.bought();Branch.objects.filter(pk=self.branch.pk).update(lock_through=s.timezone.localdate())
        with self.assertRaises(ValidationError):s.submit(self.draft(d),self.user)
        with self.assertRaises(ValidationError):s.cancel(bill,self.user)
        order=self.order([dict(product_id=str(self.product.pk),condition='Not Inspected',pta_status='Not recorded',device_type='Used',price='135000',quantity=1)])
        with self.assertRaises(ValidationError):oo.dispatch(order,self.user,'TCS','T')                                                  # the shop that holds the phone is locked
        d.refresh_from_db();order.refresh_from_db();self.assertEqual((d.status,order.status),('Reserved','New'))
        self.assertEqual(Document.objects.filter(kind='Sales Invoice').exclude(status='Draft').count(),0);self.assertEqual(online_check.problems(),[])
    def test_a_sold_phone_cannot_go_back_to_the_supplier_but_an_unsold_one_can(self):
        from . import purchase_returns
        d,bill=self.bought();sale=s.submit(self.draft(d),self.user)
        with self.assertRaises(ValidationError):purchase_returns.create(bill,self.user,[str(d.pk)],{},'Faulty')
        d2,bill2=self.bought(n=50);purchase_returns.create(bill2,self.user,[str(d2.pk)],{},'Faulty');d2.refresh_from_db();bill2.refresh_from_db()
        self.assertEqual((d2.status,bill2.outstanding),('Returned to supplier',0));self.assertEqual(all_problems(),[])
        with self.assertRaises(ValidationError):purchase_returns.create(bill2,self.user,[str(d2.pk)],{},'Again')
    def test_a_transfer_with_a_missing_phone_written_off_keeps_every_check_clean(self):
        from . import transfers as tr
        a,_=self.bought(n=60);b,_=self.bought(n=70);doc=tr.create(self.user,self.branch,self.other,[a,b]);self.assertEqual(all_problems(),[])
        with self.assertRaises(ValidationError):s.submit(self.draft(a),self.user)                                                       # held for the transfer
        tr.dispatch(self.user,doc);self.assertEqual(all_problems(),[]);tr.receive(self.user,doc,[str(a.pk)]);self.assertEqual(all_problems(),[])
        tr.resolve(self.user,doc,'write_off','Lost by the rider');self.assertEqual(all_problems(),[])
        a.refresh_from_db();b.refresh_from_db();self.assertEqual((a.status,a.branch_id,b.status),('Available',self.other.pk,'Written off'))
        for action in (lambda:tr.dispatch(self.user,doc),lambda:tr.cancel(self.user,doc),lambda:tr.receive(self.user,doc),lambda:tr.resolve(self.user,doc,'return')):
            with self.assertRaises(ValidationError):action()


class OnlineReturnsAndExchanges(ShopStockBase):
    """After delivery: an exchange paid by the return credit, and a part return of a discounted order, on really purchased stock."""
    bought=real_stock.Money.bought
    PHONE=dict(condition='Not Inspected',pta_status='Not recorded',device_type='Used',price='135000',quantity=1)
    def phone_item(self):return dict(product_id=str(self.product.pk),**self.PHONE)
    def test_an_exchange_is_paid_by_the_return_credit_and_collects_no_cash(self):
        from . import returns
        self.bought(n=80);self.bought(n=90);self.assertEqual(all_problems(),[])
        first=self.order([self.phone_item()]);inv=oo.dispatch(first,self.user,'TCS','T-1')[0];oo.deliver(first,self.user);sold=inv.lines.get().device
        credit=s.submit(returns.create(inv,self.user,[str(sold.pk)],'Speaker fault','Exchange for another unit',warehouse=self.warehouse),self.user);self.assertEqual(all_problems(),[])
        cash_before=self.money(oo.online_branch(),'Cash')
        second,made=oo.create(dict(external_ref='MANUAL-EXCHANGE-1',customer=dict(name='Ali Khan',phone='0300 1234567'),payment_method='Cash on Delivery',items=[self.phone_item()]),self.user,'Manual',credit)
        self.assertTrue(made);sale=oo.dispatch(second,self.user,'TCS','T-2')[0];credit.refresh_from_db();self.assertEqual((sale.outstanding,credit.outstanding,sale.customer_id),(0,0,inv.customer_id))
        oo.deliver(second,self.user);self.assertEqual(self.money(oo.online_branch(),'Cash'),cash_before)                    # nothing more to collect
        self.assertEqual(all_problems(),[]);sold.refresh_from_db();self.assertEqual(sold.status,'Holding')
        a=online_accounts.summary();self.assertEqual((a['revenue'],a['cogs'],a['pending'],a['credit_owed']),(Decimal('135000'),Decimal('120000'),0,0))
    def test_part_of_a_discounted_order_comes_back_and_is_refunded_to_the_paisa(self):
        from . import returns
        self.bought(n=80);self.buy(4,'300')
        order=self.order([self.phone_item(),dict(product_id=str(self.cover.pk),quantity=3)],delivery_fee='300',discount=dict(amount='1000',code='EID'))
        self.assertEqual(order.total,Decimal('135000')+Decimal('2400')-Decimal('1000')+Decimal('300'))
        inv=oo.dispatch(order,self.user,'TCS','T-3')[0];oo.deliver(order,self.user);self.assertEqual(all_problems(),[])
        line=inv.lines.get(product=self.cover);share=Decimal(line.snapshot['pricing']['total'])/3
        credit=s.submit(returns.create(inv,self.user,[],'Unopened','Ordered too many',{str(line.pk):'2'},warehouse=self.warehouse),self.user)
        self.assertEqual(credit.outstanding,-s.money(share*2));self.assertEqual(self.stock()[0],3);self.assertEqual(all_problems(),[])
        with self.assertRaises(ValidationError):s.submit(returns.create(inv,self.user,[],'Unopened','Again',{str(line.pk):'2'},warehouse=self.warehouse),self.user)      # only one is left to return
        online=oo.online_branch();refund=s.new_doc('Payment Entry',online,customer=inv.customer,handler=shopdefaults.shop_person(online),reference=credit,channel='Online',online_order=order,data=dict(amount=str(-credit.outstanding),direction='Pay',mode='Cash'))
        s.submit(refund,self.user);credit.refresh_from_db();self.assertEqual(credit.outstanding,0);self.assertEqual(all_problems(),[])
        order.refresh_from_db();self.assertEqual(oo.display_stage(order),'Returned')
