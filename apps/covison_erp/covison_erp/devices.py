"""Keep phone profiles attached to native serial-number inventory and accounting."""

import frappe
from frappe.utils import add_days, cint, flt, nowdate
from covison_erp.identifiers import valid_imei


MANAGER_ROLES = {"System Manager", "Stock Manager", "Mobile Shop Manager", "Purchase Manager"}
COST_ROLES = MANAGER_ROLES | {"Mobile Purchaser"}


def can_see_costs():
    return frappe.session.user == "Administrator" or bool(COST_ROLES.intersection(frappe.get_roles()))


def is_manager():
    return frappe.session.user == "Administrator" or bool(MANAGER_ROLES.intersection(frappe.get_roles()))


def validate_item(doc, method=None):
    if doc.variant_of:
        template = frappe.get_cached_doc("Item", doc.variant_of)
        if template.get("covison_is_mobile"):
            doc.covison_is_mobile = 1
            doc.covison_model = template.covison_model
            attributes = {str(a.attribute).lower(): a.attribute_value for a in doc.attributes}
            if attributes.get("storage"):
                doc.covison_storage = attributes["storage"].replace(" ", "").upper()
            doc.covison_color = attributes.get("color") or attributes.get("colour") or doc.covison_color
            doc.covison_ram = attributes.get("ram") or doc.covison_ram
            if doc.is_new():
                doc.has_serial_no = 1
                doc.use_serial_no_wise_valuation = 1
                doc.serial_no_series = "COV-PHN-.######"
    if doc.get("covison_is_mobile") and not doc.has_variants:
        if not doc.is_stock_item or not doc.has_serial_no or not doc.use_serial_no_wise_valuation:
            frappe.throw("Mobile phone variants require stock tracking, serial numbers and Serial No Wise Valuation.")
        if not doc.covison_model or not doc.covison_storage:
            frappe.throw("Select the phone model and storage capacity for this mobile variant.")


def serials_for_row(row):
    if row.get("serial_and_batch_bundle"):
        return frappe.get_all("Serial and Batch Entry", filters={"parent": row.serial_and_batch_bundle},
                              pluck="serial_no")
    return [s.strip() for s in (row.get("serial_no") or "").splitlines() if s.strip()]


def pending_pos_sale(serial):
    rows = frappe.db.sql("""select p.name, i.qty from `tabPOS Invoice` p
        join `tabPOS Invoice Item` i on i.parent=p.name
        where p.docstatus=1 and p.is_return=0 and i.covison_device=%s
        and coalesce(p.consolidated_invoice, '')=''""", (serial,), as_dict=True)
    for sale in rows:
        returned = frappe.db.sql("""select coalesce(sum(abs(i.qty)),0) from `tabPOS Invoice` p
            join `tabPOS Invoice Item` i on i.parent=p.name
            where p.docstatus=1 and p.is_return=1 and p.return_against=%s and i.covison_device=%s""",
            (sale.name, serial))[0][0]
        if flt(returned) < flt(sale.qty):
            return sale.name


def serial_cost(serial):
    rates = frappe.get_all("Serial and Batch Entry", filters={"serial_no": serial,
        "docstatus": 1, "is_cancelled": 0, "type_of_transaction": "Inward", "qty": [">", 0]},
        fields=["incoming_rate"], order_by="posting_datetime desc, creation desc", limit_page_length=1)
    return flt(rates[0].incoming_rate) if rates else flt(frappe.db.get_value("Serial No", serial, "purchase_rate"))


