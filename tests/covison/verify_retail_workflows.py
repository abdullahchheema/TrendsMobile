"""Retail accounting/stock regression tests. Test records are always rolled back."""
import unittest
import frappe
from frappe.utils import nowdate, flt
from covison_erp.api import create_sale
from covison_erp.workflows import checkout, purchase_bill, settle_exchange, return_phone, device_expense

# Reuse established stock/role checks and rollback setup.
exec(open('/tmp/verify_mobile_shop.py').read().rsplit('\nrun()', 1)[0])

class RetailWorkflowChecks(MobileShopChecks):
    def intake(self, customer=None, amount=35000):
        doc = frappe.get_doc(dict(doctype='Mobile Intake', company=self.phone.company, supplier=self.phone.supplier,
            purchase_source='Customer Trade-In' if customer else 'Individual Seller', trade_in_customer=customer,
            posting_date=nowdate(), warehouse=self.phone.warehouse, item_code=self.phone.item_code,
            purchase_price=amount, imei_1='356938035643809', device_type='Used', condition_grade='Good',
            inspection_status='Passed', asking_price=50000, minimum_price=40000, battery_health_checked=1,
            battery_health=85, pta_status='Compliant', pta_verified_on=nowdate()))
        doc.insert(); doc.submit()
        return doc

    def exchange(self, outgoing=65000, incoming=35000):
        customer=self.customer()
        intake=self.intake(customer,incoming)
        bill=frappe.get_doc('Purchase Invoice',purchase_bill(intake.purchase_receipt)); bill.submit()
        self.register(self.phone)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,customer,outgoing)['name']);sale.submit()
        exchange=frappe.get_doc(dict(doctype='Mobile Exchange',company=self.phone.company,customer=customer,
            purchase_receipt=intake.purchase_receipt,sales_invoice=sale.name,purchase_invoice=bill.name)).insert()
        return exchange,sale,bill

    def test_intake_receives_one_exact_phone_and_bill_is_idempotent(self):
        doc=self.intake()
        phone=frappe.get_doc('Mobile Device',doc.mobile_device)
        self.assertEqual(phone.imei_1,doc.imei_1)
        self.assertEqual(phone.purchase_cost,35000)
        self.assertEqual(phone.status,'Available')
        self.assertEqual(phone.pta_status,'Compliant')
        bill=purchase_bill(doc.purchase_receipt)
        self.assertEqual(bill,purchase_bill(doc.purchase_receipt))
        self.assertEqual(frappe.db.get_value('Purchase Invoice',bill,'docstatus'),0)

    def test_split_checkout_partial_balance_and_retry(self):
        self.register(self.phone)
        sale=create_sale(self.phone.name,self.customer(),65000)['name']
        result=checkout(sale,[dict(mode_of_payment='Cash',amount=20000),dict(mode_of_payment='JazzCash',amount=30000,reference='ROLLBACK-TEST')])
        self.assertEqual(len(result['payments']),2)
        self.assertEqual(frappe.db.get_value('Sales Invoice',sale,'outstanding_amount'),15000)
        self.assertEqual(len(checkout(sale,[])['payments']),2)
        html=frappe.get_print('Sales Invoice',sale,print_format='Covison Mobile Receipt')
        self.assertIn('JazzCash',html);self.assertIn('ROLLBACK-TEST',html)
        gl=frappe.db.sql('select sum(debit-credit) from `tabGL Entry` where is_cancelled=0')[0][0]
        self.assertAlmostEqual(flt(gl),0,places=2)

    def test_checkout_requires_reference_before_posting(self):
        self.register(self.phone)
        sale=create_sale(self.phone.name,self.customer(),65000)['name']
        with self.assertRaises(frappe.ValidationError):
            checkout(sale,[dict(mode_of_payment='Easypaisa',amount=65000)])
        self.assertEqual(frappe.db.get_value('Sales Invoice',sale,'docstatus'),0)

    def test_exchange_customer_pays_offset_and_cash(self):
        exchange,sale,bill=self.exchange()
        result=settle_exchange(exchange.name,'Cash')
        self.assertTrue(result['journal_entry']); self.assertTrue(result['payment_entry'])
        sale.reload();bill.reload()
        self.assertEqual(sale.outstanding_amount,0);self.assertEqual(bill.outstanding_amount,0)
        self.assertEqual(frappe.db.get_value('Payment Entry',result['payment_entry'],'paid_amount'),30000)
        with self.assertRaises(frappe.ValidationError):settle_exchange(exchange.name,'Cash')

    def test_exchange_shop_pays_difference(self):
        exchange,sale,bill=self.exchange(incoming=70000)
        result=settle_exchange(exchange.name,'Cash')
        entry=frappe.get_doc('Payment Entry',result['payment_entry'])
        self.assertEqual(entry.payment_type,'Pay');self.assertEqual(entry.paid_amount,5000)
        sale.reload();bill.reload()
        self.assertEqual(sale.outstanding_amount,0);self.assertEqual(bill.outstanding_amount,0)

    def test_exchange_even_trade_needs_no_cash_entry(self):
        exchange,sale,bill=self.exchange(incoming=65000)
        result=settle_exchange(exchange.name)
        self.assertTrue(result['journal_entry']);self.assertFalse(result['payment_entry'])

    def test_device_expense_counted_once_in_economics(self):
        bill=frappe.get_doc('Purchase Invoice',device_expense(self.phone.name,self.phone.supplier,2500,'Replace charging port'));bill.submit()
        self.register(self.phone)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000)['name']);sale.submit()
        from covison_erp.mobile_shop.report.mobile_device_economics.mobile_device_economics import execute
        _,rows,_=execute()
        row=next(r for r in rows if r.name==self.phone.name)
        self.assertEqual(row.repair_expenses,2500)
        self.assertEqual(row.posted_margin,65000-self.phone.purchase_cost-2500)

    def test_return_requires_exact_phone_and_reinspection(self):
        self.register(self.phone)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000)['name']);sale.submit()
        with self.assertRaises(frappe.ValidationError):return_phone(sale.name,self.phones[1],'Same','Wrong phone')
        returned=frappe.get_doc('Sales Invoice',return_phone(sale.name,self.phone.name,'Scratched frame','Customer return'))
        returned.submit();self.phone.reload()
        self.assertEqual(self.phone.status,'Available');self.assertEqual(self.phone.inspection_status,'Pending')
        self.assertIn('Scratched frame',self.phone.inspection_notes)

    def test_daily_closing_recomputes_balances_and_requires_explanation(self):
        from covison_erp.mobile_shop.doctype.mobile_daily_closing.mobile_daily_closing import balances
        rows=balances(self.phone.company,nowdate())
        self.assertEqual(len(rows),len({r['account'] for r in rows}))
        doc=frappe.get_doc(dict(doctype='Mobile Daily Closing',company=self.phone.company,closing_date=nowdate(),
            balances=[dict(r,counted_balance=r['expected_balance'],notes='') for r in rows]))
        doc.balances[0].expected_balance=999999
        doc.insert()
        self.assertNotEqual(doc.balances[0].expected_balance,999999)
        doc.balances[0].counted_balance+=10
        doc.save()
        with self.assertRaises(frappe.ValidationError):doc.submit()
        doc.reload();doc.balances[0].counted_balance+=10;doc.balances[0].notes='Test cash discrepancy';doc.save();doc.submit()
        self.assertEqual(doc.closed_by,'Administrator')
        other=frappe.get_doc(dict(doctype='Mobile Daily Closing',company=self.phone.company,closing_date=nowdate(),
            balances=[dict(r,counted_balance=r['expected_balance']) for r in rows])).insert()
        with self.assertRaises(frappe.ValidationError):other.submit()

    def test_second_imei_pta_requires_its_own_verification(self):
        self.register(self.phone)
        self.phone.imei_2='356938035643809';self.phone.pta_status_2='Compliant'
        with self.assertRaises(frappe.ValidationError):self.phone.save()
        self.phone.reload();self.phone.imei_2='356938035643809';self.phone.pta_status_2='Compliant';self.phone.pta_verified_on_2=nowdate();self.phone.save()
        self.assertEqual(self.phone.pta_status_2,'Compliant')

    def test_salesperson_cannot_access_closing_or_exchange_settlement(self):
        exchange,_,_=self.exchange()
        user=frappe.get_doc(dict(doctype='User',email='retail-sales-test@example.invalid',first_name='Retail Test',
            send_welcome_email=0,roles=[dict(role='Mobile Salesperson'),dict(role='Sales User')])).insert()
        frappe.set_user(user.name)
        with self.assertRaises(frappe.PermissionError):settle_exchange(exchange.name,'Cash')
        self.assertFalse(frappe.has_permission('Mobile Daily Closing','create'))


    def test_intake_cancellation_reverses_stock(self):
        doc=self.intake()
        receipt=doc.purchase_receipt
        doc.reload();doc.cancel()
        self.assertEqual(frappe.db.get_value('Purchase Receipt',receipt,'docstatus'),2)
        self.assertFalse(frappe.db.get_value('Serial No',doc.mobile_device,'warehouse'))
        amended=frappe.copy_doc(doc, ignore_no_copy=False)
        amended.docstatus=0;amended.amended_from=doc.name;amended.purchase_price=36000
        amended.insert();amended.submit()
        self.assertEqual(amended.mobile_device,doc.mobile_device)
        self.assertEqual(frappe.db.get_value('Mobile Device',doc.mobile_device,'purchase_cost'),36000)

    def test_daily_closing_does_not_add_accounting_entries(self):
        from covison_erp.mobile_shop.doctype.mobile_daily_closing.mobile_daily_closing import balances
        count=frappe.db.count('GL Entry')
        rows=balances(self.phone.company,nowdate())
        doc=frappe.get_doc(dict(doctype='Mobile Daily Closing',company=self.phone.company,closing_date=nowdate(),
            balances=[dict(r,counted_balance=r['expected_balance']) for r in rows])).insert()
        doc.submit()
        self.assertEqual(frappe.db.count('GL Entry'),count)

    def test_warranty_resolution_and_wrong_return_are_rejected(self):
        self.register(self.phone)
        sale=frappe.get_doc('Sales Invoice',create_sale(self.phone.name,self.customer(),65000)['name']);sale.submit()
        claim=frappe.get_doc(dict(doctype='Warranty Claim',company=self.phone.company,customer=sale.customer,
            serial_no=self.phone.name,covison_device=self.phone.name,covison_sales_invoice=sale.name,
            complaint='Rollback warranty check',status='Open')).insert()
        claim.status='Closed'
        with self.assertRaises(frappe.ValidationError):claim.save()
        claim.reload();claim.status='Closed';claim.covison_resolution='Checked and returned to customer';claim.save()

    def test_cashier_can_checkout_and_close_but_cannot_see_device_cost(self):
        self.register(self.phone)
        sale=create_sale(self.phone.name,self.customer(),65000)['name']
        user=frappe.get_doc(dict(doctype='User',email='retail-cash-test@example.invalid',first_name='Cash Test',
            send_welcome_email=0,roles=[dict(role='Mobile Cashier'),dict(role='Sales User'),dict(role='Accounts User')])).insert()
        frappe.set_user(user.name)
        checkout(sale,[dict(mode_of_payment='Cash',amount=65000)])
        self.assertEqual(frappe.db.get_value('Sales Invoice',sale,'outstanding_amount'),0)
        self.assertFalse(dashboard()['can_see_costs'])
        self.assertTrue(frappe.has_permission('Mobile Daily Closing','create'))
        self.assertFalse(frappe.has_permission('Serial and Batch Bundle','read'))
        self.assertTrue(frappe.has_permission('Serial and Batch Bundle','create'))
        from covison_erp.devices import validate_counter_bundle
        with self.assertRaises(frappe.PermissionError):
            validate_counter_bundle(frappe._dict(voucher_type='Sales Invoice',voucher_no=sale))

    def test_purchaser_can_receive_and_bill_phone(self):
        user=frappe.get_doc(dict(doctype='User',email='retail-buy-test@example.invalid',first_name='Buyer Test',
            send_welcome_email=0,roles=[dict(role='Mobile Purchaser'),dict(role='Purchase User'),dict(role='Stock User')])).insert()
        frappe.set_user(user.name)
        doc=self.intake()
        self.assertTrue(purchase_bill(doc.purchase_receipt))

    def test_capitalized_stock_cannot_be_counted_again_as_repair_expense(self):
        doc=frappe.get_doc(dict(doctype='Purchase Invoice',company=self.phone.company,supplier=self.phone.supplier,
            currency='PKR',conversion_rate=1,items=[dict(item_code=self.phone.item_code,qty=1,rate=1000,
            covison_device=self.phone.name)]))
        with self.assertRaises(frappe.ValidationError):doc.insert()

    def test_reversed_exchange_is_marked_for_review(self):
        exchange,sale,bill=self.exchange()
        result=settle_exchange(exchange.name,'Cash')
        payment=frappe.get_doc('Payment Entry',result['payment_entry']);payment.cancel()
        self.assertEqual(frappe.db.get_value('Mobile Exchange',exchange.name,'settlement_status'),'Reversed / Review')

result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RetailWorkflowChecks))
if not result.wasSuccessful():raise AssertionError('Retail workflow checks failed')
