"""QA audit, phase 5: every financial calculation, checked four ways.

For each case the expected figures come from tests_audit_oracle (an independent implementation). The sale is then posted through the real
POS endpoint and the figures are read back from (1) the API response, (2) the database records, (3) the ledger and (4) the receipt page a
person sees. All four must equal the oracle to the paisa. Results are also written to qa-audit/test-results/calculations.csv."""
import csv
import os
import re
import uuid
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from django.test import TestCase
from django.urls import reverse
from .models import *
from . import credit,easy_retail,shopdefaults,staff_views,services as s
from . import tests_audit_oracle as oracle
from .tests_audit_base import AuditMixin,P

RESULTS=Path(__file__).resolve().parents[2]/'qa-audit'/'test-results'/'calculations.csv'
_rows=[]


def record(calculation,inputs,expected,actual):
    """One line of the calculation verification table (paisa in, rupees out)."""
    _rows.append([calculation,inputs,oracle.rupees(expected),oracle.rupees(actual),oracle.rupees(actual-expected),'PASS' if actual==expected else 'FAIL'])


def tearDownModule():
    if os.environ.get('AUDIT_WRITE_RESULTS')!='1' or not _rows:return
    RESULTS.parent.mkdir(parents=True,exist_ok=True)
    with open(RESULTS,'w',newline='',encoding='utf-8') as handle:
        writer=csv.writer(handle);writer.writerow(['Calculation','Input','Independently expected','Actual result','Difference','Status']);writer.writerows(_rows)


def shown(html):
    """Every amount printed on a page, as paisa (so '1,234.50' and '1234.50' are the same number)."""
    return {P(m.replace(',','')) for m in re.findall(r'(?<![\d.])-?\d[\d,]*\.\d{2}(?!\d)',html)}


class InvoiceBase(AuditMixin,TestCase):
    def setUp(self):
        self.build();self.capital(self.a,'5000000')
        self.cover,_=self.buy_accessory(self.a,'Audit cover',500,'100','250',pay='full')
        self.cable,_=self.buy_accessory(self.a,'Audit cable',500,'33.33','99.99',pay='full')
    def check(self,name,items,payments=(),discount_type='amount',discount_value='0',rate=None,expect_status=200):
        """Post one sale and compare the oracle with the response, the documents, the ledger and the receipt."""
        rule=self.tax_rule(self.a,rate) if rate is not None else None
        want=oracle.invoice([(q,r) for _,q,r in items],discount_type,discount_value,rate or '0')
        response=self.pos(self.login_a,self.a,items,payments,discount_type=discount_type,discount_value=discount_value,rule=rule)
        self.assertEqual(response.status_code,expect_status,response.content)
        if expect_status!=200:return None,want
        body=response.json();doc=Document.objects.get(pk=body['id']);inputs=f'{len(items)} line(s); discount {discount_value} {discount_type}; tax {rate or 0}%'
        received,remaining=oracle.settle(want['total'],[p['amount'] for p in payments])
        # 1. API response
        self.assertEqual(P(body['total']),want['total']);self.assertEqual(P(body['outstanding']),remaining)
        # 2. database records
        pricing=doc.data['pricing']
        for key,figure in (('subtotal','subtotal'),('discount','discount'),('net_total','net'),('tax_total','tax'),('grand_total','total')):
            self.assertEqual(P(pricing[key]),want[figure],key);record(f'{name}: {figure}',inputs,want[figure],P(pricing[key]))
        self.assertEqual(P(doc.total),want['total']);self.assertEqual(P(doc.outstanding),remaining)
        lines=list(doc.lines.order_by('pk'))
        for line,expected in zip(lines,want['lines']):
            snapshot=line.snapshot['pricing']
            for key in ('base','discount','net','tax','total'):self.assertEqual(P(snapshot[key]),expected[key],f'line {key}')
        self.assertEqual(sum(P(l.snapshot['pricing']['discount']) for l in lines),want['discount'])           # the whole discount is on the lines, not a paisa more or less
        self.assertEqual(sum(P(l.snapshot['pricing']['tax']) for l in lines),want['tax'])
        record(f'{name}: discount shared over lines',inputs,want['discount'],sum(P(l.snapshot['pricing']['discount']) for l in lines))
        # 3. ledger
        self.assertEqual(self.balance(self.a,'receivable',doc),want['total'])
        self.assertEqual(-self.balance(self.a,'sales',doc),want['net'])
        tax_posted=sum(P(e.credit)-P(e.debit) for e in doc.ledger.filter(account__code='COA-2400'));self.assertEqual(tax_posted,want['tax'])
        record(f'{name}: tax posted to the tax account',inputs,want['tax'],tax_posted)
        paid=sum(P(p.total) for p in doc.linked.filter(kind='Payment Entry',status='Submitted'));self.assertEqual(paid,received)
        self.assertEqual(want['total'],paid+P(doc.outstanding))                                              # invoice total = payments received + remaining receivable
        record(f'{name}: customer due after payments',inputs,remaining,P(doc.outstanding))
        debit=sum(P(e.debit) for e in LedgerEntry.objects.filter(document__in=[doc.pk,*doc.linked.values_list('pk',flat=True)]))
        credit_total=sum(P(e.credit) for e in LedgerEntry.objects.filter(document__in=[doc.pk,*doc.linked.values_list('pk',flat=True)]));self.assertEqual(debit,credit_total)
        # 4. what the person sees
        page=self.as_user(self.login_a).get(reverse('receipt',args=[doc.pk]));self.assertEqual(page.status_code,200)
        amounts=shown(page.content.decode());self.assertIn(want['total'],amounts)
        if remaining:self.assertIn(remaining,amounts)
        return doc,want