def validate_device(doc, method=None):
    serial = frappe.get_doc("Serial No", doc.serial_no)
    if not frappe.get_cached_value("Item", serial.item_code, "covison_is_mobile"):
        frappe.throw("Select a serial number belonging to a mobile phone product.")
    item = frappe.get_cached_doc("Item", serial.item_code)
    doc.item_code = item.name
    doc.device_name = item.item_name
    doc.model = item.get("covison_model")
    doc.storage = item.get("covison_storage")
    doc.color = item.get("covison_color")
    doc.company = serial.company
    doc.currency = frappe.get_cached_value("Company", serial.company, "default_currency") or "PKR"
    doc.warehouse = serial.warehouse
    pending = pending_pos_sale(doc.serial_no) if serial.warehouse else None
    doc.status = ("Reserved" if pending else "Available") if serial.warehouse else "Sold" if serial.customer else "Unavailable"
    doc.pos_invoice = pending
    doc.purchase_cost = serial_cost(serial.name)
    doc.customer = serial.customer
    for key in ("imei_1", "imei_2"):
        value = (doc.get(key) or "").strip()
        doc.set(key, value or None)
        if value and not valid_imei(value):
            frappe.throw(f"{doc.meta.get_label(key)} must be a valid 15-digit IMEI, including its check digit.")
    if doc.imei_1 and doc.imei_1 == doc.imei_2:
        frappe.throw("IMEI 1 and IMEI 2 must be different.")
    if doc.imei_2 and not doc.imei_1:
        frappe.throw("Enter IMEI 1 before IMEI 2.")
    if doc.battery_health_checked and not 0 <= flt(doc.battery_health) <= 100:
        frappe.throw("Battery health must be between 0 and 100.")
    if not doc.battery_health_checked:
        doc.battery_health = 0
    for key in ("asking_price", "minimum_price", "wholesale_price", "warranty_days"):
        if flt(doc.get(key)) < 0:
            frappe.throw(f"{doc.meta.get_label(key)} cannot be negative.")
    if doc.asking_price and flt(doc.asking_price) < flt(doc.minimum_price):
        frappe.throw("Asking price cannot be below the approved minimum selling price.")
    previous = doc.get_doc_before_save()
    if previous:
        for suffix, imei_field, status_field, date_field, evidence_field in (
            ("1", "imei_1", "pta_status", "pta_verified_on", "pta_evidence"),
            ("2", "imei_2", "pta_status_2", "pta_verified_on_2", "pta_evidence_2")):
            if previous.get(imei_field) and previous.get(imei_field) != doc.get(imei_field):
                doc.set(status_field, "Not Checked")
                doc.set(date_field, None)
                doc.set(evidence_field, None)
                if imei_field == "imei_1":
                    doc.pta_verified_by = None
    if doc.pta_status not in (None, "Not Checked"):
        if not doc.pta_verified_on:
            frappe.throw("Record the verification date when entering a PTA result.")
        if not doc.pta_verified_by:
            doc.pta_verified_by = frappe.session.user
    if doc.get("pta_status_2") not in (None, "Not Checked"):
        if not doc.imei_2 or not doc.get("pta_verified_on_2"):
            frappe.throw("Record IMEI 2 and its verification date before entering its PTA result.")
    if doc.inspection_status == "Passed" and not doc.inspection_date:
        doc.inspection_date = nowdate()
    # A unique central index also prevents an IMEI appearing as slot 1 on one phone and
    # slot 2 on another. The primary key handles concurrent inserts atomically.
    identifiers = [v for v in (doc.imei_1, doc.imei_2) if v]
    for value in identifiers:
        owner = frappe.db.get_value("Mobile IMEI", value, "mobile_device")
        if owner and owner != doc.name:
            frappe.throw(f"IMEI {value} is already registered to device {owner}.")
        if not owner:
            frappe.get_doc({"doctype": "Mobile IMEI", "imei": value,
                            "mobile_device": doc.name}).insert(ignore_permissions=True, ignore_links=True)
    old = frappe.get_all("Mobile IMEI", filters={"mobile_device": doc.name}, pluck="name")
    for value in set(old) - set(identifiers):
        frappe.db.delete("Mobile IMEI", {"name": value, "mobile_device": doc.name})


def refresh_device(serial_name, transaction=None, row=None):
    if not frappe.db.exists("Serial No", serial_name):
        return
    serial = frappe.get_doc("Serial No", serial_name)
    if not frappe.get_cached_value("Item", serial.item_code, "covison_is_mobile"):
        return
    exists = frappe.db.exists("Mobile Device", {"serial_no": serial_name})
    doc = frappe.get_doc("Mobile Device", exists) if exists else frappe.new_doc("Mobile Device")
    doc.serial_no = serial_name
    if transaction and transaction.get("covison_demo_batch") and not doc.get("covison_demo_batch"):
        doc.covison_demo_batch = transaction.covison_demo_batch
    if transaction and transaction.doctype == "Purchase Receipt" and transaction.docstatus == 1 and not transaction.is_return:
        doc.supplier = transaction.supplier
        doc.purchase_receipt = transaction.name
    if transaction and transaction.doctype in ("Sales Invoice", "POS Invoice"):
        if transaction.docstatus == 1 and not transaction.is_return:
            doc.sales_invoice = transaction.name if transaction.doctype == "Sales Invoice" else None
            doc.sold_price = flt(row.base_net_rate)
            doc.sold_on = transaction.posting_date
            frappe.db.set_value("Serial No", serial_name, {"warranty_period": cint(doc.warranty_days),
                "warranty_expiry_date": add_days(transaction.posting_date, cint(doc.warranty_days))
                if cint(doc.warranty_days) else None}, update_modified=False)
        elif transaction.docstatus == 2 or transaction.is_return:
            if serial.warehouse:
                if transaction.is_return and transaction.docstatus == 1:
                    doc.inspection_status = "Pending"
                    doc.inspection_notes = "Returned: " + (transaction.get("covison_return_condition") or "Review condition")
                doc.sales_invoice = None
                doc.sold_price = 0
                doc.sold_on = None
    doc.save(ignore_permissions=True)
    return doc


def sync_transaction(doc, method=None):
    for row in doc.get("items", []):
        for serial in serials_for_row(row):
            refresh_device(serial, doc, row)
    if doc.doctype in ("Sales Invoice", "POS Invoice", "Delivery Note"):
        frappe.local.covison_posting_sale = None


