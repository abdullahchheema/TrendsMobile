"""QA audit, phase 4 and scenario 7: a full financial period, reconciled independently.

One shop trades for three months: capital, purchases (cash and credit), sales (cash, credit, discount, tax), a return, expenses, drawings,
a loan, a fixed asset, a prepaid bill, an accrual, a stock loss. Every expected balance is then worked out here from the inputs with the
oracle and compared with the raw ledger, with the application's reports and with the pages an owner sees. Followed by the period controls:
year-end close, period lock, and a posting that fails half way."""
from datetime import date,timedelta
from decimal import Decimal
from unittest.mock import patch
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from .models import *
from . import advanced,easy_retail,financial_reports,money,returns_flow,shopdefaults,stock_adjust,yearend,services as s
from . import tests_audit_oracle as oracle
from .tests_audit_base import AuditMixin,P
from .tests_audit_calculations import record


def acct(code):return Account.objects.get(code='COA-'+code)


class PeriodBase(AuditMixin,TestCase):
    def setUp(self):
        self.build();self.d=lambda n:self.today-timedelta(days=n)
    def trade(self):
        """The controlled dataset. Returns the figures the books must show, each worked out here from the inputs (paisa)."""
        a=self.a;o=self.owner;cash=bank=0;e={}
        self.capital(a,'2000000',when=self.d(90));cash+=P('2000000')
        money.move_money(o,a,'Cash','Bank Transfer','800000',self.d(89));cash-=P('800000');bank+=P('800000')
        # purchases: one paid in cash, one on credit and part paid, one unpaid
        p1,_=self.buy_phone(a,'80000','100000',pay='full',when=self.d(85));cash-=P('80000')
        p2,bill2=self.buy_phone(a,'60000.75','75000',pay='part',amount_paid='10000.25',when=self.d(84),supplier='Credit Supplier');cash-=P('10000.25')
        p3,bill3=self.buy_phone(a,'45000','52000',pay='unpaid',when=self.d(83),supplier='Unpaid Supplier')
        cover,_=self.buy_accessory(a,'Period cover',100,'100','250',pay='full',when=self.d(60));cash-=P('10000')
        self.buy_accessory(a,'Period cover',50,'133.33','250',pay='full',when=self.d(40));cash-=P('6666.50')
        e['payable']=P('60000.75')-P('10000.25')+P('45000')
        # sales
        me=shopdefaults.shop_person(a)
        sale1=easy_retail.sale(dict(branch=a,party_name='Cash Buyer',phone=p1,pta_status='PTA',price='100000',staff=me,handler=me,posting_date=self.d(30),payment_status='full',amount_paid=None,method='Cash',notes=''),o);cash+=P('100000')
        sale2=easy_retail.sale(dict(branch=a,party_name='Credit Buyer',phone=p2,pta_status='PTA',price='75000',staff=me,handler=me,posting_date=self.d(29),payment_status='part',amount_paid='25000',method='Bank Transfer',notes=''),o);bank+=P('25000')
        rule=self.tax_rule(a,'17')
        body=self.pos(self.login_a,a,[(cover,30,'250')],[dict(mode='Cash',amount='7897.50')],customer='Accessory Buyer',discount_type='percent',discount_value='10',rule=rule).json()
        sale3=Document.objects.get(pk=body['id']);inv=oracle.invoice([(30,'250')],'percent','10','17');cash+=inv['total']
        _,_,outs=oracle.weighted_average([('in',100,'100'),('in',50,'133.33'),('out',30)]);accessory_cost=outs[0]
        # a return of 5 covers, refunded in cash
        line=sale3.lines.get()
        credit=returns_flow.settle(o,sale3,[],{str(line.pk):'5'},returns_flow.CONDITIONS[0],returns_flow.REASONS[1],'refund','Cash','')
        back_net=oracle.to_paisa(oracle.Fraction(inv['net'],100)*5/30);back_tax=oracle.to_paisa(oracle.Fraction(inv['tax'],100)*5/30);cash-=back_net+back_tax
        back_cost=oracle.to_paisa(oracle.Fraction(accessory_cost,100)/30*5)
        # expenses, drawings, other income
        money.record_voucher(o,a,'expense',acct('6100'),'50000','Cash',self.d(28),'Shop rent');cash-=P('50000')
        money.record_voucher(o,a,'expense',acct('6200'),'30000','Bank Transfer',self.d(27),'Salaries');bank-=P('30000')
        money.record_voucher(o,a,'drawings',acct('3300'),'20000','Cash',self.d(26),'Owner drawings');cash-=P('20000')
        money.record_voucher(o,a,'income',acct('4900'),'5000','Cash',self.d(25),'Scrap sold');cash+=P('5000')
        # loan: 300,000 at 12%, then one repayment of principal and interest
        advanced.add_loan(o,a,dict(lender='Audit Bank',principal='300000',annual_rate='12',start_date=self.d(70),term_months=12,liability_account=str(acct('2500').pk),interest_account=str(acct('6850').pk),mode='Bank Transfer'));bank+=P('300000')
        loan=Loan.objects.get();interest=oracle.loan_interest('300000','12');self.assertEqual(P(advanced.suggested_interest(loan)),interest)
        advanced.repay_loan(o,loan,self.d(20),'50000',oracle.rupees(interest),'Bank Transfer');bank-=P('50000')+interest
        # fixed asset, straight line over 24 months with a scrap value
        bought=self.d(75)
        advanced.add_asset(o,a,dict(name='Display counter',purchase_date=bought,cost='120000',salvage='12000',life_months=24,method='Straight line',rate=None,asset_account=str(acct('1510').pk),accumulated_account=str(acct('1590').pk),expense_account=str(acct('6800').pk),funding='Bank Transfer'));bank-=P('120000')
        advanced.depreciate_all(o,a,self.today);depreciation=oracle.straight_line('120000','12000',24,bought,self.today)
        # prepaid: 60,000 for 6 months; accrual: 8,000 reversed on its date
        started=self.d(80)
        advanced.add_deferral(o,a,dict(kind='Prepaid',description='Six months of insurance',amount='60000',months=6,start_date=started,expense_account=str(acct('6900').pk),balance_account=str(acct('1320').pk),mode='Bank Transfer'));bank-=P('60000')
        advanced.add_deferral(o,a,dict(kind='Accrual',description='Electricity used, bill not in yet',amount='8000',start_date=self.d(15),reverse_on=self.d(5),expense_account=str(acct('6300').pk),balance_account=str(acct('2300').pk)))
        advanced.process_deferrals(o,a,self.today);released=oracle.prepaid_released('60000',6,started,self.today)
        # a stock loss: two covers damaged
        stock_adjust.post(dict(branch=a,warehouse=shopdefaults.warehouse(a),posting_date=self.d(10),notes='Crushed in storage',reason='Damaged',phone=None,product=cover,change=Decimal('-2'),unit_cost=None),o)
        qty,value,_=oracle.weighted_average([('in',100,'100'),('in',50,'133.33'),('out',30)]);value+=back_cost;qty+=5
        loss=oracle.to_paisa(oracle.Fraction(value,100)/qty*2);value-=loss
        e.update(cash=cash,bank=bank,receivable=P('75000')-P('25000'),inventory=P('45000')+value,tax=inv['tax']-back_tax,loan=P('250000'),
                 income=P('100000')+P('75000')+inv['net']-back_net+P('5000'),
                 cogs=P('80000')+P('60000.75')+accessory_cost-back_cost,
                 expenses=P('50000')+P('30000')+interest+depreciation+released+loss,          # the accrual and its reversal cancel out
                 depreciation=depreciation,released=released,loss=loss,capital=P('2000000'),drawings=P('20000'),asset=P('120000'),prepaid=P('60000')-released,accrued=0)
        e['profit']=e['income']-e['cogs']-e['expenses']
        self.objects=dict(p3=p3,sale1=sale1,sale2=sale2,sale3=sale3,credit=credit,cover=cover,loan=loan)
        return e