class Invoices(InvoiceBase):
    def test_one_phone_paid_in_full(self):
        phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
        doc,want=self.check('One phone',[(phone,1,'100000')],[dict(mode='Cash',amount='100000.00')])
        phone.refresh_from_db();self.assertEqual((phone.status,phone.sale_id,P(phone.sold_price)),('Sold',doc.pk,want['total']))
        self.assertEqual(P(doc.lines.get().cost),P('80000'));record('Phone profit','sold 100000, cost 80000',P('20000'),P(phone.sold_price)-P(phone.purchase_cost))
        self.assert_books_clean()
    def test_two_phones_and_accessories_with_an_amount_discount(self):
        one,_=self.buy_phone(self.a,'80000','100000',pay='full');two,_=self.buy_phone(self.a,'41500.50','55999.99',pay='full')
        doc,want=self.check('Phones and accessories, amount discount',[(one,1,'100000'),(two,1,'55999.99'),(self.cover,3,'250'),(self.cable,2,'99.99')],[dict(mode='Cash',amount='50000.00'),dict(mode='Bank Transfer',amount='60000.00',reference='TRX-1')],discount_value='1000.01')
        self.assertEqual(doc.linked.filter(kind='Payment Entry').count(),2);self.assert_books_clean()
    def test_percentage_discount_and_tax_on_paisa_prices(self):
        self.check('Percent discount with tax',[(self.cover,1,'333.33'),(self.cable,1,'0.01'),(self.cover,2,'0.05'),(self.cable,3,'0.99')],[],'percent','7.5','17');self.assert_books_clean()
    def test_tax_only_and_zero_tax(self):
        self.check('Tax, no discount',[(self.cover,4,'250')],[dict(mode='Cash',amount='1170.00')],rate='17')
        self.check('Zero tax rate',[(self.cover,1,'250')],[],rate='0');self.assert_books_clean()
    def test_three_equal_lines_share_one_and_two_paisa_in_line_order(self):
        """The paisa that cannot be split go to the earliest lines, always the same way."""
        doc,_=self.check('Residual one paisa',[(self.cover,1,'100'),(self.cover,1,'100'),(self.cover,1,'100')],[],discount_value='0.01')
        self.assertEqual([P(l.snapshot['pricing']['discount']) for l in doc.lines.order_by('pk')],[1,0,0])
        doc,_=self.check('Residual two paisa',[(self.cable,1,'100'),(self.cable,1,'100'),(self.cable,1,'100')],[],discount_value='0.02')
        self.assertEqual([P(l.snapshot['pricing']['discount']) for l in doc.lines.order_by('pk')],[1,1,0])
    def test_boundary_amounts(self):
        for name,rate in (('One paisa','0.01'),('Five paisa','0.05'),('Ten paisa','0.10'),('Ninety-nine paisa','0.99')):
            self.check(name,[(self.cover,1,rate)],[dict(mode='Cash',amount=rate)],rate='17')
        self.check('Repeating decimal share',[(self.cover,1,'10'),(self.cover,1,'10'),(self.cover,1,'10')],[],discount_value='10')          # 3.33 + 3.33 + 3.34
        self.check('Full discount',[(self.cover,2,'250')],[],'percent','100');self.assert_books_clean()
    def test_fractional_quantity(self):
        self.check('Half a unit',[(self.cover,'0.5','333.33'),(self.cable,'1.125','99.99')],[],'percent','3.33','17');self.assert_books_clean()
    def test_exact_half_paisa_follows_the_documented_half_even_rule(self):
        """1000.50 at 5% is 50.025: the shop's rule (round half to even) gives 50.02. Recorded so a change of rule is noticed."""
        doc,want=self.check('Half paisa tie',[(self.cover,1,'1000.50')],[],'percent','5');self.assertEqual(want['discount'],5002)
    def test_a_large_invoice(self):
        items=[(self.cover,1,str(Decimal('17.37')+i)) for i in range(15)]+[(self.cable,2,str(Decimal('3.19')*(i+1))) for i in range(15)]
        doc,want=self.check('Thirty lines',items,[],'percent','12.5','17');self.assertEqual(doc.lines.count(),30);self.assert_books_clean()
    def test_discount_larger_than_the_items_is_refused_and_nothing_is_posted(self):
        before=(Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count())
        response=self.pos(self.login_a,self.a,[(self.cover,1,'250')],discount_value='250.01');self.assertEqual(response.status_code,400)
        response=self.pos(self.login_a,self.a,[(self.cover,1,'250')],discount_type='percent',discount_value='100.01');self.assertEqual(response.status_code,400)
        response=self.pos(self.login_a,self.a,[(self.cover,1,'250')],discount_value='-1');self.assertEqual(response.status_code,400)
        self.assertEqual((Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count()),before)


