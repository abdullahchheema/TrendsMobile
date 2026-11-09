"""Phase E: credit terms, instalments, limits, overdue list, party profiles, POS search feed and IMEI on receipts."""
import json
from datetime import date,timedelta
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import credit,easy_retail,services as s


class CreditBase(TestCase):
    def setUp(self):
        self.__dict__.update(fixtures());self.client.force_login(self.user);self.today=timezone.localdate();self.n=0
    def phone(self,cost=50000):
        self.n+=1
        data=dict(branch=self.branch,party_name='Credit Supplier',product=self.product,quantity=1,price=cost,asking_price=90000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,
                  posting_date=self.today,payment_status='full',method='Cash',imeis=[[imei(500+self.n)]])
        return easy_retail.purchase(data,self.user).reference.lines.get().device
    def sale(self,price=60000,paid='unpaid',amount=None,name='Credit Customer',on=None,**extra):
        data=dict(branch=self.branch,party_name=name,phone=self.phone(),price=price,pta_status='PTA',staff=self.seller,handler=self.handler,posting_date=on or self.today,payment_status=paid,method='Cash');data.update(extra)
        if amount:data['amount_paid']=amount
        return easy_retail.sale(data,self.user)
    def pay(self,sale,amount):
        return s.submit(s.new_doc('Payment Entry',self.branch,customer=sale.customer,reference=sale,handler=self.handler,data=dict(direction='Receive',mode='Cash',amount=str(amount))),self.user)


class Plans(CreditBase):
    def test_instalments_split_exactly_with_month_end_dates(self):
        sale=self.sale(100000,on=date(2026,1,15));credit.plan(sale,self.user,3,date(2026,1,31))
        rows=list(sale.installments.all());self.assertEqual([r.amount for r in rows],[Decimal('33333.34'),Decimal('33333.33'),Decimal('33333.33')])
        self.assertEqual([r.due_date for r in rows],[date(2026,1,31),date(2026,2,28),date(2026,3,31)]);sale.refresh_from_db();self.assertEqual(sale.due_date,date(2026,3,31))
    def test_payments_are_applied_to_the_oldest_instalment_first_and_overdue_follows_what_is_paid(self):
        sale=self.sale(90000,on=self.today-timedelta(days=100));credit.plan(sale,self.user,3,self.today-timedelta(days=65))     # due -65, -35, -5 days ago
        self.assertEqual(credit.overdue_amount(sale),Decimal('90000.00'))
        self.pay(sale,30000);sale.refresh_from_db();rows=credit.schedule(sale)
        self.assertEqual([(r['paid'],r['left']) for r in rows],[(Decimal('30000.00'),Decimal('0.00')),(Decimal('0.00'),Decimal('30000.00')),(Decimal('0.00'),Decimal('30000.00'))])
        self.assertEqual(credit.overdue_amount(sale),Decimal('60000.00'))
        self.pay(sale,60000);sale.refresh_from_db();self.assertEqual(credit.overdue_amount(sale),Decimal('0'))
    def test_single_due_date_is_overdue_only_after_it_passes_and_only_while_unpaid(self):
        sale=self.sale(50000);credit.plan(sale,self.user,0,self.today+timedelta(days=3));self.assertEqual(credit.overdue_amount(sale),Decimal('0'))
        self.assertEqual(credit.overdue_amount(sale,self.today+timedelta(days=4)),Decimal('50000.00'))
    def test_rules_paid_sales_before_sale_date_and_missing_date_are_rejected(self):
        paid=self.sale(50000,paid='full');unpaid=self.sale(50000)
        with self.assertRaisesMessage(ValidationError,'unpaid balance'):credit.plan(paid,self.user,0,self.today)
        with self.assertRaisesMessage(ValidationError,'before the sale date'):credit.plan(unpaid,self.user,0,self.today-timedelta(days=5))
        with self.assertRaisesMessage(ValidationError,'Choose the date'):credit.plan(unpaid,self.user,0,None)
    def test_counter_sale_creates_the_plan_in_one_save(self):
        sale=self.sale(60000,paid='part',amount=20000,due_date=self.today+timedelta(days=30),instalments=2)
        self.assertEqual([r.amount for r in sale.installments.all()],[Decimal('20000.00'),Decimal('20000.00')])
        with self.assertRaisesMessage(ValidationError,'first instalment date'):self.sale(60000,instalments=2)
    def test_plan_page_shows_the_schedule_and_saves_changes(self):
        sale=self.sale(60000);page=self.client.post(reverse('payment_plan',args=[sale.pk]),dict(first_due=str(self.today+timedelta(days=10)),instalments='2'),follow=True)
        self.assertContains(page,'Payment plan saved');self.assertContains(self.client.get(reverse('payment_plan',args=[sale.pk])),'Upcoming')


