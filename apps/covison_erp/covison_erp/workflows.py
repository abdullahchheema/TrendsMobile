"""Retail workflows using native ERP stock, payable and receivable documents.

No endpoint commits: a failed action rolls back the entire request. Permissions are
checked on every linked document and every document created or submitted.
"""
import frappe
from frappe.utils import cint, flt, getdate, nowdate, now_datetime
from covison_erp.devices import can_see_costs, is_manager


def lock_doc(doctype, name):
    # Only internal callers choose a DocType; callers cannot supply a SQL identifier.
    frappe.db.sql(f'select name from `tab{doctype}` where name=%s for update', (name,))
    doc = frappe.get_doc(doctype, name)
    doc.check_permission('read')
    return doc


def pkr_company(company):
    doc = frappe.get_doc('Company', company)
    doc.check_permission('read')
    if doc.default_currency != 'PKR':
        frappe.throw('This retail workflow requires a PKR company.')
    return doc


def payment_account(company, mode):
    method = frappe.get_doc('Mode of Payment', mode)
    method.check_permission('read')
    if not method.enabled:
        frappe.throw('This payment method is disabled.')
    account = next((r.default_account for r in method.accounts if r.company == company), None)
    if not account:
        frappe.throw(f'Configure the {mode} account for {company}.')
    details = frappe.db.get_value('Account', account, ['company', 'account_currency', 'account_type', 'is_group', 'disabled'], as_dict=True)
    if details.company != company or details.account_currency != 'PKR' or details.is_group or details.disabled or details.account_type not in ('Cash', 'Bank'):
        frappe.throw('Select an enabled PKR cash or bank account for this payment method.')
    return account, method.type


def post_payment(invoice, mode, amount, reference=None, reference_date=None, handled_by=None):
    from erpnext.accounts.doctype.payment_entry.payment_entry import get_payment_entry
    amount = flt(amount, 2)
    if amount <= 0:
        frappe.throw('Payment amount must be positive.')
    account, kind = payment_account(invoice.company, mode)
    if kind != 'Cash' and not (reference or '').strip():
        frappe.throw(f'Enter the actual transfer / transaction reference for {mode}.')
    invoice.reload()
    if amount > abs(flt(invoice.outstanding_amount)) + 0.005:
        frappe.throw('Payment exceeds the invoice balance. Record deposits separately as advances.')
    entry = get_payment_entry(invoice.doctype, invoice.name, bank_account=account)
    entry.covison_handled_by = handled_by
    entry.mode_of_payment = mode
    entry.paid_amount = amount
    entry.received_amount = amount
    entry.set('deductions', [])
    entry.set('references', [dict(reference_doctype=invoice.doctype, reference_name=invoice.name,
        total_amount=invoice.rounded_total or invoice.grand_total,
        outstanding_amount=invoice.outstanding_amount, allocated_amount=amount)])
    entry.reference_no = (reference or '').strip() or None
    entry.reference_date = reference_date or nowdate()
    entry.insert()
    entry.submit()
    return entry


@frappe.whitelist(methods=['POST'])
def checkout(invoice, payments=None, sold_by=None, collected_by=None):
    """Post a reviewed draft sale and split receipts, or leave a credit balance."""
    try:
        return _checkout(invoice, payments, sold_by, collected_by)
    except Exception as error:
        if isinstance(error, (frappe.QueryDeadlockError, frappe.QueryTimeoutError)) or (
            getattr(error, "args", ()) and error.args[0] in (1020, 1205, 1213)
        ):
            frappe.throw("This phone or invoice was updated at another till. Refresh the sale and check its status before trying again.")
        raise


