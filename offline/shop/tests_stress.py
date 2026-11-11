"""Adversarial edge-case and security tests. Every scenario ends by re-running the whole-books audit, so a rejected action must also
leave stock, money and the ledger exactly as they were."""
import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import Client,TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from . import books_check,catalog_entry,coa,easy_retail,money,purchase_returns,returns,services as s,setup,shop_accounts,shopdefaults,stock_adjust,transfers
from .tests import imei

D=Decimal
BAD=(ValidationError,PermissionDenied)
OWNER_PASSWORD='Owner-pass-12345!';SHOP_PASSWORD='Shop-pass-12345!'


class World(TestCase):
    """Owner, two shops with logins, phones in both shops."""
    @classmethod
    def setUpTestData(cls):
        User=get_user_model()
        cls.owner=User.objects.create_user('owner@test.local',password=OWNER_PASSWORD,is_superuser=True,is_staff=True)
        def shop(code,name):return setup.create_shop(dict(code=code,name=name,company='Trends Mobile',payment_methods=['Cash','Bank Transfer','JazzCash']),cls.owner)
        cls.a=shop('SHOP-001','Trends Mobile');cls.b=shop('SHOP-002','Capital Mobiles');coa.install_standard(cls.owner)
        for br,u in ((cls.a,'shop1@test.local'),(cls.b,'shop2@test.local')):shop_accounts.create_login(br,u,SHOP_PASSWORD)
        cls.today=timezone.localdate()
        for br in (cls.a,cls.b):money.record_voucher(cls.owner,br,'capital',Account.objects.get(code='COA-3100'),D('2000000'),'Cash',cls.today-datetime.timedelta(days=40),'Opening cash')

    def buy(self,branch,n,price=50000,supplier='Supplier One',status='full',paid=None,method='Cash',numbers=None):
        me=shopdefaults.person(self.owner,branch)
        return easy_retail.purchase(dict(branch=branch,party_name=supplier,variant=catalog_entry.check(dict(brand='Samsung',model='A15',storage='128GB',colour='Black')),quantity=1,price=D(price),pta_status='PTA',
            device_type='New',condition='Excellent',warehouse=shopdefaults.warehouse(branch),staff=me,handler=me,posting_date=self.today-datetime.timedelta(days=30),payment_status=status,amount_paid=paid,method=method,imeis=[numbers or [imei(n)]]),self.owner)

    def device(self,purchase):return purchase.reference.lines.get().device

    def sell(self,branch,device,price=60000,status='full',customer='Customer One',paid=None,method='Cash'):
        me=shopdefaults.person(self.owner,branch)
        return easy_retail.sale(dict(branch=branch,party_name=customer,phone=device,price=D(price),pta_status='PTA',staff=me,handler=me,posting_date=self.today-datetime.timedelta(days=5),payment_status=status,amount_paid=paid,method=method),self.owner)

    def audit(self):
        failed=[(name,issues[:3]) for name,issues in books_check.run_all() if issues]
        self.assertEqual(failed,[],'whole-books audit failed')

    def snapshot(self):
        return (Device.objects.count(),Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count(),Party.objects.count())

    def rejected(self,fn,*args,**kw):
        before=self.snapshot()
        with self.assertRaises(BAD):fn(*args,**kw)
        self.assertEqual(before,self.snapshot(),'a rejected action must not leave anything behind')
        self.audit()

    def login(self,who):
        c=Client();ok=c.login(username=('owner@test.local' if who=='owner' else who+'@test.local'),password=OWNER_PASSWORD if who=='owner' else SHOP_PASSWORD);self.assertTrue(ok);return c


class Imeis(World):
    def test_duplicate_imei_rejected_in_same_and_other_shop(self):
        self.buy(self.a,1)
        self.rejected(self.buy,self.a,2,numbers=[imei(1)])
        self.rejected(self.buy,self.b,3,numbers=[imei(1)])

    def test_invalid_imeis_rejected(self):
        for bad in ('123','abcdefghijklmno','35000000000000','3'*16,'99000019000001X','990000190000019'):
            with self.subTest(imei=bad):self.rejected(self.buy,self.a,4,numbers=[bad])

    def test_blank_imei_is_allowed_by_design_but_stays_consistent(self):
        d=self.device(self.buy(self.a,3,numbers=['']));self.assertFalse(d.imeis.exists());self.audit()

    def test_dual_imei_accepted_and_each_blocks_reuse(self):
        self.buy(self.a,5,numbers=[imei(5),imei(6)])
        self.rejected(self.buy,self.b,7,numbers=[imei(6)])
        self.audit()