class FullPeriod(PeriodBase):
    def test_the_books_of_a_whole_period_reconcile_to_independent_figures(self):
        e=self.trade();a=self.a;branches=Branch.objects.filter(pk=a.pk);start=self.d(120);end=self.today
        sections={x.code:x.root_type for x in Account.objects.all()}
        # ---- the raw ledger, aggregated by the oracle
        nets,debits,credits=oracle.trial_balance(self.ledger_rows([a]))
        self.assertEqual(debits,credits);record('Trial balance: total debits = total credits','full period',debits,credits)
        code=lambda key:self.account_of(a,key).code
        figures=[('Cash',nets[code('Cash')],e['cash']),('Bank',nets[code('Bank Transfer')],e['bank']),('Customer receivables',nets[code('receivable')],e['receivable']),
                 ('Supplier payables',-nets[code('payable')],e['payable']),('Inventory value',nets[code('inventory')],e['inventory']),('Sales tax payable',-nets['COA-2400'],e['tax']),
                 ('Loan balance',-nets['COA-2500'],e['loan']),('Owner capital',-nets['COA-3100'],e['capital']),('Owner drawings',nets['COA-3300'],e['drawings']),
                 ('Fixed asset at cost',nets['COA-1510'],e['asset']),('Accumulated depreciation',-nets['COA-1590'],e['depreciation']),('Prepaid balance',nets['COA-1320'],e['prepaid']),
                 ('Accrued expenses',-nets['COA-2300'],e['accrued']),('Stock loss',nets[code('expense')] if code('expense') in nets else 0,e['loss'])]
        for name,actual,expected in figures:
            record(name,'full period dataset',expected,actual);self.assertEqual(actual,expected,name)
        income,expenses,profit=oracle.profit_and_loss(nets,sections)
        self.assertEqual((income,expenses,profit),(e['income'],e['cogs']+e['expenses'],e['profit']))
        record('Net profit','income - cost of goods - expenses',e['profit'],profit)
        # ---- capital, drawings and the loan are not income or expense
        self.assertNotEqual(sections['COA-3100'],'Income');self.assertNotEqual(sections['COA-3300'],'Expense');self.assertNotEqual(sections['COA-2500'],'Income')
        # ---- the application's reports say the same as the ledger
        _,tb=financial_reports.run('Trial Balance',branches,start,end)
        self.assertEqual({r['Account code']:P(r['Closing debit'])-P(r['Closing credit']) for r in tb if r['Closing debit'] or r['Closing credit']},{k:v for k,v in nets.items() if v})
        self.assertEqual(sum(P(r['Closing debit']) for r in tb),sum(P(r['Closing credit']) for r in tb))
        _,pl=financial_reports.run('Profit and Loss Statement',branches,start,end);summary=dict(financial_reports.summary('Profit and Loss Statement',pl))
        self.assertEqual((P(summary['Income']),P(summary['Expenses']),P(summary['Net profit / loss'])),(e['income'],e['cogs']+e['expenses'],e['profit']))
        record('Profit and loss report: net profit','application report',e['profit'],P(summary['Net profit / loss']))
        _,bs=financial_reports.run('Balance Sheet',branches,start,end);sheet=dict(financial_reports.summary('Balance Sheet',bs))
        self.assertEqual(P(sheet['Assets less liabilities and equity']),0)
        self.assertEqual(P(sheet['Assets']),e['cash']+e['bank']+e['receivable']+e['inventory']+e['asset']-e['depreciation']+e['prepaid'])
        _,cf=financial_reports.run('Cash Flow Statement',branches,start,end);flow={r['Line']:P(r['Amount']) for r in cf}
        self.assertEqual(flow['Unexplained difference (should be zero)'],0);self.assertEqual(flow['Cash and bank at end (ledger)'],e['cash']+e['bank'])
        # ---- cash and bank: ledger, balances screen and history agree
        held={r['method']:P(r['amount']) for r in money.balances([a])};self.assertEqual((held['Cash'],held['Bank Transfer']),(e['cash'],e['bank']))
        _,totals=money.history([a]);self.assertEqual(P(totals['received'])-P(totals['paid']),e['cash']+e['bank'])
        # ---- customer and supplier dues: screen = subsidiary ledger = control account
        self.assertEqual(sum(P(r['remaining']) for r in money.customer_dues([a])),e['receivable']);self.assertEqual(sum(P(r['remaining']) for r in money.supplier_dues([a])),e['payable'])
        # ---- the owner's pages show the same numbers
        owner=self.as_user(self.owner);query={'branch':str(a.pk),'from_date':str(start),'to_date':str(end)}
        for name in ('Trial Balance','Profit and Loss Statement','Balance Sheet','Cash Flow Statement','General Ledger','Accounts Receivable','Accounts Payable','Stock Balance','Expense Report'):
            self.assertEqual(owner.get(reverse('report',args=[name]),query).status_code,200,name)
        page=owner.get(reverse('report',args=['Profit and Loss Statement']),query)
        self.assertIn(oracle.rupees(e['profit']),page.content.decode().replace(',',''))
        self.assert_books_clean()
    def test_depreciation_never_passes_the_depreciable_amount_and_is_posted_once(self):
        o=self.owner;a=self.a;self.capital(a,'500000',when=self.d(400))
        bought=self.d(380)
        advanced.add_asset(o,a,dict(name='Old shelf',purchase_date=bought,cost='10000',salvage='1000',life_months=6,method='Straight line',rate=None,asset_account=str(acct('1510').pk),accumulated_account=str(acct('1590').pk),expense_account=str(acct('6800').pk),funding='Cash'))
        advanced.add_asset(o,a,dict(name='Laptop',purchase_date=bought,cost='90000',salvage='5000',life_months=36,method='Reducing balance',rate='30',asset_account=str(acct('1510').pk),accumulated_account=str(acct('1590').pk),expense_account=str(acct('6800').pk),funding='Cash'))
        advanced.depreciate_all(o,a,self.today);self.assertEqual(advanced.depreciate_all(o,a,self.today),[])            # a second run posts nothing
        shelf=FixedAsset.objects.get(name='Old shelf');laptop=FixedAsset.objects.get(name='Laptop')
        self.assertEqual(P(shelf.accumulated),oracle.straight_line('10000','1000',6,bought,self.today));self.assertEqual(P(shelf.accumulated),P('9000'))
        self.assertEqual(P(laptop.accumulated),oracle.reducing_balance('90000','5000','30',bought,self.today))
        record('Straight-line depreciation past the end of life','10000 less 1000 over 6 months, 12+ months later',P('9000'),P(shelf.accumulated))
        record('Reducing-balance depreciation','90000 at 30% a year',oracle.reducing_balance('90000','5000','30',bought,self.today),P(laptop.accumulated))
        total=LedgerEntry.objects.filter(account=acct('1590')).values_list('credit',flat=True);self.assertEqual(sum(P(c) for c in total),P(shelf.accumulated)+P(laptop.accumulated))
    def test_an_asset_can_be_sold_for_more_than_it_cost(self):
        """Proceeds above cost: the whole book value comes off, and everything above it is a gain."""
        o=self.owner;a=self.a;self.capital(a,'500000',when=self.d(100))
        bought=self.d(95)
        advanced.add_asset(o,a,dict(name='Signboard',purchase_date=bought,cost='100000',salvage='0',life_months=20,method='Straight line',rate=None,asset_account=str(acct('1510').pk),accumulated_account=str(acct('1590').pk),expense_account=str(acct('6800').pk),funding='Cash'))
        advanced.depreciate_all(o,a,self.today);asset=FixedAsset.objects.get();done=P(asset.accumulated);cash=self.balance(a,'Cash')
        advanced.dispose_asset(o,asset,self.today,'150000','Cash')
        nets,debits,credits=oracle.trial_balance(self.ledger_rows([a]));self.assertEqual(debits,credits)
        self.assertEqual(nets.get('COA-1510',0),0);self.assertEqual(nets.get('COA-1590',0),0)                          # nothing of the asset is left on the books
        self.assertEqual(self.balance(a,'Cash')-cash,P('150000'));self.assertEqual(-nets['COA-4900'],P('150000')-(P('100000')-done))
        record('Gain on selling an asset above cost','cost 100000, sold 150000',P('150000')-(P('100000')-done),-nets['COA-4900'])
        self.assert_books_clean()
    def test_an_asset_sold_below_book_value_is_a_loss(self):
        o=self.owner;a=self.a;self.capital(a,'500000',when=self.d(100))
        advanced.add_asset(o,a,dict(name='Counter',purchase_date=self.d(95),cost='100000',salvage='0',life_months=20,method='Straight line',rate=None,asset_account=str(acct('1510').pk),accumulated_account=str(acct('1590').pk),expense_account=str(acct('6800').pk),funding='Cash'))
        advanced.depreciate_all(o,a,self.today);asset=FixedAsset.objects.get();done=P(asset.accumulated)
        advanced.dispose_asset(o,asset,self.today,'20000','Cash');nets,debits,credits=oracle.trial_balance(self.ledger_rows([a]))
        self.assertEqual((debits-credits,nets.get('COA-1510',0),nets.get('COA-1590',0)),(0,0,0));self.assertEqual(nets['COA-6900'],P('100000')-done-P('20000'))


