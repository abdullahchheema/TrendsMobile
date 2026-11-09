"""Phase C: ledgers with running balances, cash/bank books and customer/supplier statements."""
from datetime import timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import coa,ledgers,vouchers,easy_retail,services as s


class LedgerBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());coa.install_standard(self.user);self.client.force_login(self.user);self.today=timezone.localdate()
        self.capital=Account.objects.get(code='COA-3100');self.rent=Account.objects.get(code='COA-6100')
    def voucher(self,kind,account,amount,on=None,mode='Cash'):
        return vouchers.post(dict(branch=self.branch,type=kind,posting_date=on or self.today,amount=Decimal(amount),account=str(account.pk),mode=mode,description='Ledger test',reference='TX-LEDGER' if mode!='Cash' else ''),self.user)
    def buy(self,number,price=50000,paid='unpaid',amount=None,on=None):
        data=dict(branch=self.branch,party_name='Ledger Supplier',product=self.product,quantity=1,price=price,asking_price=80000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,
                  posting_date=on or self.today,payment_status=paid,method='Cash',imeis=[[imei(number)]])
        if amount:data['amount_paid']=amount
        return easy_retail.purchase(data,self.user)
    def sell(self,device,price=70000,paid='unpaid',amount=None,on=None):
        data=dict(branch=self.branch,party_name='Ledger Customer',phone=device,price=price,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=on or self.today,payment_status=paid,method='Cash')
        if amount:data['amount_paid']=amount
        return easy_retail.sale(data,self.user)


class RunningBalance(LedgerBase):
    def test_cash_book_shows_opening_running_balance_and_closing(self):
        self.voucher('capital',self.capital,100000,on=self.today-timedelta(days=10))
        self.voucher('expense',self.rent,30000,on=self.today-timedelta(days=1));self.voucher('expense',self.rent,5000)
        response=self.client.get(reverse('ledger'),{'book':'cash','from_date':str(self.today-timedelta(days=2))})
        data=response.context['data'];self.assertEqual(data['opening'],Decimal('100000'))
        self.assertEqual([r['balance'] for r in data['rows']],[Decimal('70000'),Decimal('65000')]);self.assertEqual(data['closing'],Decimal('65000'))
        self.assertEqual((data['debit'],data['credit']),(Decimal('0'),Decimal('35000')))
    def test_account_ledger_uses_the_natural_side_for_equity_and_expense(self):
        self.voucher('capital',self.capital,80000);self.voucher('expense',self.rent,12000)
        cap=self.client.get(reverse('ledger'),{'account':str(self.capital.pk)}).context['data'];self.assertEqual(cap['closing'],Decimal('80000'))
        exp=self.client.get(reverse('ledger'),{'account':str(self.rent.pk)}).context['data'];self.assertEqual(exp['closing'],Decimal('12000'))
    def test_bank_book_only_lists_bank_and_wallet_accounts(self):
        self.voucher('capital',self.capital,9000);self.voucher('capital',self.capital,4000,mode='Bank Transfer')
        rows=self.client.get(reverse('ledger'),{'book':'bank'}).context['data']['rows'];self.assertEqual(len(rows),1);self.assertEqual(rows[0]['debit'],Decimal('4000'))


class Statements(LedgerBase):
    def test_customer_statement_balance_equals_what_they_owe_after_part_payment(self):
        bill=self.buy(81,paid='full');phone=bill.reference.lines.get().device
        sale=self.sell(phone,price=70000,paid='part',amount=20000);customer=sale.customer
        page=self.client.get(reverse('statement',args=[customer.pk]));data=page.context['data']
        self.assertEqual(data['closing'],Decimal('50000'));self.assertEqual(data['due'],Decimal('50000'))
        self.assertEqual([(r['debit'],r['credit']) for r in data['rows']],[(Decimal('70000'),Decimal('0')),(Decimal('0'),Decimal('20000'))])
        self.assertContains(page,'Amount this customer owes you')
    def test_supplier_statement_balance_is_what_we_owe(self):
        bill=self.buy(82,price=50000,paid='part',amount=15000)
        data=self.client.get(reverse('statement',args=[bill.supplier.pk])).context['data'];self.assertEqual(data['closing'],Decimal('35000'))
    def test_ageing_buckets_follow_invoice_age(self):
        bill=self.buy(83,paid='full');phone=bill.reference.lines.get().device
        old=self.sell(phone,price=60000,paid='unpaid',on=self.today-timedelta(days=45))
        bill2=self.buy(84,paid='full');fresh=self.sell(bill2.reference.lines.get().device,price=40000,paid='unpaid')
        ageing={a['label']:a['amount'] for a in self.client.get(reverse('statement',args=[old.customer.pk])).context['data']['ageing']}
        self.assertEqual(ageing['Current (0-30 days)'],Decimal('40000'));self.assertEqual(ageing['31-60 days'],Decimal('60000'))
    def test_statement_is_limited_to_the_users_shops(self):
        bill=self.buy(85,paid='full');sale=self.sell(bill.reference.lines.get().device,paid='unpaid')
        other=Branch.objects.create(code='OTH2',name='Other shop',company='X')
        user=get_user_model().objects.create_user('mgr-led',password='Unrelated-test-password-729!');Staff.objects.create(code='MGR-L',name='Mgr L',branch=other,user=user,role=Role.objects.get(code='manager'))
        self.client.force_login(user);self.assertEqual(self.client.get(reverse('statement',args=[sale.customer.pk])).status_code,404)       # another shop's customer does not exist for this shop


class Access(LedgerBase):
    def test_customer_and_supplier_lists_link_to_statements(self):
        Party.objects.create(code='STM-C',kind='Customer',name='Statement Link Customer')
        self.assertContains(self.client.get(reverse('collection',args=['Customer'])),'Statement')