class Limits(CreditBase):
    def setUp(self):
        super().setUp();self.clerk_user=get_user_model().objects.create_user('clerk-cr',password='Unrelated-test-password-729!')
        self.clerk=Staff.objects.create(code='CLK-CR',name='Clerk CR',branch=self.branch,user=self.clerk_user,role=Role.objects.get(code='sales'))
        self.boss=Staff.objects.create(code='BOSS-CR',name='Boss CR',branch=self.branch,role=Role.objects.get(code='manager'),approval_pin=make_password('7391'))
        Party.objects.create(code='LIM-C',kind='Customer',name='Limited Customer',credit_limit=Decimal('50000'))
    def attempt(self,price,**extra):return self.sale(price,name='Limited Customer',staff=self.clerk,**extra)
    def test_a_sale_within_the_limit_posts_and_one_above_it_is_refused_and_rolled_back(self):
        self.assertEqual(self.attempt(40000).status,'Submitted')
        with self.assertRaisesMessage(PermissionDenied,'credit limit'):self.attempt(20000)
        self.assertEqual(credit.customer_balance(Party.objects.get(name='Limited Customer')),Decimal('40000'))
    def test_manager_override_allows_it_and_no_limit_means_unlimited(self):
        self.attempt(40000);big=self.attempt(30000,override_approver='BOSS-CR',override_pin='7391');self.assertEqual(big.status,'Submitted')
        self.assertEqual(self.sale(500000,name='Unlimited Customer').status,'Submitted')
    def test_paying_part_in_cash_counts_only_what_is_still_owed(self):
        self.assertEqual(self.attempt(80000,paid='part',amount=40000).outstanding,Decimal('40000.00'))


class OverdueList(CreditBase):
    def test_overdue_groups_with_reminder_text_and_whatsapp_number(self):
        Party.objects.create(code='OVD',kind='Customer',name='Late Larry',phone='0300 1234567')
        sale=self.sale(70000,name='Late Larry',on=self.today-timedelta(days=60));credit.plan(sale,self.user,0,self.today-timedelta(days=10))
        page=self.client.get(reverse('overdue'));group=page.context['groups'][0]
        self.assertEqual((group['customer'].name,group['total'],group['oldest']),('Late Larry',Decimal('70000.00'),10))
        self.assertIn('PKR 70,000',group['message']);self.assertIn('Late Larry',group['message']);self.assertTrue(group['whatsapp'].startswith('https://wa.me/923001234567?text='))
        self.assertContains(page,'Open in WhatsApp')
    def test_current_and_paid_credit_are_not_listed_and_sales_staff_have_no_access(self):
        ok=self.sale(40000);credit.plan(ok,self.user,0,self.today+timedelta(days=9))
        late=self.sale(40000,on=self.today-timedelta(days=9));credit.plan(late,self.user,0,self.today-timedelta(days=2));self.pay(late,40000)
        self.assertEqual(self.client.get(reverse('overdue')).context['groups'],[])
        clerk=get_user_model().objects.create_user('clerk-od',password='Unrelated-test-password-729!');Staff.objects.create(code='CLK-OD',name='Clerk OD',branch=self.branch,user=clerk,role=Role.objects.get(code='sales'))
        self.client.force_login(clerk);self.assertEqual(self.client.get(reverse('overdue')).status_code,403)
    def test_phone_numbers_are_normalised_for_whatsapp(self):
        for raw,out in (('0300-1234567','923001234567'),('+92 300 1234567','923001234567'),('0092 300 1234567','923001234567'),('3001234567','923001234567'),('123','')):
            self.assertEqual(credit.whatsapp_number(raw),out,raw)


