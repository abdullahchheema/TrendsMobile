"""QA audit, phases 10 and 13: races. Real threads, real database connections, real row locks (these need PostgreSQL).

Each test lines several requests up behind a barrier and releases them at the same instant, several rounds over. Whatever the order
the database serves them in, the result must be the same: one winner, and books that still reconcile."""
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import IntegrityError,close_old_connections,connections
from django.test import Client,TransactionTestCase,override_settings
from django.urls import reverse
from .models import *
from . import money,online_orders as oo,online_store,shopdefaults,transfers,services as s
from .tests_audit_base import AuditMixin,P
from .tests_audit_online import KEY,SPECS


def together(jobs):
    """Run the callables at the same moment, each on its own thread and database connection. Returns each result or the exception it raised."""
    barrier=threading.Barrier(len(jobs))
    def run(job):
        close_old_connections()
        try:
            barrier.wait(timeout=20);return job()
        except Exception as error:return error
        finally:connections.close_all()
    with ThreadPoolExecutor(max_workers=len(jobs)) as pool:return list(pool.map(run,jobs))


@override_settings(ONLINE_API_KEY=KEY)
class Races(AuditMixin,TransactionTestCase):
    def setUp(self):
        self.build();self.capital(self.a,'5000000');self.capital(self.b,'1000000')
        Product.objects.filter(pk=self.phone_product.pk).update(specs=SPECS,slug='galaxy-a99-128-black')
        ProductImage.objects.create(product=self.phone_product,path='audit/a99.jpg',url='https://img.invalid/a99.jpg');self.phone_product.refresh_from_db();online_store.refresh_live(self.phone_product)
        OnlineSettings.objects.update_or_create(pk=1,defaults=dict(delivery_fee=Decimal('0'),free_delivery_from=None,cod_enabled=True))
        oo.online_branch();self.n=0
    def order_body(self,ref=None,phone=None):
        self.n+=1
        return dict(external_ref=ref or f'TM-RACE{self.n:05d}-ABCDEF',customer=dict(name='Racer',phone=phone or f'+92300{self.n:07d}',city='Lahore',address='Street 1',province='Punjab'),payment_method='Cash on Delivery',delivery_fee='0',
                    items=[dict(product_id=str(self.phone_product.pk),condition='Good',pta_status='PTA',device_type='Used',price='100000',quantity=1)])
    def place(self,body):
        return lambda:Client().post(reverse('api_online_order'),json.dumps(body),content_type='application/json',HTTP_X_API_KEY=KEY).status_code
    def counter_sale(self,user,branch,items,payments=()):
        def job():
            client=Client();client.force_login(user);return self.pos(user,branch,items,payments,client=client).status_code
        return job
    def clean(self):
        from . import books_check,online_check,tests_audit_invariants as inv
        problems=[f'{name}: {p}' for name,found in books_check.run_all() for p in found]+online_check.problems()+inv.all_problems();self.assertEqual(problems,[])

    def test_many_customers_order_the_last_phone_at_once(self):
        for round_ in range(3):
            phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
            results=together([self.place(self.order_body()) for _ in range(8)])
            self.assertEqual(sorted(results),[201]+[409]*7,results)
            phone.refresh_from_db();self.assertEqual(phone.status,'Reserved');self.assertEqual(OnlineAllocation.objects.filter(device=phone).count(),1)
            oo.cancel(OnlineOrder.objects.get(lines__allocations__device=phone),self.owner,'Race test clean-up');Device.objects.filter(pk=phone.pk).update(asking_price=Decimal('1'))      # out of the way for the next round
        self.clean()
    def test_the_same_order_sent_many_times_at_once_is_one_order(self):
        self.buy_phone(self.a,'80000','100000',pay='full');self.buy_phone(self.a,'80000','100000',pay='full')
        body=self.order_body(ref='TM-SAMEORDER01-ABCDEF');results=together([self.place(body) for _ in range(6)])
        self.assertEqual(sorted(results),[200]*5+[201],results);self.assertEqual(OnlineOrder.objects.count(),1);self.assertEqual(Device.objects.filter(status='Reserved').count(),1);self.clean()
    def test_a_counter_sale_and_an_online_order_for_the_same_phone(self):
        outcomes=[]
        for round_ in range(4):
            phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
            sale,order=together([self.counter_sale(self.login_a,self.a,[(phone,1,'100000')],[dict(mode='Cash',amount='100000.00')]),self.place(self.order_body())])
            outcomes.append((sale,order));self.assertIn((sale,order),[(200,409),(400,201)],(sale,order))             # exactly one of them got the phone
            phone.refresh_from_db();self.assertEqual(phone.status,'Sold' if sale==200 else 'Reserved')
            if order==201:oo.cancel(OnlineOrder.objects.get(lines__allocations__device=phone),self.owner,'Race test clean-up');Device.objects.filter(pk=phone.pk).update(asking_price=Decimal('1'))
        self.clean()
    def test_two_counters_sell_the_same_phone(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
        results=together([self.counter_sale(user,self.a,[(phone,1,'100000')],[dict(mode='Cash',amount='100000.00')]) for user in (self.login_a,self.owner,self.login_a,self.owner)])
        self.assertEqual(sorted(results),[200,400,400,400],results);self.assertEqual(Line.objects.filter(device=phone,document__kind__in=s.SELL,document__status='Submitted').count(),1);self.clean()
    def test_the_last_accessories_are_never_oversold(self):
        cover,_=self.buy_accessory(self.a,'Race cover',3,'100','250',pay='full')
        results=together([self.counter_sale(self.login_a if i%2 else self.owner,self.a,[(cover,1,'250')]) for i in range(7)])
        self.assertEqual(sorted(results),[200]*3+[400]*4,results);self.assertEqual(s.on_hand(cover,shopdefaults.warehouse(self.a)),(0,0));self.clean()
    def test_a_bill_is_never_paid_twice_over(self):
        cover,_=self.buy_accessory(self.a,'Race cover',10,'100','250',pay='full')
        body=self.pos(self.login_a,self.a,[(cover,4,'250')],customer='Racing Debtor').json();doc=Document.objects.get(pk=body['id'])
        def pay():
            pe=s.new_doc('Payment Entry',self.a,customer=doc.customer,reference=doc,handler=shopdefaults.shop_person(self.a),data=dict(amount='400.00',direction='Receive',mode='Cash'));return s.submit(pe,self.owner).code
        results=together([pay for _ in range(6)]);paid=[r for r in results if isinstance(r,str)]
        self.assertEqual(len(paid),2,results);doc.refresh_from_db();self.assertEqual(P(doc.outstanding),20000);self.clean()            # 1000 owed: two payments of 400 fit, the rest are refused
    def test_an_order_is_dispatched_and_delivered_once(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');order=oo.create(self.order_body())[0];oo.confirm(order,self.owner)
        results=together([lambda:(oo.dispatch(order,self.owner,'TCS','T-1'),'dispatched')[1] for _ in range(5)])
        self.assertEqual(len([r for r in results if isinstance(r,str)]),1,results);self.assertEqual(Document.objects.filter(online_order=order,kind__in=s.SELL).count(),0)      # posted at delivery (D4)
        results=together([lambda:oo.deliver(order,self.owner,'Cash').code for _ in range(5)])
        self.assertEqual(len([r for r in results if isinstance(r,str)]),1,results)
        self.assertEqual((Document.objects.filter(online_order=order,kind__in=s.SELL).count(),Document.objects.filter(online_order=order,kind='Payment Entry').count()),(1,1));self.clean()
    def test_a_payment_confirmed_many_times_at_once_is_booked_once(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');body=self.order_body();body['payment_method']='Bank Transfer'
        order=oo.create(body)[0]
        results=together([lambda:oo.mark_paid(order,self.owner,'TRX-RACE').code for _ in range(6)])
        self.assertEqual([r for r in results if not isinstance(r,str)],[]);self.assertEqual(Document.objects.filter(online_order=order,kind='Payment Entry').count(),1)      # one advance (D5)
        self.clean()
    def test_a_transfer_and_a_sale_of_the_same_phone(self):
        for round_ in range(3):
            phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
            moved,sold=together([lambda:transfers.create(self.owner,self.a,self.b,[phone]).code,self.counter_sale(self.login_a,self.a,[(phone,1,'100000')],[dict(mode='Cash',amount='100000.00')])])
            self.assertTrue((isinstance(moved,str) and sold==400) or (isinstance(moved,ValidationError) and sold==200),(moved,sold))
            phone.refresh_from_db();self.assertIn(phone.status,('Reserved','Sold'))
        self.clean()
    def test_a_refund_is_not_paid_twice(self):
        from . import returns_flow
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');body=self.pos(self.login_a,self.a,[(phone,1,'100000')],[dict(mode='Cash',amount='100000.00')]).json();sale=Document.objects.get(pk=body['id'])
        results=together([lambda:returns_flow.settle(self.owner,sale,[str(phone.pk)],None,returns_flow.CONDITIONS[0],returns_flow.REASONS[0],'refund','Cash','').code for _ in range(4)])
        self.assertEqual(len([r for r in results if isinstance(r,str)]),1,results)
        self.assertEqual(Document.objects.filter(kind='Payment Entry',customer=sale.customer,data__direction='Pay').count(),1);self.clean()
    def test_the_same_checkout_request_sent_twice_at_once(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full');key=uuid.uuid4()
        def job():
            client=Client();client.force_login(self.login_a);return self.pos(self.login_a,self.a,[(phone,1,'100000')],[dict(mode='Cash',amount='100000.00')],key=key,client=client).status_code
        results=together([job,job,job]);self.assertIn(200,results);self.assertEqual(Document.objects.filter(kind__in=s.SELL).count(),1)
        self.assertEqual(Document.objects.filter(kind='Payment Entry',customer__isnull=False).count(),1);self.clean()