def validate_sale(doc, method=None):
    seen = set()
    for row in doc.get("items", []):
        if not row.item_code or not frappe.get_cached_value("Item", row.item_code, "covison_is_mobile"):
            continue
        serials = serials_for_row(row)
        if row.get("covison_device") and not serials:
            serials = [row.covison_device]
            row.serial_no = row.covison_device
            row.use_serial_batch_fields = 1
        if len(serials) != 1 or abs(flt(row.qty)) != 1:
            frappe.throw(f"Row {row.idx}: select one exact phone / serial number per invoice row.")
        serial = serials[0]
        if serial in seen:
            frappe.throw("The same phone cannot appear twice on a sale.")
        seen.add(serial)
        if not frappe.db.exists("Mobile Device", serial):
            frappe.throw(f"Register device details for {serial} before selling it.")
        device = frappe.get_doc("Mobile Device", serial)
        device.check_permission("read")
        if device.item_code != row.item_code or (row.get("covison_device") and row.covison_device != serial):
            frappe.throw("Selected phone does not match the product variant or serial number.")
        if not doc.get("is_return"):
            pending = pending_pos_sale(serial)
            if pending and pending not in (doc.name, row.get("pos_invoice")):
                frappe.throw(f"Phone {serial} is already sold on POS invoice {pending}, pending shift closing.")
            if not device.imei_1:
                frappe.throw(f"Enter the actual IMEI for {serial} before selling it.")
            if doc.doctype in ("Sales Invoice", "POS Invoice") and not doc.update_stock and not row.get("delivery_note"):
                frappe.throw("Enable Update Stock, or link the original delivery note, when invoicing a phone.")
            if not doc.get("delivery_note") and not row.get("delivery_note"):
                warehouse = frappe.db.get_value("Serial No", serial, "warehouse")
                if warehouse != row.warehouse:
                    frappe.throw(f"Phone {serial} is no longer available in the selected warehouse.")
            effective_rate = row.get("base_net_rate")
            if effective_rate is None:
                effective_rate = row.rate
            if flt(effective_rate) < flt(device.minimum_price) and not is_manager():
                frappe.throw(f"The price for {serial} is below its approved minimum. A manager must approve this sale.")
        row.covison_device = serial
        snapshot_fields = ("covison_imei", "covison_imei_2", "covison_condition", "covison_pta_status",
            "covison_battery", "covison_warranty_days", "covison_pta_status_2", "covison_disclosed_faults", "covison_warranty_terms")
        original_row = None
        if doc.doctype == "Sales Invoice" and doc.get("is_consolidated") and row.get("pos_invoice"):
            original = frappe.get_doc("POS Invoice", row.pos_invoice)
            original.check_permission("read")
            original_row = next((r for r in original.items if r.get("covison_device") == serial
                                 and r.name == row.get("pos_invoice_item")), None)
            if original_row is None or original.docstatus != 1:
                frappe.throw("Consolidation must reference the exact posted POS phone row.")
        elif doc.get("is_return") and doc.get("return_against"):
            original = frappe.get_doc(doc.doctype, doc.return_against)
            original.check_permission("read")
            original_row = next((r for r in original.items if r.get("covison_device") == serial), None)
            if original_row is None:
                frappe.throw("Return the exact phone from the original invoice.")
        if original_row is not None:
            # Returns and POS closing retain the terms agreed at the original sale.
            for field in snapshot_fields:
                row.set(field, original_row.get(field))
        else:
            row.covison_imei = device.imei_1
            row.covison_imei_2 = device.imei_2
            row.covison_condition = device.condition_grade
            row.covison_pta_status = device.pta_status
            row.covison_battery = f"{flt(device.battery_health):g}%" if device.battery_health_checked else "Not recorded"
            row.covison_warranty_days = cint(device.warranty_days)
            row.covison_pta_status_2 = device.get("pta_status_2") if device.imei_2 else ""
            row.covison_disclosed_faults = device.get("disclosed_faults")
            row.covison_warranty_terms = device.get("warranty_terms")


def lock_sale_devices(doc, method=None):
    for row in doc.get("items", []):
        for serial in serials_for_row(row):
            frappe.db.sql("select name from `tabSerial No` where name=%s for update", (serial,))
    validate_sale(doc)
    frappe.local.covison_posting_sale = (doc.doctype, doc.name)


def validate_counter_bundle(doc, method=None):
    """Counter-role bundle writes are allowed only within an authorized sale post.

    Native stock/purchasing roles retain their existing ERP permissions. This keeps
    a direct bundle create request from exposing valuation to counter-only roles.
    """
    roles = set(frappe.get_roles())
    if frappe.session.user == 'Administrator' or roles.intersection({
        'System Manager', 'Stock User', 'Stock Manager', 'Purchase User', 'Purchase Manager',
        'Delivery User', 'Delivery Manager', 'Manufacturing User', 'Manufacturing Manager'}):
        return
    if roles.intersection({'Mobile Salesperson', 'Mobile Cashier'}):
        context = getattr(frappe.local, 'covison_posting_sale', None)
        if context != (doc.voucher_type, doc.voucher_no):
            frappe.throw('Serial bundles for counter staff are created only while posting an authorized sale or return.', frappe.PermissionError)