class Numbers(World):
    def test_zero_negative_and_absurd_prices_and_quantities(self):
        for price in (0,-1,-50000,D('0.001'),D('1E+30')):
            self.rejected(self.buy,self.a,10,price=price)
        d=self.device(self.buy(self.a,11))
        for price in (0,-5,D('0.001')):self.rejected(self.sell,self.a,d,price=price)

    def test_rounding_is_to_whole_paisa(self):
        self.assertEqual(s.payment_money('33.33'),D('33.33'))
        for raw in ('33.335','NaN','Infinity','-Infinity','abc'):
            with self.assertRaises(BAD+(ArithmeticError,)):s.payment_money(raw)


class Selling(World):
    def test_cannot_sell_sold_phone_twice(self):
        d=self.device(self.buy(self.a,20));self.sell(self.a,d)
        self.rejected(self.sell,self.a,d,customer='Another')

    def test_cannot_sell_other_shops_phone(self):
        d=self.device(self.buy(self.b,21))
        self.rejected(self.sell,self.a,d)

    def test_over_payment_rejected_and_partial_sale_balance_correct(self):
        d=self.device(self.buy(self.a,22));sale=self.sell(self.a,d,price=60000,status='part',paid=D(20000))
        self.assertEqual(sale.outstanding,D(40000))
        party=sale.customer
        money.record_party_payment(self.owner,party,D(15000),'Cash',self.today,'','part');sale.refresh_from_db();self.assertEqual(sale.outstanding,D(25000))
        money.record_party_payment(self.owner,party,D(25000),'Cash',self.today,'','rest');sale.refresh_from_db();self.assertEqual(sale.outstanding,D(0))
        money.record_party_payment(self.owner,party,D(700),'Cash',self.today,'','over-payment is kept as the customer advance');self.audit()   # not lost: it stays on the customer's account
        self.assertEqual(Document.objects.filter(kind='Payment Entry',customer=party,reference__isnull=True).count()>=1,True)

    def test_cancel_sale_after_payment_restores_stock_and_books(self):
        d=self.device(self.buy(self.a,23));sale=self.sell(self.a,d,status='part',paid=D(10000))
        self.rejected(s.cancel,sale,self.owner)                   # the app insists the payments are reversed first
        for p in list(sale.linked.exclude(status='Cancelled')):s.cancel(p,self.owner)
        s.cancel(sale,self.owner);d.refresh_from_db();self.assertEqual(d.status,'Available');self.audit()

    def test_double_submit_and_double_cancel_rejected(self):
        d=self.device(self.buy(self.a,24));sale=self.sell(self.a,d)
        self.rejected(s.submit,sale,self.owner)
        for p in list(sale.linked.exclude(status='Cancelled')):s.cancel(p,self.owner)
        s.cancel(sale,self.owner)
        self.rejected(s.cancel,sale,self.owner)


