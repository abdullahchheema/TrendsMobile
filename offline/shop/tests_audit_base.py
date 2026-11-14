"""Shared fixture for the QA audit tests (qa-audit/TEST_DATA_DESIGN.md).

Everything is created the way the business creates it: shops through the owner's shop set-up, logins through Shop Accounts, stock through
real purchases (receipt, supplier bill, payment), sales through the POS checkout endpoint. No test shortcut writes a ledger row or a stock
movement by hand, so whatever the checks find is something the real screens can produce."""
import json
import uuid
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.db.models import Sum
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from .models import *
from . import accessories,coa,easy_retail,money,pricing,setup,shop_accounts,shopdefaults,services as s
from . import tests_audit_oracle as oracle

PASSWORD='Audit-only-password-4471!'


def imei(n):
    """A valid 15-digit IMEI (Luhn check digit worked out here, not by the application)."""
    body='35693803'+f'{n:06d}';digits=[int(c) for c in body]
    total=sum((d*2//10+d*2%10) if i%2 else d for i,d in enumerate(digits))
    return body+str((10-total%10)%10)


def P(amount):
    """Decimal rupees -> whole paisa (exact; refuses a value that is not a whole number of paisa)."""
    value=Decimal(str(amount))*100
    if value!=value.to_integral_value():raise AssertionError(f'{amount} is not a whole number of paisa')
    return int(value)


class AuditMixin:
    def build(self):
        User=get_user_model()
        self.owner=User.objects.create_superuser('audit-owner',password=PASSWORD)
        coa.install_standard(self.owner)
        self.a=setup.create_shop(dict(code='AUDA',name='Audit Shop A',company='Audit Co',payment_methods=['Cash','Bank Transfer','Easypaisa']),self.owner)
        self.b=setup.create_shop(dict(code='AUDB',name='Audit Shop B',company='Audit Co',payment_methods=['Cash','Bank Transfer']),self.owner)
        self.login_a=shop_accounts.create_login(self.a,'audit-shop-a',PASSWORD)
        self.login_b=shop_accounts.create_login(self.b,'audit-shop-b',PASSWORD)
        self.online_login=shop_accounts.create_online_login('Online Manager','audit-online',PASSWORD)
        self.model=PhoneModel.objects.create(code='AUD-M1',name='Galaxy A99',brand='Samsung')
        self.phone_product=Product.objects.create(code='AUD-P1',name='Galaxy A99 128GB Black',model=self.model,brand='Samsung',storage='128GB',colour='Black',serialized=True,stock_item=True)
        self.today=timezone.localdate();self._imei=0
    # ------------------------------------------------------------------ people
    def as_user(self,user):
        client=Client();client.force_login(user);return client
    # ------------------------------------------------------------------ money in
    def capital(self,branch,amount,method='Cash',when=None):
        return money.record_voucher(self.owner,branch,'capital',Account.objects.get(code='COA-3100'),amount,method,when or self.today,'Owner capital for the audit')
    # ------------------------------------------------------------------ purchases
    def buy_phone(self,branch,cost,asking,pay='unpaid',amount_paid=None,method='Cash',supplier='Audit Supplier',when=None,product=None,condition='Good',pta='PTA',kind='Used',user=None):
        """One phone bought through the same service the Buy a Phone screen uses. Returns (device, bill)."""
        self._imei+=1;me=shopdefaults.shop_person(branch)
        bill=easy_retail.purchase(dict(branch=branch,party_name=supplier,warehouse=shopdefaults.warehouse(branch),product=product or self.phone_product,quantity=1,price=str(cost),asking_price=str(asking),
            pta_status=pta,device_type=kind,condition=condition,staff=me,handler=me,posting_date=when or self.today,payment_status=pay,amount_paid=amount_paid,method=method,imeis=[[imei(self._imei)]],notes=''),user or self.owner)
        receipt=bill.reference
        return Device.objects.get(pk=receipt.lines.get().device_id),bill
    def buy_accessory(self,branch,name,qty,cost,price,pay='unpaid',amount_paid=None,method='Cash',supplier='Audit Supplier',when=None,user=None):
        me=shopdefaults.shop_person(branch)
        bill=accessories.post(dict(branch=branch,party_name=supplier,warehouse=shopdefaults.warehouse(branch),staff=me,handler=me,posting_date=when or self.today,notes='',payment_status=pay,amount_paid=amount_paid,method=method),
            [dict(name=name,category='Other',qty=qty,cost=Decimal(str(cost)),price=Decimal(str(price)),brand='')],user or self.owner)
        return Product.objects.get(serialized=False,stock_item=True,name__iexact=name),bill
    # ------------------------------------------------------------------ sales through the real POS endpoint
    def tax_rule(self,branch,rate,name='Sales tax'):
        rule=SalesTaxRule(code='TAX-'+uuid.uuid4().hex[:8].upper(),branch=branch,name=name,rate=Decimal(str(rate)),account=Account.objects.get(code='COA-2400'),reference='Audit test rate')
        rule.full_clean();rule.save();return rule
    def pos(self,user,branch,items,payments=(),customer='Walk-in Audit',discount_type='amount',discount_value='0',rule=None,key=None,client=None,**extra):
        """items: [(device or product, quantity, rate)]. Posts to /api/pos/checkout exactly as the New Sale screen does. Returns the HTTP response."""
        warehouse=shopdefaults.warehouse(branch);rows=[]
        for thing,qty,rate in items:
            if isinstance(thing,Device):rows.append(dict(product=str(thing.product_id),device=str(thing.pk),warehouse=str(warehouse.pk),quantity=str(qty),rate=str(rate)))
            else:rows.append(dict(product=str(thing.pk),device='',warehouse=str(warehouse.pk),quantity=str(qty),rate=str(rate)))
        body=dict(branch=str(branch.pk),customer_name=customer,items=rows,payments=list(payments),request_key=str(key or uuid.uuid4()),
                  adjustments=dict(discount_type=discount_type,discount_value=str(discount_value),tax_rule=str(rule.pk) if rule else '',tax_version=pricing.tax_version(rule) if rule else ''))
        body.update(extra)
        return (client or self.as_user(user)).post(reverse('pos_checkout'),json.dumps(body),content_type='application/json')
    # ------------------------------------------------------------------ reading the books back, straight from the ledger table
    def account_of(self,branch,key):
        config=ShopSettings.objects.get(branch=branch)
        return Account.objects.get(pk=config.accounts.get(key) or config.payment_methods[key])
    def balance(self,branch,key,document=None):
        """Debit minus credit of one of the shop's accounts, in paisa (optionally for one document only)."""
        rows=LedgerEntry.objects.filter(branch=branch,account=self.account_of(branch,key))
        if document is not None:rows=rows.filter(document=document)
        total=rows.aggregate(d=Sum('debit'),c=Sum('credit'))
        return P(total['d'] or 0)-P(total['c'] or 0)
    def ledger_rows(self,branches=None):
        """[(account code, debit paisa, credit paisa)] of every ledger row, for the oracle's own trial balance."""
        rows=LedgerEntry.objects.all() if branches is None else LedgerEntry.objects.filter(branch__in=branches)
        return [(code,P(d),P(c)) for code,d,c in rows.values_list('account__code','debit','credit')]
    def assert_books_clean(self,stock=True):
        """The application's own consistency checker and this audit's independent invariants must both be silent."""
        from . import books_check,online_check,tests_audit_invariants as inv
        problems=[f'{name}: {p}' for name,found in books_check.run_all() for p in found]+online_check.problems()+inv.all_problems()
        self.assertEqual(problems,[])
