"""Phase B: discount limits and manager PIN overrides."""
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.exceptions import PermissionDenied,ValidationError
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from .models import *
from .tests import fixtures,imei
from . import services as s,easy_retail,overrides,pricing


class OverrideBase(TestCase):
    def setUp(self):
        cache.clear();self.__dict__.update(fixtures())
        self.clerk_user=get_user_model().objects.create_user('clerk-ovr',password='Unrelated-test-password-729!')
        self.clerk=Staff.objects.create(code='CLERK-OVR',name='Counter Clerk',branch=self.branch,user=self.clerk_user,role=Role.objects.get(code='sales'),default_warehouse=self.warehouse)  # 5% limit
        self.boss=Staff.objects.create(code='BOSS-OVR',name='Shop Boss',branch=self.branch,role=Role.objects.get(code='manager'),approval_pin=make_password('4821'))
        self.phone=self.stock_phone(imei(61),cost=50000,minimum=60000)
    def stock_phone(self,number,cost=50000,minimum=0):
        data=dict(branch=self.branch,party_name='Ovr Supplier',product=self.product,quantity=1,price=cost,asking_price=70000,pta_status='PTA',device_type='Used',condition='Good',warehouse=self.warehouse,
                  staff=self.buyer,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash',imeis=[[number]])
        device=easy_retail.purchase(data,self.user).reference.lines.get().device
        Device.objects.filter(pk=device.pk).update(minimum_price=minimum);device.refresh_from_db();return device
    def invoice(self,user,price=70000,percent=None,device=None):
        device=device or self.phone
        doc=s.new_doc('Sales Invoice',self.branch,customer=self.customer,seller=self.clerk,warehouse=device.warehouse,posting_date=timezone.localdate())
        if percent is not None:pricing.inputs(doc,'percent',str(percent));doc.save()
        Line.objects.create(document=doc,product=device.product,device=device,warehouse=device.warehouse,quantity=1,rate=price);return doc


class DiscountLimit(OverrideBase):
    def test_discount_within_the_role_limit_posts(self):
        done=s.checkout(self.invoice(self.clerk_user,percent=4),self.clerk_user,[],None);self.assertEqual(done.status,'Submitted')
    def test_manager_pin_override_lets_the_sale_through_and_is_audited(self):
        doc=self.invoice(self.clerk_user,percent=10);overrides.apply(doc,self.clerk_user,'BOSS-OVR','4821','Regular customer')
        done=s.checkout(doc,self.clerk_user,[],None)
        self.assertEqual(done.data['override']['code'],'BOSS-OVR');self.assertNotIn('4821',str(done.data))
        entry=Audit.objects.get(action='Manager override');self.assertEqual(entry.details['approved_by'],'BOSS-OVR');self.assertNotIn('4821',str(entry.details))
class PinSecurity(OverrideBase):
    def test_wrong_pin_unknown_code_and_blank_pin_are_rejected(self):
        doc=self.invoice(self.clerk_user,percent=10)
        for code,pin in (('BOSS-OVR','0000'),('NOBODY','4821'),('BOSS-OVR',''),('CLERK-OVR','4821')):
            with self.assertRaises(ValidationError):overrides.apply(doc,self.clerk_user,code,pin)
    def test_pin_locks_after_repeated_wrong_attempts_even_for_the_right_pin(self):
        doc=self.invoice(self.clerk_user,percent=10)
        for _ in range(overrides.MAX_TRIES):
            with self.assertRaises(ValidationError):overrides.apply(doc,self.clerk_user,'BOSS-OVR','1111')
        with self.assertRaisesMessage(ValidationError,'locked'):overrides.apply(doc,self.clerk_user,'BOSS-OVR','4821')
class BelowMinimum(OverrideBase):
    def sale_data(self,**extra):
        return dict(branch=self.branch,party_name='Walk In',phone=self.phone,price=55000,pta_status='PTA',staff=self.clerk,handler=self.handler,posting_date=timezone.localdate(),payment_status='unpaid',method='Cash',**extra)
    def test_counter_sale_below_minimum_posts_with_a_valid_override(self):
        sale=easy_retail.sale(self.sale_data(override_approver='BOSS-OVR',override_pin='4821'),self.clerk_user)
        self.assertEqual(sale.status,'Submitted');self.assertEqual(sale.data['override']['approved_by'],'Shop Boss')
    def test_a_wrong_pin_blocks_the_sale_and_leaves_stock_untouched(self):
        with self.assertRaises(ValidationError):easy_retail.sale(self.sale_data(override_approver='BOSS-OVR',override_pin='9999'),self.clerk_user)
        self.phone.refresh_from_db();self.assertEqual(self.phone.status,'Available')
    def test_the_sale_form_shows_the_override_fields_for_sales_only(self):
        self.client.force_login(self.clerk_user)
        self.assertContains(self.client.get(reverse('easy_entry',args=['sale'])),'Manager PIN')
        self.client.force_login(self.user);self.assertNotContains(self.client.get(reverse('easy_entry',args=['purchase'])),'Manager PIN')