class PeriodControls(PeriodBase):
    def test_year_end_close_moves_the_profit_once_and_locks_the_period(self):
        e=self.trade();a=self.a;retained=acct('3900');start=self.d(120);end=self.d(1)
        before=oracle.profit_and_loss(oracle.trial_balance([r for r in self.ledger_rows([a])])[0],{x.code:x.root_type for x in Account.objects.all()})[2]
        period_profit=-sum(P(x.debit)-P(x.credit) for x in LedgerEntry.objects.filter(branch=a,posting_date__lte=end,account__root_type__in=('Income','Expense')))
        doc,profit=yearend.close_year(self.owner,a,start,end,retained);self.assertEqual(P(profit),period_profit)
        self.assertEqual(-sum(P(x.debit)-P(x.credit) for x in LedgerEntry.objects.filter(branch=a,account=retained)),period_profit)
        record('Year-end close: profit moved to retained earnings','period through yesterday',period_profit,P(profit))
        a.refresh_from_db();self.assertEqual(a.lock_through,end)
        with self.assertRaisesMessage(ValidationError,'already been closed'):yearend.close_year(self.owner,a,start,end,retained)
        # a second close that overlaps the first must not move the same profit again
        with self.assertRaises(ValidationError):yearend.close_year(self.owner,a,start,self.today,retained)
        self.assertEqual(-sum(P(x.debit)-P(x.credit) for x in LedgerEntry.objects.filter(branch=a,account=retained)),period_profit)
        # the following period closes on its own and only moves what happened in it
        Branch.objects.filter(pk=a.pk).update(lock_through=end)
        today_profit=-sum(P(x.debit)-P(x.credit) for x in LedgerEntry.objects.filter(branch=a,posting_date=self.today,account__root_type__in=('Income','Expense')))
        if today_profit:
            yearend.close_year(self.owner,a,self.today,self.today,retained)
            self.assertEqual(-sum(P(x.debit)-P(x.credit) for x in LedgerEntry.objects.filter(branch=a,account=retained)),before)
            income_left=sum(P(x.debit)-P(x.credit) for x in LedgerEntry.objects.filter(branch=a,account__root_type__in=('Income','Expense')));self.assertEqual(income_left,0)
        nets,debits,credits=oracle.trial_balance(self.ledger_rows([a]));self.assertEqual(debits,credits)
    def test_a_locked_period_refuses_new_postings_changes_and_cancellations(self):
        self.trade();a=self.a;o=self.owner;Branch.objects.filter(pk=a.pk).update(lock_through=self.d(5));me=shopdefaults.shop_person(a)
        before=(Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count())
        with self.assertRaisesMessage(ValidationError,'locked'):
            easy_retail.sale(dict(branch=a,party_name='Late Buyer',phone=self.objects['p3'],pta_status='PTA',price='52000',staff=me,handler=me,posting_date=self.d(6),payment_status='full',amount_paid=None,method='Cash',notes=''),o)
        with self.assertRaisesMessage(ValidationError,'locked'):money.record_voucher(o,a,'expense',acct('6100'),'100','Cash',self.d(5),'Back-dated rent')
        with self.assertRaisesMessage(ValidationError,'locked'):s.cancel(self.objects['sale1'],o)
        with self.assertRaisesMessage(ValidationError,'locked'):money.record_party_payment(o,self.objects['sale2'].customer,'1000','Cash',self.d(10))
        with self.assertRaisesMessage(ValidationError,'locked'):advanced.repay_loan(o,self.objects['loan'],self.d(7),'1000','0','Bank Transfer')
        self.assertEqual((Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count()),before)
        self.objects['p3'].refresh_from_db();self.assertEqual(self.objects['p3'].status,'Available')
        money.record_voucher(o,a,'expense',acct('6100'),'100','Cash',self.d(4),'Rent the day after the lock')                 # the first open day still works
    def test_only_the_owner_can_lock_or_reopen_a_period(self):
        a=self.a;shop=self.as_user(self.login_a);owner=self.as_user(self.owner)
        self.assertEqual(shop.post(reverse('periods'),dict(branch=str(a.pk),lock_through=str(self.d(3)))).status_code,403)
        self.assertEqual(self.as_user(self.online_login).post(reverse('periods'),dict(branch=str(a.pk),lock_through=str(self.d(3)))).status_code,403)
        owner.post(reverse('periods'),dict(branch=str(a.pk),lock_through=str(self.d(3))));a.refresh_from_db();self.assertEqual(a.lock_through,self.d(3))
        owner.post(reverse('periods'),dict(branch=str(a.pk),lock_through=str(self.d(9)),reason=''));a.refresh_from_db();self.assertEqual(a.lock_through,self.d(3))       # reopening needs a reason
        owner.post(reverse('periods'),dict(branch=str(a.pk),lock_through=str(self.d(9)),reason='Correcting a supplier bill'));a.refresh_from_db();self.assertEqual(a.lock_through,self.d(9))
        self.assertTrue(Audit.objects.filter(action='Reopen period',branch=a).exists())
        owner.post(reverse('periods'),dict(branch=str(a.pk),lock_through=str(self.today+timedelta(days=1))));a.refresh_from_db();self.assertEqual(a.lock_through,self.d(9))    # never into the future