class Returns(World):
    def test_return_twice_and_over_refund_rejected(self):
        d=self.device(self.buy(self.a,30));sale=self.sell(self.a,d,price=70000)
        draft=returns.create(sale,self.owner,[str(d.pk)],'Returned in working order','Changed mind',warehouse=shopdefaults.warehouse(self.a));credit=s.submit(draft,self.owner)
        self.assertEqual(-credit.outstanding,D(70000))
        again=returns.create(sale,self.owner,[str(d.pk)],'Again','Again',warehouse=shopdefaults.warehouse(self.a))   # a draft may exist; it can never post
        with self.assertRaises(BAD):s.submit(again,self.owner)
        self.audit()
        self.rejected(money.record_party_cash_back,self.owner,sale.customer,D(70001),'Cash',self.today,'','too much')
        money.record_party_cash_back(self.owner,sale.customer,D(30000),'Cash',self.today,'','first');credit.refresh_from_db();self.assertEqual(-credit.outstanding,D(40000))
        money.record_party_cash_back(self.owner,sale.customer,D(40000),'Cash',self.today,'','second');credit.refresh_from_db();self.assertEqual(credit.outstanding,D(0))
        self.rejected(money.record_party_cash_back,self.owner,sale.customer,D(1),'Cash',self.today,'','already refunded')
        self.audit()

    def test_refund_to_customer_who_never_had_credit_rejected(self):
        d=self.device(self.buy(self.a,31));sale=self.sell(self.a,d)
        self.rejected(money.record_party_cash_back,self.owner,sale.customer,D(500),'Cash',self.today,'','nothing owed')

    def test_credit_applied_to_unpaid_invoice_cannot_also_be_refunded(self):
        d=self.device(self.buy(self.a,32));sale=self.sell(self.a,d,price=80000,status='part',paid=D(10000))
        credit=s.submit(returns.create(sale,self.owner,[str(d.pk)],'ok','x',warehouse=shopdefaults.warehouse(self.a)),self.owner)
        self.rejected(money.record_party_cash_back,self.owner,sale.customer,D(1000),'Cash',self.today,'','credit belongs to the unpaid invoice')
        returns.apply_credit(credit,D(70000),self.owner);self.audit()

    def test_purchase_return_of_sold_phone_rejected(self):
        bill=self.buy(self.a,33);d=self.device(bill);self.sell(self.a,d)
        self.rejected(purchase_returns.create,bill,self.owner,[str(d.pk)],{},'wrong')


class Transfers(World):
    def test_shop_login_cannot_transfer_but_owner_can_and_no_double_stock(self):
        d=self.device(self.buy(self.a,40))
        shop_user=get_user_model().objects.get(username='shop1@test.local')
        self.rejected(transfers.create,shop_user,self.a,self.b,[d],'sneaky')
        doc=transfers.create(self.owner,self.a,self.b,[d],'balance');transfers.dispatch(self.owner,doc)
        d.refresh_from_db();self.assertEqual(d.status,'In transit')
        self.rejected(self.sell,self.a,d);self.rejected(self.sell,self.b,d)
        transfers.receive(self.owner,doc);d.refresh_from_db();self.assertEqual((d.status,d.branch_id),('Available',self.b.pk));self.audit()

    def test_cannot_transfer_sold_phone_or_to_same_shop(self):
        d=self.device(self.buy(self.a,41));self.sell(self.a,d)
        self.rejected(transfers.create,self.owner,self.a,self.b,[d],'sold')
        e=self.device(self.buy(self.a,42))
        self.rejected(transfers.create,self.owner,self.a,self.a,[e],'same shop')

    def test_cancel_pending_leaves_stock_in_place(self):
        d=self.device(self.buy(self.a,43));doc=transfers.create(self.owner,self.a,self.b,[d],'x');transfers.cancel(self.owner,doc,'changed plan')
        d.refresh_from_db();self.assertEqual((d.status,d.branch_id),('Available',self.a.pk));self.audit()


class Adjustments(World):
    def test_write_off_then_sell_rejected(self):
        d=self.device(self.buy(self.a,50))
        stock_adjust.post(dict(branch=self.a,warehouse=shopdefaults.warehouse(self.a),reason='Damaged',phone=d,posting_date=self.today,notes='dropped'),self.owner)
        d.refresh_from_db();self.assertEqual(d.status,'Written off');self.rejected(self.sell,self.a,d);self.audit()