class Profiles(CreditBase):
    def test_customer_profile_shows_balance_overdue_devices_with_warranty_and_history(self):
        Party.objects.create(code='PRF',kind='Customer',name='Profile Pat',phone='0301 7654321')
        sale=self.sale(60000,paid='part',amount=10000,name='Profile Pat',on=self.today-timedelta(days=40));credit.plan(sale,self.user,0,self.today-timedelta(days=5))
        Device.objects.filter(pk=sale.lines.get().device_id).update(warranty_days=30)
        page=self.client.get(reverse('party',args=[sale.customer.pk]));ctx=page.context
        self.assertEqual((ctx['balance'],ctx['overdue'],ctx['total']),(Decimal('50000.00'),Decimal('50000.00'),Decimal('60000.00')))
        self.assertEqual(ctx['devices'][0]['warranty']['valid'],False);self.assertContains(page,'Expired');self.assertContains(page,sale.code)
    def test_supplier_profile_lists_supplied_phones_and_what_we_owe(self):
        self.phone();supplier=Party.objects.get(name='Credit Supplier');self.n+=1
        data=dict(branch=self.branch,party_name='Credit Supplier',product=self.product,quantity=1,price=40000,asking_price=90000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,staff=self.buyer,handler=self.handler,posting_date=self.today,payment_status='unpaid',method='Cash',imeis=[[imei(590)]])
        easy_retail.purchase(data,self.user);ctx=self.client.get(reverse('party',args=[supplier.pk])).context
        self.assertEqual(ctx['balance'],Decimal('40000.00'));self.assertEqual(len(ctx['supplied']),2)
    def test_phone_validation_accepts_pakistani_formats_and_rejects_nonsense(self):
        for good in ('0300 1234567','+92 300 1234567','042-35761234','(042) 3576 1234'):Party(code='P'+good[-4:],kind='Customer',name='N'+good[-4:],phone=good).full_clean()
        for bad in ('abc','12','0300-12x4567'):
            with self.assertRaises(ValidationError):Party(code='BAD',kind='Customer',name='Bad',phone=bad).full_clean()
    def test_lists_link_to_profiles(self):
        Party.objects.create(code='LNK',kind='Customer',name='Link Larry');self.assertContains(self.client.get(reverse('collection',args=['Customer'])),'Profile')


class PosAndReceipts(CreditBase):
    def test_pos_feed_carries_imei_brand_storage_and_condition_for_search(self):
        device=self.phone();self.product.model.brand='Apple';self.product.model.save();Product.objects.filter(pk=self.product.pk).update(storage='128GB',colour='Blue')
        s.submit(s.new_doc('POS Opening Entry',self.branch,handler=self.handler,till=self.till,data={'opening_float':'0'}),self.user)
        items=json.loads(self.client.get(reverse('pos')).context['items'] if isinstance(self.client.get(reverse('pos')).context['items'],str) else json.dumps(self.client.get(reverse('pos')).context['items']))
        item=next(i for i in items if i['device']==str(device.pk))
        self.assertEqual((item['brand'],item['storage'],item['colour'],item['condition']),('Apple','128GB','Blue','Good'));self.assertEqual(item['barcodes'][1:],item['imeis']);self.assertEqual(len(item['imeis']),1)
        self.assertContains(self.client.get(reverse('pos')),'pos-brand')
    def test_sale_receipt_prints_the_phone_imei(self):
        sale=self.sale(60000,paid='full');number=sale.lines.get().device.imeis.get().number
        self.assertContains(self.client.get(reverse('receipt',args=[sale.pk])),'IMEI '+number)