class FailureHalfWay(AuditMixin,TestCase):
    """A posting that breaks after some of its rows are written must leave nothing at all."""
    def setUp(self):
        self.build();self.capital(self.a,'500000');self.phone,_=self.buy_phone(self.a,'80000','100000',pay='full')
        self.before=(Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count(),Audit.objects.count())
    def nothing_changed(self):
        self.assertEqual((Document.objects.count(),LedgerEntry.objects.count(),StockMovement.objects.count(),Audit.objects.count()),self.before)
        self.phone.refresh_from_db();self.assertEqual((self.phone.status,self.phone.sale_id),('Available',None))
    def test_a_sale_that_fails_after_its_first_ledger_row_posts_nothing(self):
        real=LedgerEntry.objects.create;calls=[]
        def breaks_on_the_second(*args,**kwargs):
            calls.append(1)
            if len(calls)==2:raise RuntimeError('database went away')
            return real(*args,**kwargs)
        me=shopdefaults.shop_person(self.a)
        with patch.object(LedgerEntry.objects,'create',side_effect=breaks_on_the_second),self.assertRaises(RuntimeError):
            easy_retail.sale(dict(branch=self.a,party_name='Unlucky Buyer',phone=self.phone,pta_status='PTA',price='100000',staff=me,handler=me,posting_date=self.today,payment_status='full',amount_paid=None,method='Cash',notes=''),self.owner)
        self.assertEqual(len(calls),2);self.nothing_changed();self.assertFalse(Party.objects.filter(name='Unlucky Buyer').exists())
    def test_a_sale_whose_payment_fails_takes_the_invoice_back_with_it(self):
        response=self.pos(self.login_a,self.a,[(self.phone,1,'100000')],[dict(mode='Cash',amount='60000.00'),dict(mode='Card',amount='40000.00')])     # this shop has no Card method
        self.assertEqual(response.status_code,400);self.nothing_changed()
    def test_an_unbalanced_posting_is_refused(self):
        real=s.entry
        def wrong_amount(doc,key,debit=0,credit=0,**kwargs):
            return real(doc,key,debit=debit,credit=(Decimal(str(credit))+Decimal('0.01')) if key=='sales' else credit,**kwargs)
        me=shopdefaults.shop_person(self.a)
        with patch.object(s,'entry',side_effect=wrong_amount),self.assertRaisesMessage(ValidationError,'does not balance'):
            easy_retail.sale(dict(branch=self.a,party_name='Odd Buyer',phone=self.phone,pta_status='PTA',price='100000',staff=me,handler=me,posting_date=self.today,payment_status='unpaid',amount_paid=None,method='Cash',notes=''),self.owner)
        self.nothing_changed()
    def test_a_purchase_that_fails_at_the_payment_leaves_no_stock_and_no_bill(self):
        from django.core.exceptions import ValidationError as Invalid
        devices=Device.objects.count()
        with self.assertRaises(Invalid):self.buy_phone(self.a,'90000','110000',pay='full',method='JazzCash')            # not a method of this shop
        self.assertEqual(Device.objects.count(),devices);self.nothing_changed()