def _checkout(invoice, payments=None, sold_by=None, collected_by=None):
    doc = lock_doc('Sales Invoice', invoice)
    doc.check_permission('submit')
    if doc.docstatus == 1 and doc.get('covison_checkout_done'):
        return {'name': doc.name, 'payments': frappe.get_all('Payment Entry Reference',
                filters={'reference_doctype': 'Sales Invoice', 'reference_name': doc.name, 'docstatus': 1}, pluck='parent')}
    if doc.docstatus != 0 or doc.is_return or doc.is_pos or not any(r.get('covison_device') for r in doc.items):
        frappe.throw('Select a draft, non-POS phone sales invoice for counter checkout.')
    amounts = frappe.parse_json(payments) if isinstance(payments, str) else payments or []
    if not isinstance(amounts, list):
        frappe.throw('Provide a list of payments.')
    if sum(flt(r.get('amount'), 2) for r in amounts) > flt(doc.rounded_total or doc.grand_total) + 0.005:
        frappe.throw('Payments exceed the invoice total. Reduce the amount or record an advance separately.')
    for row in amounts:
        if flt(row.get('amount'), 2) <= 0:
            frappe.throw('Each payment must be positive; remove unused rows.')
        _, kind = payment_account(doc.company, row.get('mode_of_payment'))
        if kind != 'Cash' and not row.get('reference'):
            frappe.throw('Enter a transaction reference for each non-cash payment.')
    if sold_by: doc.covison_sold_by = sold_by
    if collected_by: doc.covison_collected_by = collected_by
    doc.covison_checkout_done = 1
    doc.save()
    doc.submit()
    entries = [post_payment(doc, r['mode_of_payment'], r['amount'], r.get('reference'), r.get('reference_date'), handled_by=collected_by or doc.get('covison_collected_by')).name for r in amounts]
    return {'name': doc.name, 'payments': entries}


@frappe.whitelist(methods=['POST'])
def purchase_bill(receipt):
    from erpnext.stock.doctype.purchase_receipt.purchase_receipt import make_purchase_invoice
    doc = lock_doc('Purchase Receipt', receipt)
    if doc.docstatus != 1 or doc.is_return:
        frappe.throw('Receive the incoming phone before creating its supplier bill.')
    existing = frappe.get_all('Purchase Invoice Item', filters={'purchase_receipt': receipt, 'docstatus': ['!=', 2]}, pluck='parent')
    if existing:
        bill = frappe.get_doc('Purchase Invoice', existing[0]); bill.check_permission('read')
        return bill.name
    bill = make_purchase_invoice(receipt)
    bill.covison_purchased_by = doc.get("covison_purchased_by")
    bill.insert()
    return bill.name


@frappe.whitelist(methods=['POST'])
def settle_exchange(exchange, mode_of_payment=None, reference=None, handled_by=None):
    doc = lock_doc('Mobile Exchange', exchange)
    doc.check_permission('write')
    if doc.journal_entry or doc.payment_entry:
        frappe.throw('This exchange already has settlement records. Review those records before making another payment.')
    doc.validate()
    if not doc.purchase_invoice:
        frappe.throw('Select and submit the incoming supplier bill first.')
    sale = lock_doc('Sales Invoice', doc.sales_invoice)
    purchase = lock_doc('Purchase Invoice', doc.purchase_invoice)
    if sale.docstatus != 1 or purchase.docstatus != 1:
        frappe.throw('Submit both invoices after reviewing them, then settle the exchange.')
    pkr_company(doc.company)
    if sale.currency != 'PKR' or purchase.currency != 'PKR' or flt(sale.conversion_rate) != 1 or flt(purchase.conversion_rate) != 1:
        frappe.throw('Exchange settlement requires PKR invoices at conversion rate 1.')
    outgoing, incoming = flt(sale.outstanding_amount, 2), flt(purchase.outstanding_amount, 2)
    if outgoing < 0 or incoming < 0 or not (outgoing or incoming):
        frappe.throw('The invoices are already settled or require a credit/refund review.')
    difference = flt(outgoing - incoming, 2)
    if difference and not mode_of_payment:
        frappe.throw('Select the actual payment method for the remaining difference.')
    if difference:
        _, kind = payment_account(doc.company, mode_of_payment)
        if kind != 'Cash' and not (reference or '').strip():
            frappe.throw('Enter the actual transaction reference for the difference payment.')
    offset = min(outgoing, incoming)
    if offset:
        journal = frappe.get_doc(dict(doctype='Journal Entry', company=doc.company, posting_date=nowdate(),
            voucher_type='Journal Entry', user_remark=f'Phone exchange {doc.name}: offset purchase against sale',
            accounts=[dict(account=purchase.credit_to, party_type='Supplier', party=purchase.supplier,
                           debit_in_account_currency=offset, reference_type='Purchase Invoice', reference_name=purchase.name),
                      dict(account=sale.debit_to, party_type='Customer', party=sale.customer,
                           credit_in_account_currency=offset, reference_type='Sales Invoice', reference_name=sale.name)]))
        journal.insert(); journal.submit()
        doc.journal_entry = journal.name
    if difference:
        payment = post_payment(sale if difference > 0 else purchase, mode_of_payment, abs(difference), reference, handled_by=handled_by)
        doc.payment_entry = payment.name
    sale.reload(); purchase.reload()
    if abs(flt(sale.outstanding_amount)) > 0.005 or abs(flt(purchase.outstanding_amount)) > 0.005:
        frappe.throw('The balances did not fully settle. No changes have been posted; review the invoices.')
    doc.flags.settling_exchange = True
    doc.settlement_status = 'Settled'
    doc.save()
    return {'journal_entry': doc.journal_entry, 'payment_entry': doc.payment_entry}