class SplitPayments(InvoiceBase):
    def sale(self,payments,expect=200,**extra):
        return self.pos(self.login_a,self.a,[(self.cover,4,'250')],payments,**extra)
    def test_every_combination_leaves_total_equal_to_paid_plus_due(self):
        for name,payments in (('all cash',[dict(mode='Cash',amount='1000.00')]),('all bank',[dict(mode='Bank Transfer',amount='1000.00',reference='B1')]),
                              ('cash and bank',[dict(mode='Cash',amount='400.00'),dict(mode='Bank Transfer',amount='600.00')]),('cash and credit',[dict(mode='Cash',amount='250.50')]),
                              ('bank and credit',[dict(mode='Bank Transfer',amount='999.99')]),('three methods',[dict(mode='Cash',amount='100.00'),dict(mode='Bank Transfer',amount='200.00'),dict(mode='Easypaisa',amount='300.00')]),('nothing paid',[])):
            body=self.sale(payments).json();doc=Document.objects.get(pk=body['id'])
            received,remaining=oracle.settle(P('1000'),[p['amount'] for p in payments])
            self.assertEqual(P(doc.outstanding),remaining,name);self.assertEqual(sum(P(p.total) for p in doc.linked.filter(kind='Payment Entry')),received,name)
            record(f'Split payment ({name}): remaining receivable','total 1000.00',remaining,P(doc.outstanding))
        self.assert_books_clean()
    def test_money_lands_in_the_right_account(self):
        cash=self.balance(self.a,'Cash');bank=self.balance(self.a,'Bank Transfer');wallet=self.balance(self.a,'Easypaisa')
        self.sale([dict(mode='Cash',amount='100.00'),dict(mode='Bank Transfer',amount='200.00'),dict(mode='Easypaisa',amount='300.00')])
        self.assertEqual((self.balance(self.a,'Cash')-cash,self.balance(self.a,'Bank Transfer')-bank,self.balance(self.a,'Easypaisa')-wallet),(10000,20000,30000))
    def test_overpayment_negative_and_malformed_payments_are_refused_whole(self):
        before=(Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count())
        for payments in ([dict(mode='Cash',amount='1000.01')],[dict(mode='Cash',amount='600.00'),dict(mode='Bank Transfer',amount='400.01')],[dict(mode='Cash',amount='-5.00')],[dict(mode='Cash',amount='10.005')],
                         [dict(mode='Bitcoin',amount='10.00')],[dict(mode='Cash',amount='abc')],'cash',[['Cash','10']],[dict(mode='Cash',amount='500.00'),dict(mode='Cash',amount='-100.00')]):
            self.assertEqual(self.sale(payments).status_code,400,payments)
        self.assertEqual((Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count()),before)       # the failed sale left nothing behind: no invoice, no stock out, no half payment
    def test_the_same_request_sent_twice_is_one_sale_and_one_payment(self):
        key=uuid.uuid4();first=self.sale([dict(mode='Cash',amount='1000.00')],key=key);again=self.sale([dict(mode='Cash',amount='1000.00')],key=key)
        self.assertEqual((first.status_code,again.status_code),(200,200));self.assertEqual(first.json()['id'],again.json()['id'])
        self.assertEqual(Document.objects.filter(kind__in=s.SELL).count(),1);self.assertEqual(Document.objects.filter(kind='Payment Entry',customer__isnull=False).count(),1)
    def test_a_reused_request_key_with_different_contents_is_refused(self):
        key=uuid.uuid4();self.sale([dict(mode='Cash',amount='1000.00')],key=key)
        self.assertEqual(self.sale([dict(mode='Cash',amount='999.00')],key=key).status_code,400);self.assertEqual(Document.objects.filter(kind__in=s.SELL).count(),1)
    def test_cash_handed_and_change(self):
        body=self.sale([dict(mode='Cash',amount='1000.00',cash_tendered='5000.00')]).json();pay=Document.objects.get(reference_id=body['id'],kind='Payment Entry')
        self.assertEqual((pay.data['cash_tendered'],pay.data['change_returned'],P(pay.total)),('5000.00','4000.00',100000))
        self.assertEqual(self.sale([dict(mode='Cash',amount='1000.00',cash_tendered='999.99')]).status_code,400)


