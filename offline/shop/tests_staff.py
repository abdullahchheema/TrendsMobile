"""Shop staff without logins: added by the owner, recorded on every sale and purchase, changed afterwards only by the owner, with commission reports."""
import json
from datetime import timedelta
from decimal import Decimal
from django.urls import reverse
from .models import *
from .tests_pos import PosBase
from . import bulk,easy_retail,services as s


class StaffBase(PosBase):
    def add(self,name='Bilal',branch=None,commission='2.5'):
        self.client.force_login(self.user)
        self.client.post(reverse('staff_list'),dict(action='add',name=name,branch=str((branch or self.branch).pk),designation='Salesman',commission=commission))
        return Staff.objects.get(name=name,branch=branch or self.branch)


class Management(StaffBase):
    def test_only_the_owner_manages_staff_and_the_input_is_checked(self):
        self.client.force_login(self.a);self.assertEqual(self.client.get(reverse('staff_list')).status_code,403)
        person=self.add();self.assertEqual((person.crew,person.can_sell,person.can_buy,person.user_id,person.commission_rate),(True,True,True,None,Decimal('2.50')))
        self.client.post(reverse('staff_list'),dict(action='add',name='Bad',branch=str(self.branch.pk),commission='150'));self.assertFalse(Staff.objects.filter(name='Bad').exists())
        self.client.post(reverse('staff_list'),dict(action='add',name='bilal',branch=str(self.branch.pk),commission='1'));self.assertEqual(Staff.objects.filter(branch=self.branch,name__iexact='bilal').count(),1)
        self.client.post(reverse('staff_list'),dict(action='update',staff=str(person.pk),designation='Senior',commission='3'));person.refresh_from_db();self.assertEqual((person.designation,person.commission_rate),('Senior',Decimal('3.00')))
        self.assertContains(self.client.get(reverse('staff_list')),'Bilal')


class Recording(StaffBase):
    def test_a_shop_with_staff_must_name_who_made_each_sale_at_the_pos(self):
        device=self.stock();person=self.add();self.login(self.a)
        page=self.client.get(reverse('pos'));self.assertContains(page,'Sold by');self.assertContains(page,'Bilal')
        data=self.payload(device);data['seller']=''
        bad=self.checkout(data);self.assertEqual(bad.status_code,400);self.assertIn('who made this sale',bad.json()['error']);device.refresh_from_db();self.assertEqual(device.status,'Available')
        data=self.payload(device);data['seller']=str(person.pk);ok=self.checkout(data);self.assertEqual(ok.status_code,200,ok.content)
        doc=Document.objects.filter(kind__in=['POS Invoice','Sales Invoice'],is_return=False).latest('created');self.assertEqual((doc.seller_id,doc.staff_names['seller']),(person.pk,'Bilal'))
    def test_a_person_from_another_shop_cannot_be_chosen(self):
        device=self.stock();stranger=self.add('Sana',branch=self.other);self.add('Bilal');self.login(self.a);data=self.payload(device);data['seller']=str(stranger.pk)
        self.assertNotEqual(self.checkout(data).status_code,200);device.refresh_from_db();self.assertEqual(device.status,'Available')
    def test_a_shop_without_staff_works_as_before(self):
        device=self.stock();self.login(self.a);self.assertNotContains(self.client.get(reverse('pos')),'Sold by');self.assertEqual(self.checkout(self.payload(device)).status_code,200)
    def test_purchases_ask_who_bought_once_the_shop_has_staff(self):
        self.assertFalse(bulk.BulkForm(user=self.user,branch=self.branch).fields['staff'].required)
        self.add();form=bulk.BulkForm(user=self.user,branch=self.branch);self.assertTrue(form.fields['staff'].required);self.assertEqual(form.fields['staff'].label,'Bought by')


class Afterwards(StaffBase):
    def sale_by(self,person,price=60000):
        bill=self.phone();device=bill.reference.lines.get().device
        return easy_retail.sale(dict(branch=self.branch,party_name='Staff Customer',phone=device,price=price,pta_status='PTA',staff=person,handler=self.handler,posting_date=self.today-timedelta(days=1),payment_status='full',method='Cash',amount_paid=price),self.user)
    def test_only_the_owner_can_change_who_a_posted_sale_is_credited_to(self):
        bilal=self.add();sana=self.add('Sana');doc=self.sale_by(bilal);self.assertEqual(doc.staff_names['seller'],'Bilal')
        self.login(self.a);page=self.client.get(reverse('detail',args=[doc.pk]));self.assertContains(page,'Bilal');self.assertNotContains(page,'Change (owner only)')       # the shop can see it, not change it
        self.assertEqual(self.client.post(reverse('change_staff',args=[doc.pk]),dict(staff=str(sana.pk))).status_code,403);doc.refresh_from_db();self.assertEqual(doc.seller_id,bilal.pk)
        self.client.force_login(self.user);self.assertContains(self.client.get(reverse('detail',args=[doc.pk])),'Change (owner only)')
        self.client.post(reverse('change_staff',args=[doc.pk]),dict(staff=str(sana.pk)));doc.refresh_from_db();self.assertEqual((doc.seller_id,doc.staff_names['seller']),(sana.pk,'Sana'))
        self.assertTrue(Audit.objects.filter(action='Staff changed on a posted document',document=doc).exists())
        stranger=self.add('Omar',branch=self.other);self.client.post(reverse('change_staff',args=[doc.pk]),dict(staff=str(stranger.pk)));doc.refresh_from_db();self.assertEqual(doc.seller_id,sana.pk)   # another shop's person: refused
    def test_the_owner_sees_a_persons_full_record_and_commission(self):
        bilal=self.add(commission='2.5');a=self.sale_by(bilal,60000);b=self.sale_by(bilal,40000);self.sale_by(self.add('Sana'),99000)
        self.client.force_login(self.user);page=self.client.get(reverse('staff_detail',args=[bilal.pk]))
        self.assertContains(page,a.code);self.assertContains(page,b.code);self.assertNotContains(page,'99,000')
        ctx=page.context;self.assertEqual((ctx['net'],ctx['commission'],ctx['sale_count']),(Decimal('100000.00'),Decimal('2500.00'),2))
        self.login(self.a);self.assertEqual(self.client.get(reverse('staff_detail',args=[bilal.pk])).status_code,403)
        self.client.force_login(self.user);self.assertContains(self.client.get(reverse('staff_list')),'2 sales')


class Scanner(StaffBase):
    def test_every_screen_loads_the_camera_scanner_and_its_library_is_bundled(self):
        from django.contrib.staticfiles import finders
        self.client.force_login(self.user);page=self.client.get(reverse('dashboard')).content.decode()
        self.assertIn('scan.',page);self.assertIn('data-zxing',page);self.assertIn('zxing-library',page)
        self.assertTrue(finders.find('scan.js') and finders.find('vendor/zxing-library-0.21.3.min.js') and finders.find('vendor/zxing-library-LICENSE.txt'))