@frappe.whitelist(methods=['POST'])
def return_phone(invoice, device, condition, reason):
    from erpnext.controllers.sales_and_purchase_return import make_return_doc
    original = lock_doc('Sales Invoice', invoice)
    if original.docstatus != 1 or original.is_return:
        frappe.throw('Select the original submitted sale.')
    if not any(r.get('covison_device') == device for r in original.items):
        frappe.throw('This phone does not belong to the selected sale.')
    if not (condition or '').strip() or not (reason or '').strip():
        frappe.throw('Record the returned condition and reason.')
    doc = make_return_doc('Sales Invoice', original.name)
    doc.set('items', [r for r in doc.items if r.get('covison_device') == device])
    # A partial phone return must not copy a whole invoice's POS payment rows.
    doc.is_pos = 0; doc.set('payments', [])
    doc.paid_amount = 0; doc.base_paid_amount = 0
    doc.covison_return_condition = condition
    doc.covison_return_reason = reason
    doc.covison_checkout_done = 0
    doc.insert()
    return doc.name


def validate_return(doc, method=None):
    if doc.is_return and any(r.get('covison_device') for r in doc.items):
        if not doc.return_against:
            frappe.throw('Phone returns must reference the original sale.')
        if not doc.get('covison_return_condition') or not doc.get('covison_return_reason'):
            frappe.throw('Record the returned phone condition and return reason.')
        original = frappe.get_doc(doc.doctype, doc.return_against)
        original.check_permission('read')
        sold = {r.get('covison_device') for r in original.items}
        if any(r.get('covison_device') and r.covison_device not in sold for r in doc.items):
            frappe.throw('Return the exact phone from the original invoice.')


def receipt_snapshot(doc, method=None):
    if doc.is_new() and not doc.get('amended_from') and not doc.get('is_return'):
        settings = frappe.get_single('Mobile Shop Settings')
        doc.covison_shop_phone = settings.shop_phone
        doc.covison_shop_address = settings.shop_address
        doc.covison_receipt_terms = settings.receipt_terms
        doc.covison_urdu_note = settings.urdu_receipt_note


def validate_expenses(doc, method=None):
    for row in doc.items:
        if not row.get('covison_device'):
            continue
        phone = frappe.get_doc('Mobile Device', row.covison_device)
        phone.check_permission('read')
        account = frappe.get_cached_doc('Account', row.expense_account)
        if (phone.company != doc.company or doc.currency != 'PKR' or flt(doc.conversion_rate) != 1
            or frappe.get_cached_value('Item', row.item_code, 'is_stock_item')
            or row.get('purchase_receipt') or doc.update_stock or account.root_type != 'Expense'):
            frappe.throw('Device expenses must be PKR non-stock service bills posted to an Expense account. Capitalized stock costs already belong in device valuation.')