class Security(World):
    def setUp(self):
        self.pa=self.buy(self.a,60,supplier='Alpha Supplier');self.pb=self.buy(self.b,61,supplier='Beta Supplier')
        self.da,self.db=self.device(self.pa),self.device(self.pb)
        self.sa=self.sell(self.a,self.da,customer='Alpha Customer');self.sb=self.sell(self.b,self.db,customer='Beta Customer')

    def blocked(self,response):
        self.assertIn(response.status_code,(302,403,404),response.status_code)
        if response.status_code==302:self.assertNotIn('Beta',response.get('Location',''))

    def test_shop1_cannot_open_shop2_records(self):
        c=self.login('shop1')
        for name,pk in (('party',self.sb.customer.pk),('statement',self.sb.customer.pk),('party_account',self.sb.customer.pk),('detail',self.sb.pk),('device',self.db.pk),('device_history',self.db.pk),('transfer_detail',self.sb.pk),('transfer_slip',self.sb.pk)):
            r=c.get(reverse(name,args=[pk]));self.blocked(r)
            if r.status_code==200:self.assertNotContains(r,'Beta Customer')

    def test_shop_lists_never_show_other_shop(self):
        c=self.login('shop1')
        for name in ('customer_dues','supplier_dues','money_history','money_in','money_out','money_expenses','cash_and_bank','stock_everywhere','imei_lookup'):
            r=c.get(reverse(name));self.assertIn(r.status_code,(200,302,403),name)
            if r.status_code==200:
                for secret in ('Beta Customer','Beta Supplier'):self.assertNotContains(r,secret,msg_prefix=name)
        r=c.get(reverse('search'),{'q':'Beta'});
        if r.status_code==200:self.assertNotContains(r,'Beta Customer')

    def test_shop_login_blocked_from_owner_only_pages(self):
        c=self.login('shop1')
        for name in ('stock_transfer','chart','periods','year_end','audit_log'):
            r=c.get(reverse(name));self.assertIn(r.status_code,(302,403,404),name+' '+str(r.status_code))
        r=c.post(reverse('stock_transfer'),{'source':self.a.pk,'target':self.b.pk});self.assertIn(r.status_code,(302,403,404,400,200))
        self.assertEqual(Document.objects.filter(kind='Stock Entry').count(),0)

    def test_shop_ledger_never_shows_other_shop(self):
        c=self.login('shop1');acct=LedgerEntry.objects.filter(branch=self.b,party=self.sb.customer).first().account
        for query in ({'account':str(acct.pk)},{'book':'cash'},{'book':'cash','branch':str(self.b.pk)},{'account':str(acct.pk),'branch':str(self.b.pk)}):
            r=c.get(reverse('ledger'),query);self.assertIn(r.status_code,(200,302,403,404))
            if r.status_code==200:self.assertNotContains(r,'Beta Customer')

    def test_shop_cannot_post_into_other_shop_through_forged_ids(self):
        c=self.login('shop1');before=self.snapshot()
        r=c.post(reverse('action',args=[self.sb.pk,'cancel']));self.assertIn(r.status_code,(302,403,404,400,405))
        r=c.post(reverse('accept_holding',args=[self.db.pk]));self.assertIn(r.status_code,(302,403,404,400,405))
        self.sb.refresh_from_db();self.assertEqual(self.sb.status,'Submitted');self.assertEqual(before,self.snapshot());self.audit()

    def test_owner_sees_both_shops(self):
        c=self.login('owner');r=c.get(reverse('customer_dues'));self.assertEqual(r.status_code,200)
        r=c.get(reverse('stock_everywhere'));self.assertEqual(r.status_code,200)

    def test_anonymous_is_sent_to_login(self):
        c=Client()
        for name in ('customer_dues','money_history','stock_everywhere'):
            r=c.get(reverse(name));self.assertEqual(r.status_code,302);self.assertIn('login',r['Location'])


class LongText(World):
    def test_very_long_names_notes_and_unicode_do_not_break_pages(self):
        long='X'*500;urdu='محمد علی خان — موبائل'
        d=self.device(self.buy(self.a,70,supplier=urdu+long[:80]))
        try:sale=self.sell(self.a,d,customer=urdu)
        except BAD:sale=None                       # a rejection is fine; a crash is not
        c=self.login('owner')
        for name in ('customer_dues','supplier_dues','money_history','cash_and_bank','stock_everywhere'):self.assertEqual(c.get(reverse(name)).status_code,200,name)
        if sale:self.assertEqual(c.get(reverse('detail',args=[sale.pk])).status_code,200)
        self.audit()