class CustomerDues(InvoiceBase):
    def test_part_payments_then_full_settlement(self):
        body=self.pos(self.login_a,self.a,[(self.cover,4,'250')],[dict(mode='Cash',amount='300.00')],customer='Credit Customer').json()
        doc=Document.objects.get(pk=body['id']);customer=doc.customer
        from . import money
        money.record_party_payment(self.login_a,customer,'200.00','Cash',self.today);doc.refresh_from_db();self.assertEqual(P(doc.outstanding),50000)
        record('Customer due after a part payment','1000 - 300 - 200',50000,P(doc.outstanding))
        with self.assertRaises(Exception):money.record_party_payment(self.login_a,customer,'-1','Cash',self.today)
        money.record_party_payment(self.login_a,customer,'500.00','Bank Transfer',self.today);doc.refresh_from_db();self.assertEqual(P(doc.outstanding),0)
        ledger=LedgerEntry.objects.filter(branch=self.a,account=self.account_of(self.a,'receivable'),party=customer)
        self.assertEqual(sum(P(e.debit)-P(e.credit) for e in ledger),0);self.assert_books_clean()
    def test_money_paid_beyond_the_bills_stays_as_an_advance_and_is_not_income(self):
        body=self.pos(self.login_a,self.a,[(self.cover,4,'250')],customer='Advance Customer').json();customer=Document.objects.get(pk=body['id']).customer
        from . import money
        sales_before=self.balance(self.a,'sales');money.record_party_payment(self.login_a,customer,'1500.00','Cash',self.today)
        self.assertEqual(self.balance(self.a,'sales'),sales_before)                    # a customer advance is a liability to the customer, never revenue
        rows=money.customer_balances([self.a]);row=next(r for r in rows if r['party'].pk==customer.pk)
        self.assertEqual((P(row['you_owe']),P(row['owes_you'])),(50000,0));self.assert_books_clean()
    def test_instalments_add_up_and_overdue_follows_the_due_dates(self):
        body=self.pos(self.login_a,self.a,[(self.cover,4,'250')],customer='Instalment Customer').json();doc=Document.objects.get(pk=body['id'])
        credit.plan(doc,self.login_a,3,self.today);parts=[P(i.amount) for i in doc.installments.order_by('number')]
        self.assertEqual(parts,oracle.instalments('1000',3));self.assertEqual(sum(parts),100000)
        self.assertEqual(P(credit.overdue_amount(doc,self.today)),0);self.assertEqual(P(credit.overdue_amount(doc,self.today+timedelta(days=1))),parts[0])
        self.assertEqual(P(credit.overdue_amount(doc,self.today+timedelta(days=40))),parts[0]+parts[1])
    def test_credit_limit_is_enforced_by_the_server(self):
        body=self.pos(self.login_a,self.a,[(self.cover,1,'250')],customer='Limited Customer').json();customer=Document.objects.get(pk=body['id']).customer
        customer.credit_limit=Decimal('500');customer.save()
        self.assertEqual(self.pos(self.login_a,self.a,[(self.cover,1,'250')],customer='Limited Customer').status_code,200)       # owes exactly 500: at the limit
        refused=self.pos(self.login_a,self.a,[(self.cover,1,'0.01')],customer='Limited Customer');self.assertEqual(refused.status_code,400);self.assertIn('credit limit',refused.json()['error'])
        self.assertEqual(self.pos(self.login_a,self.a,[(self.cover,1,'250')],[dict(mode='Cash',amount='250.00')],customer='Limited Customer').status_code,200)       # paid in full: no new debt


