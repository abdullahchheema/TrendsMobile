"""Whole-CRM QA runner. Only runs against an explicitly isolated QA site."""
import json,time,unittest
from pathlib import Path
import frappe
from frappe.utils import flt,nowdate,now_datetime

if not frappe.conf.get('covison_qa') or frappe.local.site=='frontend':
    raise RuntimeError('Full QA requires an isolated covison_qa site.')
frappe.in_test=True
namespace=globals()
exec(Path('/tmp/verify_retail_workflows.py').read_text().split('result=unittest.TextTestRunner',1)[0],namespace)
exec(Path('/tmp/verify_staff.py').read_text().split('result=unittest.TextTestRunner',1)[0],namespace)
from covison_erp.workflows import checkout,return_phone,post_payment,device_expense,purchase_bill
from covison_erp.staff_reports import activity,summary
from erpnext.accounts.doctype.pos_closing_entry.pos_closing_entry import make_closing_entry_from_opening
from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry


class ExtendedCRMChecks(RetailWorkflowChecks):
    def enable_sample_staff(self):
        frappe.db.set_value('Mobile Staff',{},'enabled',1,update_modified=False)
        manager=frappe.db.get_value('Mobile Staff',{'staff_name':'Ahmed Raza'},'name')
        frappe.db.set_value('Mobile Staff',manager,'user','Administrator',update_modified=False)
        return manager

    def posted_sale(self,paid=False):
        self.register(self.phone)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000)['name'])
        if paid:
            checkout(sale.name,[dict(mode_of_payment='Cash',amount=65000)])
            sale.reload()
        else:sale.submit()
        return sale

    def test_sample_staff_complete_shop_journey(self):
        manager=self.enable_sample_staff()
        intake=self.intake()
        self.assertEqual(intake.covison_purchased_by,manager)
        bill=frappe.get_doc('Purchase Invoice',purchase_bill(intake.purchase_receipt));bill.submit()
        payment=post_payment(bill,'Cash',35000)
        self.assertEqual(payment.covison_handled_by,manager)
        sale=self.posted_sale(paid=True)
        self.assertEqual(sale.covison_sold_by,manager)
        _,rows=summary({'company':self.phone.company,'staff':manager})
        self.assertEqual(rows[0]['sales'],65000);self.assertEqual(rows[0]['collected'],65000)
        self.assertEqual(rows[0]['purchases'],35000);self.assertEqual(rows[0]['paid'],35000)

    def test_salesperson_can_submit_valid_credit_sale(self):
        self.register(self.phone)
        customer=self.customer()
        user=frappe.get_doc(dict(doctype='User',email='qa-valid-sales@example.invalid',first_name='QA Sales',send_welcome_email=0,
             roles=[dict(role='Sales User'),dict(role='Mobile Salesperson')])).insert()
        frappe.set_user(user.name)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,customer,65000)['name'])
        sale.submit()
        self.assertEqual(sale.docstatus,1)

    def test_discount_cannot_bypass_minimum_price(self):
        self.register(self.phone)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000)['name'])
        user=frappe.get_doc(dict(doctype='User',email='qa-discount@example.invalid',first_name='QA Discount',send_welcome_email=0,
             roles=[dict(role='Sales User'),dict(role='Mobile Salesperson')])).insert()
        frappe.set_user(user.name)
        sale.items[0].discount_percentage=100;sale.items[0].rate=0
        with self.assertRaises(frappe.ValidationError):sale.save()

    def test_duplicate_draft_cannot_double_sell_one_phone(self):
        self.register(self.phone);customer=self.customer()
        a=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,customer,65000)['name'])
        b=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,customer,65000)['name'])
        a.submit()
        with self.assertRaises(frappe.ValidationError):b.submit()
        self.assertEqual(frappe.db.count('Sales Invoice',{'docstatus':1}),1)

    def test_serial_transfer_updates_device_location_and_cost(self):
        target=frappe.db.get_value('Warehouse',{'warehouse_name':'Finished Goods','company':self.phone.company},'name')
        before=self.phone.purchase_cost
        doc=frappe.get_doc(dict(doctype='Stock Entry',stock_entry_type='Material Transfer',company=self.phone.company,
            items=[dict(item_code=self.phone.item_code,qty=1,s_warehouse=self.phone.warehouse,t_warehouse=target,
                serial_no=self.phone.serial_no,use_serial_batch_fields=1)])).insert();doc.submit()
        self.phone.reload()
        self.assertEqual(self.phone.warehouse,target);self.assertEqual(self.phone.status,'Available')
        self.assertAlmostEqual(self.phone.purchase_cost,before,places=2)

    def test_return_preserves_original_sale_warranty_snapshot(self):
        sale=self.posted_sale()
        self.phone.reload();self.phone.warranty_days=30;self.phone.save()
        credit=frappe.get_doc('Sales Invoice',return_phone(sale.name,self.phone.name,'Same condition','Checking warranty'))
        self.assertEqual(credit.items[0].covison_warranty_days,7)

    def test_removing_second_imei_resets_its_pta_evidence(self):
        self.register(self.phone)
        self.phone.imei_2='356938035643809';self.phone.pta_status_2='Compliant';self.phone.pta_verified_on_2=nowdate();self.phone.save()
        self.phone.imei_2=None;self.phone.save()
        self.assertEqual(self.phone.pta_status_2,'Not Checked')
        self.assertFalse(self.phone.pta_verified_on_2)

    def test_native_refund_of_paid_return(self):
        sale=self.posted_sale(paid=True)
        credit=frappe.get_doc('Sales Invoice',return_phone(sale.name,self.phone.name,'Same','Refund request'));credit.submit()
        sale.reload()
        target=credit if abs(flt(credit.outstanding_amount)) else sale
        from covison_erp.workflows import payment_account
        payment=get_payment_entry('Sales Invoice',target.name,bank_account=payment_account(self.phone.company,'Cash')[0])
        payment.mode_of_payment='Cash';payment.insert();payment.submit()
        self.assertEqual(payment.payment_type,'Pay');self.assertEqual(payment.paid_amount,65000)
        sale.reload();credit.reload()
        self.assertEqual(sale.outstanding_amount,0);self.assertEqual(credit.outstanding_amount,0)

    def test_expense_credit_reverses_device_expense(self):
        bill=frappe.get_doc('Purchase Invoice',device_expense(self.phone.name,self.phone.supplier,2500,'Repair'));bill.submit()
        returned=make_return_doc('Purchase Invoice',bill.name);returned.insert();returned.submit()
        from covison_erp.mobile_shop.report.mobile_device_economics.mobile_device_economics import execute
        _,rows,_=execute()
        self.assertEqual(next(r for r in rows if r.name==self.phone.name).repair_expenses,0)

    def test_deferred_pos_shift_closes_and_counts_staff_once(self):
        self.enable_sample_staff();self.register(self.phone)
        settings=frappe.get_single('POS Settings');settings.invoice_type='POS Invoice';settings.save()
        opening=frappe.get_doc(dict(doctype='POS Opening Entry',company=self.phone.company,pos_profile='Covison Mobile Shop',
            user='Administrator',period_start_date=now_datetime(),balance_details=[dict(mode_of_payment='Cash',opening_amount=0)])).insert();opening.submit()
        pos=frappe.get_doc(dict(doctype='POS Invoice',update_stock=1,company=self.phone.company,customer=self.customer(),
            pos_profile='Covison Mobile Shop',currency='PKR',conversion_rate=1,selling_price_list='Standard Selling PKR',
            taxes_and_charges='',taxes=[],payments=[dict(mode_of_payment='Cash',amount=65000)],
            items=[dict(item_code=self.phone.item_code,qty=1,rate=65000,warehouse=self.phone.warehouse,
                serial_no=self.phone.serial_no,use_serial_batch_fields=1,covison_device=self.phone.name)])).insert();pos.submit()
        self.phone.reload();self.phone.warranty_days=30;self.phone.save()
        closing=make_closing_entry_from_opening(opening)
        for row in closing.payment_reconciliation:row.closing_amount=row.expected_amount
        closing.flags.in_test=True;closing.submit()
        pos.reload();self.phone.reload()
        self.assertTrue(pos.consolidated_invoice)
        self.assertEqual(frappe.get_doc('Sales Invoice',pos.consolidated_invoice).items[0].covison_warranty_days,7)
        self.assertEqual(self.phone.status,'Sold')
        _,rows=summary({'company':self.phone.company,'staff':pos.covison_sold_by})
        self.assertEqual(rows[0]['sales'],65000);self.assertEqual(rows[0]['collected'],65000)

    def test_phone_pos_requires_stock_tracking(self):
        self.register(self.phone)
        settings=frappe.get_single('POS Settings');settings.invoice_type='POS Invoice';settings.save()
        opening=frappe.get_doc(dict(doctype='POS Opening Entry',company=self.phone.company,pos_profile='Covison Mobile Shop',
            user='Administrator',period_start_date=now_datetime(),balance_details=[dict(mode_of_payment='Cash',opening_amount=0)])).insert();opening.submit()
        pos=frappe.get_doc(dict(doctype='POS Invoice',update_stock=0,company=self.phone.company,customer=self.customer(),
            pos_profile='Covison Mobile Shop',currency='PKR',conversion_rate=1,selling_price_list='Standard Selling PKR',
            payments=[dict(mode_of_payment='Cash',amount=65000)],
            items=[dict(item_code=self.phone.item_code,qty=1,rate=65000,warehouse=self.phone.warehouse,
                serial_no=self.phone.serial_no,use_serial_batch_fields=1,covison_device=self.phone.name)]))
        with self.assertRaisesRegex(frappe.ValidationError,'Update Stock'):pos.insert()

    def test_cashier_field_permissions_hide_valuation(self):
        from frappe.client import get as get_document
        user=frappe.get_doc(dict(doctype='User',email='qa-cost-read@example.invalid',first_name='QA Cost Read',send_welcome_email=0,
            roles=[dict(role='Mobile Cashier'),dict(role='Sales User'),dict(role='Accounts User')])).insert()
        frappe.set_user(user.name)
        doc=get_document('Mobile Device',self.phone.name)
        self.assertFalse(doc.get('purchase_cost'));self.assertFalse(doc.get('minimum_price'));self.assertFalse(doc.get('supplier'))

    def test_all_custom_and_core_reports_load(self):
        from frappe.desk.query_report import run
        filters={'company':self.phone.company,'from_date':nowdate()[:7]+'-01','to_date':nowdate(),'report_date':nowdate()}
        for report in ('Mobile Inventory','Device Profit','Mobile Stock Ageing','Mobile Device Economics','Staff Activity','Staff Summary',
                       'General Ledger','Accounts Receivable','Accounts Payable','Stock Balance'):
            with self.subTest(report=report):
                result=run(report,filters=filters,ignore_prepared_report=True)
                self.assertIn('columns',result)

    def test_page_sidebar_and_pos_profile_integrity(self):
        from frappe.desk.desk_page import get
        from frappe.desk.doctype.dock.dock import get_site_dock
        page=get('mobile-shop');self.assertIn('Mobile Shop Dashboard',page.script)
        self.assertNotIn('Your shop. Every phone.',page.script)
        self.assertIn('point-of-sale.bundle.js',get('point-of-sale').script)
        self.assertIn('Retail',frappe.get_active_domains())
        for sidebar in ('Mobile Shop','Point of Sale'):
            for item in frappe.get_doc('Sidebar',sidebar).items:
                if item.type!='Link' or item.link_type=='URL':continue
                self.assertTrue(frappe.db.exists(item.link_type,item.link_to),(item.link_type,item.link_to))
        self.assertTrue(any(r.get('link_to')=='Point of Sale' and not r.get('hidden') for r in get_site_dock('erpnext')))
        profile=frappe.get_doc('POS Profile','Covison Mobile Shop')
        self.assertFalse(profile.disabled);self.assertEqual(profile.currency,'PKR')
        for mode in ('Cash','Easypaisa','JazzCash','Bank Transfer','Card'):
            from covison_erp.workflows import payment_account
            self.assertTrue(payment_account(self.phone.company,mode)[0])

suite=unittest.TestSuite();seen=set()
for case in (ExtendedCRMChecks,StaffChecks):
    for test in unittest.defaultTestLoader.loadTestsFromTestCase(case):
        if test._testMethodName not in seen:
            seen.add(test._testMethodName);suite.addTest(test)
started=time.monotonic()
result=unittest.TextTestRunner(verbosity=2).run(suite)
report={'site':frappe.local.site,'tests':result.testsRun,'failures':[{'test':str(t),'detail':d} for t,d in result.failures],
    'errors':[{'test':str(t),'detail':d} for t,d in result.errors],'skipped':[(str(t),why) for t,why in result.skipped],
    'elapsed_seconds':round(time.monotonic()-started,2),'passed':result.wasSuccessful()}
Path('/tmp/covison-qa-results.json').write_text(json.dumps(report,indent=2))
frappe.db.rollback()
if not result.wasSuccessful():raise AssertionError('Full CRM QA found failures; see /tmp/covison-qa-results.json')