@frappe.whitelist(methods=['POST'])
def device_expense(device, supplier, amount, description):
    if not can_see_costs():
        frappe.throw('Only purchasing staff or managers can record device costs.', frappe.PermissionError)
    phone = frappe.get_doc('Mobile Device', device); phone.check_permission('read')
    company = pkr_company(phone.company)
    if flt(amount) <= 0 or not (description or '').strip():
        frappe.throw('Enter a positive cost and a description of the repair or service.')
    frappe.get_doc('Supplier', supplier).check_permission('read')
    doc = frappe.get_doc(dict(doctype='Purchase Invoice', company=phone.company, supplier=supplier,
        currency='PKR', conversion_rate=1, update_stock=0, taxes_and_charges='', taxes=[],
        items=[dict(item_code='COV-DEVICE-SERVICE', qty=1, rate=flt(amount), description=description,
                    expense_account=company.default_expense_account, cost_center=company.cost_center,
                    covison_device=device)]))
    doc.insert()
    return doc.name


def validate_warranty(doc, method=None):
    if not doc.get('covison_device'):
        return
    phone = frappe.get_doc('Mobile Device', doc.covison_device); phone.check_permission('read')
    if doc.serial_no != phone.serial_no:
        frappe.throw('The warranty serial must match the selected phone.')
    if doc.get('covison_sales_invoice'):
        invoice = frappe.get_doc('Sales Invoice', doc.covison_sales_invoice); invoice.check_permission('read')
        if invoice.docstatus != 1 or invoice.is_return or invoice.customer != doc.customer or not any(r.get('covison_device') == phone.name for r in invoice.items):
            frappe.throw('Select the original posted sale for this customer and phone.')
    if doc.get('covison_return_invoice'):
        returned = frappe.get_doc('Sales Invoice', doc.covison_return_invoice); returned.check_permission('read')
        if returned.docstatus != 1 or not returned.is_return or returned.return_against != doc.get('covison_sales_invoice') or not any(r.get('covison_device') == phone.name for r in returned.items):
            frappe.throw('Select the submitted return for this warranty sale and phone.')
    if doc.get('covison_replacement_invoice'):
        replacement = frappe.get_doc('Sales Invoice', doc.covison_replacement_invoice); replacement.check_permission('read')
        if replacement.docstatus != 1 or replacement.is_return or replacement.customer != doc.customer or not any(r.get('covison_device') and r.covison_device != phone.name for r in replacement.items):
            frappe.throw('Select a submitted replacement sale to the same customer.')
    if doc.status == 'Closed' and not doc.get('covison_resolution'):
        frappe.throw('Record how the warranty claim was resolved before closing it.')


def receipt_payments(doc):
    """Receipt-safe payment details for this invoice only, including split receipts."""
    if isinstance(doc, dict):
        doc = frappe.get_doc(doc)
    doc.check_permission('read')
    if doc.doctype not in ('Sales Invoice', 'POS Invoice'):
        return []
    result = [dict(mode_of_payment=r.mode_of_payment, amount=r.amount, reference='')
              for r in doc.get('payments', []) if flt(r.amount)]
    if doc.docstatus == 1 and doc.doctype == 'Sales Invoice':
        rows = frappe.db.sql("""select p.mode_of_payment, r.allocated_amount as amount,
                p.reference_no as reference, p.payment_type
            from `tabPayment Entry Reference` r join `tabPayment Entry` p on p.name=r.parent
            where p.docstatus=1 and r.reference_doctype='Sales Invoice' and r.reference_name=%s""",
            (doc.name,), as_dict=True)
        for row in rows:
            if row.payment_type == 'Pay': row.amount = -abs(flt(row.amount))
            result.append(row)
    return result


def settlement_cancelled(doc, method=None):
    field = 'journal_entry' if doc.doctype == 'Journal Entry' else 'payment_entry'
    for name in frappe.get_all('Mobile Exchange', filters={field: doc.name}, pluck='name'):
        frappe.db.set_value('Mobile Exchange', name, 'settlement_status', 'Reversed / Review')