class SupplierDues(InvoiceBase):
    def test_credit_purchase_part_payment_and_settlement(self):
        from . import money
        phone,bill=self.buy_phone(self.a,'60000.75','70000',pay='part',amount_paid='10000.25',supplier='Dues Supplier');self.assertEqual(P(bill.outstanding),P('50000.50'))
        record('Supplier due after a part payment','60000.75 - 10000.25',P('50000.50'),P(bill.outstanding))
        other,other_bill=self.buy_phone(self.a,'30000','40000',pay='unpaid',supplier='Another Supplier')
        money.record_party_payment(self.login_a,bill.supplier,'50000.50','Cash',self.today);bill.refresh_from_db();other_bill.refresh_from_db()
        self.assertEqual((P(bill.outstanding),P(other_bill.outstanding)),(0,3000000))             # paying one supplier never touches another
        self.assertEqual(-self.balance(self.a,'payable'),3000000);self.assertEqual(self.balance(self.a,'inventory')-P('100')*500-P('33.33')*500,P('90000.75'))
        self.assert_books_clean()
    def test_a_supplier_cannot_be_paid_from_an_account_that_does_not_hold_the_money(self):
        from . import money
        from django.core.exceptions import ValidationError
        phone,bill=self.buy_phone(self.a,'60000','70000',pay='unpaid')
        with self.assertRaisesMessage(ValidationError,'has only'):money.record_party_payment(self.login_a,bill.supplier,'60000.00','Easypaisa',self.today)


