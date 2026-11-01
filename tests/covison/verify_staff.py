"""Responsibility attribution, native POS, reporting and staff permission checks."""
import unittest
import frappe
from frappe.utils import nowdate, now_datetime
from covison_erp.api import create_sale
from covison_erp.workflows import checkout,purchase_bill,post_payment
from covison_erp.staff_reports import activity,summary
from covison_erp.staff import defaults
exec(open('/tmp/verify_mobile_shop.py').read().rsplit('\nrun()',1)[0])

class StaffChecks(MobileShopChecks):
    def staff(self,name,**kwargs):
        values=dict(doctype='Mobile Staff',staff_name=name,company=self.phone.company,designation='Other',can_sell=0,can_buy=0,can_handle_payments=0)
        values.update(kwargs)
        return frappe.get_doc(values).insert()

    def buy(self,person=None):
        doc=frappe.get_doc(dict(doctype='Mobile Intake',company=self.phone.company,supplier=self.phone.supplier,
            purchase_source='Individual Seller',posting_date=nowdate(),warehouse=self.phone.warehouse,
            item_code=self.phone.item_code,purchase_price=35000,imei_1='356938035643809',
            condition_grade='Good',inspection_status='Passed',covison_purchased_by=person))
        doc.insert();doc.submit();return doc

    def test_separate_salesperson_and_collector_attribution(self):
        seller=self.staff('Rollback Seller',can_sell=1)
        cashier=self.staff('Rollback Cashier',can_handle_payments=1)
        self.register(self.phone)
        sale=create_sale(self.phone.name,self.customer(),65000,sold_by=seller.name)['name']
        result=checkout(sale,[dict(mode_of_payment='Cash',amount=20000),dict(mode_of_payment='JazzCash',amount=30000,reference='STAFF-ROLLBACK')],collected_by=cashier.name)
        invoice=frappe.get_doc('Sales Invoice',sale)
        self.assertEqual(invoice.covison_seller_name,'Rollback Seller')
        self.assertEqual(invoice.covison_collector_name,'Rollback Cashier')
        self.assertEqual(invoice.outstanding_amount,15000)
        self.assertEqual(invoice.covison_posted_by,'Administrator')
        for name in result['payments']:
            self.assertEqual(frappe.db.get_value('Payment Entry',name,'covison_handled_by'),cashier.name)
        _,rows=summary({'company':self.phone.company})
        totals={r['staff']:r for r in rows}
        self.assertEqual(totals[seller.name]['sales'],65000)
        self.assertEqual(totals[seller.name]['collected'],0)
        self.assertEqual(totals[cashier.name]['sales'],0)
        self.assertEqual(totals[cashier.name]['collected'],50000)
        self.assertEqual(totals[seller.name]['phones_sold'],1)
        html=frappe.get_print('Sales Invoice',sale,print_format='Covison Mobile Receipt')
        self.assertIn('Rollback Seller',html);self.assertIn('Rollback Cashier',html)
        seller.staff_name='Renamed Seller';seller.save()
        self.assertEqual(frappe.db.get_value('Sales Invoice',sale,'covison_seller_name'),'Rollback Seller')

    def test_buyer_carries_into_receipt_and_supplier_bill(self):
        buyer=self.staff('Rollback Buyer',can_buy=1,user='Administrator')
        cashier=self.staff('Rollback Paying Cashier',can_handle_payments=1)
        intake=self.buy()
        self.assertEqual(intake.covison_purchased_by,buyer.name)
        self.assertEqual(frappe.db.get_value('Purchase Receipt',intake.purchase_receipt,'covison_purchased_by'),buyer.name)
        bill=frappe.get_doc('Purchase Invoice',purchase_bill(intake.purchase_receipt));bill.submit()
        self.assertEqual(bill.covison_purchased_by,buyer.name)
        entry=post_payment(bill,'Cash',35000,handled_by=cashier.name)
        self.assertEqual(entry.covison_handler_name,'Rollback Paying Cashier')
        _,rows=summary({'company':self.phone.company})
        totals={r['staff']:r for r in rows}
        self.assertEqual(totals[buyer.name]['purchases'],35000)
        self.assertEqual(totals[buyer.name]['phones_bought'],1)
        self.assertEqual(totals[cashier.name]['paid'],35000)

    def test_missing_assignment_blocks_submission_after_staff_setup(self):
        self.staff('Rollback Seller',can_sell=1)
        self.register(self.phone)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000)['name'])
        with self.assertRaises(frappe.ValidationError):sale.submit()
        self.assertEqual(frappe.db.get_value('Sales Invoice',sale.name,'docstatus'),0)

    def test_wrong_responsibility_and_inactive_staff_rejected(self):
        person=self.staff('Rollback Buyer',can_buy=1)
        self.register(self.phone)
        with self.assertRaises(frappe.ValidationError):create_sale(self.phone.name,self.customer(),65000,sold_by=person.name)
        person.can_sell=1;person.enabled=0;person.save()
        with self.assertRaises(frappe.ValidationError):create_sale(self.phone.name,self.customer(),65000,sold_by=person.name)

    def test_linked_login_defaults_and_directory_permissions(self):
        user=frappe.get_doc(dict(doctype='User',email='staff-check@example.invalid',first_name='Staff Check',send_welcome_email=0,
             roles=[dict(role='Mobile Salesperson'),dict(role='Sales User')])).insert()
        person=self.staff('Actual Linked Test Staff',can_sell=1,user=user.name)
        frappe.set_user(user.name)
        self.assertEqual(defaults(self.phone.company)['sold_by'],person.name)
        self.assertTrue(frappe.has_permission('Mobile Staff','read'))
        self.assertFalse(frappe.has_permission('Mobile Staff','create'))
        with self.assertRaises(frappe.PermissionError):activity({'company':self.phone.company})

    def test_pos_sale_and_collection_are_counted_once(self):
        person=self.staff('Rollback POS Operator',can_sell=1,can_handle_payments=1,user='Administrator')
        self.register(self.phone)
        opening=frappe.get_doc(dict(doctype='POS Opening Entry',company=self.phone.company,pos_profile='Covison Mobile Shop',
            user='Administrator',period_start_date=now_datetime(),balance_details=[dict(mode_of_payment='Cash',opening_amount=0)])).insert();opening.submit()
        invoice=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000)['name'])
        invoice.is_pos=1;invoice.pos_profile='Covison Mobile Shop';invoice.append('payments',dict(mode_of_payment='Cash',amount=65000))
        invoice.save();invoice.submit()
        self.assertEqual(invoice.covison_sold_by,person.name);self.assertEqual(invoice.covison_collected_by,person.name)
        _,rows=summary({'company':self.phone.company,'staff':person.name})
        self.assertEqual(len(rows),1);self.assertEqual(rows[0]['sales'],65000);self.assertEqual(rows[0]['collected'],65000)

    def test_staff_name_snapshot_cannot_be_forged(self):
        person=self.staff('Confirmed Staff Name',can_sell=1)
        self.register(self.phone)
        invoice=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000,sold_by=person.name)['name'])
        invoice.covison_seller_name='Forged Name';invoice.save()
        self.assertEqual(invoice.covison_seller_name,'Confirmed Staff Name')

    def test_duplicate_active_login_link_is_rejected(self):
        self.staff('First Linked Staff',can_sell=1,user='Administrator')
        with self.assertRaises(frappe.ValidationError):self.staff('Duplicate Staff',can_sell=1,user='Administrator')

result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(StaffChecks))
if not result.wasSuccessful():raise AssertionError('Staff checks failed')