class Commission(AuditMixin,TestCase):
    def setUp(self):
        self.build();self.capital(self.a,'5000000');owner=self.as_user(self.owner)
        owner.post(reverse('staff_list'),dict(action='add',name='Bilal Seller',branch=str(self.a.pk),commission='2',designation='Sales'))
        self.person=Staff.objects.get(name='Bilal Seller');self.owner_client=owner
    def sell(self,price,when,cost='50000'):
        phone,_=self.buy_phone(self.a,cost,price,pay='full',when=when);me=shopdefaults.shop_person(self.a)
        return easy_retail.sale(dict(branch=self.a,party_name='Commission Customer',phone=phone,pta_status='PTA',price=str(price),staff=self.person,handler=me,posting_date=when,payment_status='full',amount_paid=None,method='Cash',notes=''),self.owner),phone
    def report(self):
        page=self.owner_client.get(reverse('staff_detail',args=[self.person.pk]),{'from':str(self.today-timedelta(days=60)),'to':str(self.today)})
        return page
    def test_each_sale_earns_the_rate_of_its_own_day(self):
        d=self.today;rates=[(staff_views.BEGINNING,'2')]
        early,_=self.sell('60000',d-timedelta(days=20))
        staff_views.set_rate(self.person,Decimal('5'),d-timedelta(days=10));self.person.save();rates.append((d-timedelta(days=10),'5'))
        on_the_day,_=self.sell('70000.50',d-timedelta(days=10));later,_=self.sell('80000',d-timedelta(days=3))
        staff_views.set_rate(self.person,Decimal('1.5'),d);self.person.save();rates.append((d,'1.5'))
        today_sale,_=self.sell('90000',d)
        want=oracle.commission([(d-timedelta(days=20),'60000',None),(d-timedelta(days=10),'70000.50',None),(d-timedelta(days=3),'80000',None),(d,'90000',None)],rates)
        page=self.report();self.assertEqual(P(page.context['commission']),want)
        record('Staff commission across three rates','2% then 5% then 1.5%',want,P(page.context['commission']))
        self.assertEqual({str(r['d'].pk):str(r['rate']) for r in page.context['sales']},{str(early.pk):'2.00',str(on_the_day.pk):'5.00',str(later.pk):'5.00',str(today_sale.pk):'1.50'})
        self.assertIn(want,shown(page.content.decode()))
    def test_commission_is_on_the_amount_after_discount_and_without_tax(self):
        accessory,_=self.buy_accessory(self.a,'Commission cover',10,'100','1000',pay='full');rule=self.tax_rule(self.a,'17')
        self.pos(self.login_a,self.a,[(accessory,2,'1000')],[dict(mode='Cash',amount='2106.00')],discount_value='200',rule=rule,seller=str(self.person.pk))
        want=oracle.commission([(self.today,'1800',None)],[(staff_views.BEGINNING,'2')])              # 2000 less 200 discount; the 306 tax is not the seller's
        page=self.report();self.assertEqual(P(page.context['net']),180000);self.assertEqual(P(page.context['commission']),want)
        record('Commission base excludes tax and discount','2000 - 200 discount + 17% tax, rate 2%',want,P(page.context['commission']))
    def test_a_return_takes_back_exactly_what_the_sale_earned_even_after_a_rate_change(self):
        from . import returns_flow
        d=self.today;sale,phone=self.sell('60000',d-timedelta(days=20))
        staff_views.set_rate(self.person,Decimal('5'),d-timedelta(days=10));self.person.save()
        returns_flow.settle(self.owner,sale,[str(phone.pk)],None,returns_flow.CONDITIONS[0],returns_flow.REASONS[1],'refund','Cash','')
        want=oracle.commission([(d-timedelta(days=20),'60000',None),(d,'-60000',d-timedelta(days=20))],[(staff_views.BEGINNING,'2'),(d-timedelta(days=10),'5')])
        self.assertEqual(want,0)                                                           # the sale is fully undone: the seller ends with nothing for it, and owes nothing
        page=self.report();self.assertEqual(P(page.context['commission']),want)
        record('Commission after a full return across a rate change','sale at 2%, return after the rate became 5%',want,P(page.context['commission']))
    def test_an_unpaid_credit_sale_still_earns_commission_as_documented(self):
        phone,_=self.buy_phone(self.a,'50000','60000',pay='full');me=shopdefaults.shop_person(self.a)
        easy_retail.sale(dict(branch=self.a,party_name='Pay Later',phone=phone,pta_status='PTA',price='60000',staff=self.person,handler=me,posting_date=self.today,payment_status='unpaid',amount_paid=None,method='Cash',notes=''),self.owner)
        self.assertEqual(P(self.report().context['commission']),oracle.commission([(self.today,'60000',None)],[(staff_views.BEGINNING,'2')]))
