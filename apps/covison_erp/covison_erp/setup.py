"""Repeatable site configuration. Existing transactions are never changed here."""

import json
from pathlib import Path

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.custom.doctype.property_setter.property_setter import make_property_setter


def before_install():
    for role in ("Mobile Shop Manager", "Mobile Salesperson", "Mobile Purchaser", "Mobile Cashier", "Mobile Technician"):
        if not frappe.db.exists("Role", role):
            frappe.get_doc({"doctype": "Role", "role_name": role, "desk_access": 1}).insert(ignore_permissions=True)


def install():
    before_install()
    assets()
    custom_fields()
    from covison_erp.staff import setup as setup_staff
    setup_staff()
    from covison_erp.demo import setup as setup_demo
    setup_demo()
    branding()
    stock_settings()
    regional_defaults()
    catalog()
    payments()
    receipt_formats()
    role_profiles()
    native_permissions()
    retail_access()
    navigation()
    frappe.clear_cache()


def assets():
    target = Path(frappe.get_site_path("..", "assets", "covison_erp"))
    if not target.exists() and not target.is_symlink():
        target.symlink_to(Path(__file__).parent / "public", target_is_directory=True)


def custom_fields():
    invoice_fields = [
        {"fieldname": "covison_device", "label": "Mobile Device", "fieldtype": "Link", "options": "Mobile Device",
         "insert_after": "item_code", "in_list_view": 1},
        {"fieldname": "covison_imei", "label": "IMEI 1", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "covison_imei_2", "label": "IMEI 2", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "covison_condition", "label": "Phone Condition", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "covison_pta_status", "label": "PTA Status", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "covison_battery", "label": "Battery Health", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "covison_warranty_days", "label": "Shop Warranty Days", "fieldtype": "Int", "read_only": 1},
    ]
    create_custom_fields({
        "Item": [
            {"fieldname": "covison_mobile_section", "label": "Mobile Phone Details", "fieldtype": "Section Break", "insert_after": "brand"},
            {"fieldname": "covison_is_mobile", "label": "Is a Mobile Phone", "fieldtype": "Check", "insert_after": "covison_mobile_section"},
            {"fieldname": "covison_model", "label": "Phone Model", "fieldtype": "Link", "options": "Mobile Model", "insert_after": "covison_is_mobile"},
            {"fieldname": "covison_storage", "label": "Storage", "fieldtype": "Select", "options": "\n16GB\n32GB\n64GB\n128GB\n256GB\n512GB\n1TB\n2TB", "insert_after": "covison_model"},
            {"fieldname": "covison_ram", "label": "RAM", "fieldtype": "Data", "insert_after": "covison_storage"},
            {"fieldname": "covison_color", "label": "Color", "fieldtype": "Data", "insert_after": "covison_ram"},
        ],
        "Sales Invoice Item": invoice_fields,
        "POS Invoice Item": invoice_fields,
        "Delivery Note Item": invoice_fields,
        "Purchase Receipt": [{"fieldname": "covison_purchase_source", "label": "Phone Purchase Source", "fieldtype": "Select",
                              "options": "\nDistributor\nDealer\nIndividual Seller\nCustomer Trade-In", "insert_after": "supplier"}],
        "Warranty Claim": [{"fieldname": "covison_device", "label": "Mobile Device", "fieldtype": "Link", "options": "Mobile Device", "insert_after": "serial_no"}],
    }, update=True)
    details = [
        {"fieldname": "covison_pta_status_2", "label": "PTA Status IMEI 2", "fieldtype": "Data", "read_only": 1},
        {"fieldname": "covison_disclosed_faults", "label": "Disclosed Faults", "fieldtype": "Small Text", "read_only": 1},
        {"fieldname": "covison_warranty_terms", "label": "Device Warranty Terms", "fieldtype": "Small Text", "read_only": 1},
    ]
    headers = [
        {"fieldname": "covison_shop_phone", "label": "Shop Contact", "fieldtype": "Data"},
        {"fieldname": "covison_shop_address", "label": "Shop Address", "fieldtype": "Small Text"},
        {"fieldname": "covison_receipt_terms", "label": "Checking / Receipt Terms", "fieldtype": "Small Text"},
        {"fieldname": "covison_urdu_note", "label": "Urdu Receipt Note", "fieldtype": "Small Text"},
        {"fieldname": "covison_return_condition", "label": "Returned Phone Condition", "fieldtype": "Small Text", "depends_on": "eval:doc.is_return"},
        {"fieldname": "covison_return_reason", "label": "Return Reason", "fieldtype": "Small Text", "depends_on": "eval:doc.is_return"},
        {"fieldname": "covison_checkout_done", "label": "Counter Checkout Posted", "fieldtype": "Check", "read_only": 1, "hidden": 1, "no_copy": 1},
    ]
    create_custom_fields({
        "Sales Invoice Item": details, "POS Invoice Item": details, "Delivery Note Item": details,
        "Sales Invoice": headers, "POS Invoice": headers,
        "Purchase Receipt": [{"fieldname": "covison_trade_in_customer", "label": "Trade-in Customer", "fieldtype": "Link", "options": "Customer", "insert_after": "covison_purchase_source"}],
        "Purchase Invoice Item": [{"fieldname": "covison_device", "label": "Device Expense For", "fieldtype": "Link", "options": "Mobile Device", "description": "Only non-stock service expenses. Stock valuation already includes capitalized costs."}],
        "Warranty Claim": [
            {"fieldname": "covison_sales_invoice", "label": "Original Phone Sale", "fieldtype": "Link", "options": "Sales Invoice"},
            {"fieldname": "covison_received_condition", "label": "Received Condition / Accessories", "fieldtype": "Small Text"},
            {"fieldname": "covison_return_invoice", "label": "Posted Return / Credit Note", "fieldtype": "Link", "options": "Sales Invoice"},
            {"fieldname": "covison_replacement_invoice", "label": "Replacement Sale", "fieldtype": "Link", "options": "Sales Invoice"},
            {"fieldname": "covison_resolution", "label": "Resolution / Customer Agreement", "fieldtype": "Small Text"},
        ],
    }, update=True)


def branding():
    settings = frappe.get_single("System Settings")
    settings.app_name = "Covison ERP"
    settings.time_zone = "Asia/Karachi"
    settings.save(ignore_permissions=True)
    website = frappe.get_single("Website Settings")
    website.update({"app_name": "Covison ERP", "title_prefix": "Covison ERP",
                    "app_logo": "/assets/covison_erp/images/covison-wordmark.svg",
                    "banner_image": "/assets/covison_erp/images/covison-wordmark.svg",
                    "splash_image": "/assets/covison_erp/images/covison-mark.svg",
                    "favicon": "/assets/covison_erp/images/covison-mark.svg",
                    "brand_html": '<img src="/assets/covison_erp/images/covison-wordmark.svg" alt="Covison ERP" width="190">',
                    "footer_powered": "Powered by Covison ERP", "disable_signup": 1})
    website.save(ignore_permissions=True)
    navbar = frappe.get_single("Navbar Settings")
    navbar.app_logo = "/assets/covison_erp/images/covison-wordmark.svg"
    for row in navbar.help_dropdown:
        if row.item_label in ("Frappe School", "Frappe Support", "User Forum", "Report an Issue"):
            row.hidden = 1
        elif row.item_label == "About":
            row.action = "covison.about()"
        elif row.item_label == "Documentation":
            row.item_label = "Mobile Shop Guide"
            row.route = "/desk/mobile-shop"
    navbar.save(ignore_permissions=True)
    frappe.db.set_default("desktop:home_page", "mobile-shop")
    # US-specific UI inherited from the initial US company setup.
    for dt, name in (("Supplier", "irs_1099"), ("Customer", "exempt_from_sales_tax")):
        if frappe.get_meta(dt).has_field(name):
            make_property_setter(dt, name, "hidden", 1, "Check")


def catalog():
    for group, parent in (("Mobile Phones", "All Item Groups"), ("New Phones", "Mobile Phones"),
                          ("Used Phones", "Mobile Phones"), ("Refurbished Phones", "Mobile Phones"),
                          ("Accessories", "All Item Groups"), ("Spare Parts", "All Item Groups")):
        if not frappe.db.exists("Item Group", group):
            frappe.get_doc({"doctype": "Item Group", "item_group_name": group,
                            "parent_item_group": parent, "is_group": int(group == "Mobile Phones")}).insert(ignore_permissions=True)
    if not frappe.db.exists("Item", "COV-DEVICE-SERVICE"):
        frappe.get_doc({"doctype": "Item", "item_code": "COV-DEVICE-SERVICE", "item_name": "Phone Repair / Service Cost",
                        "item_group": "Services", "stock_uom": "Nos", "is_stock_item": 0, "is_sales_item": 0}).insert(ignore_permissions=True)
    for brand in ("Apple", "Samsung", "Xiaomi", "OPPO", "vivo", "Infinix", "TECNO", "realme", "Nokia"):
        if not frappe.db.exists("Brand", brand):
            frappe.get_doc({"doctype": "Brand", "brand": brand}).insert(ignore_permissions=True)
    attributes = {
        "Storage": ["16GB", "32GB", "64GB", "128GB", "256GB", "512GB", "1TB", "2TB"],
        "Color": ["Black", "White", "Blue", "Green", "Red", "Purple", "Pink", "Yellow", "Midnight", "Starlight",
                  "Silver", "Gold", "Graphite", "Space Gray", "Midnight Green", "Pacific Blue", "Sierra Blue",
                  "Deep Purple", "Natural Titanium"],
        "RAM": ["2GB", "3GB", "4GB", "6GB", "8GB", "12GB", "16GB", "24GB"],
    }
    for name, values in attributes.items():
        if not frappe.db.exists("Item Attribute", name):
            frappe.get_doc({"doctype": "Item Attribute", "attribute_name": name,
                            "item_attribute_values": [{"attribute_value": value, "abbr": value.replace(" ", "")}
                                                       for value in values]}).insert(ignore_permissions=True)


def stock_settings():
    doc = frappe.get_single("Stock Settings")
    doc.enable_serial_and_batch_no_for_item = 1
    doc.use_serial_batch_fields = 1
    doc.save(ignore_permissions=True)


def regional_defaults():
    if frappe.db.get_single_value("Global Defaults", "country") != "Pakistan":
        return
    for dt in ("Sales Taxes and Charges Template", "Purchase Taxes and Charges Template"):
        for name in frappe.get_all(dt, filters={"name": ["like", "US ST %"]}, pluck="name"):
            doc = frappe.get_doc(dt, name)
            doc.is_default = 0
            doc.disabled = 1
            doc.save(ignore_permissions=True)


def payments():
    company_name = frappe.db.get_single_value("Global Defaults", "default_company")
    if not company_name:
        return
    company = frappe.get_doc("Company", company_name)
    for name, kind in (("Cash", "Cash"), ("Bank Transfer", "Bank"), ("Easypaisa", "Bank"), ("JazzCash", "Bank"), ("Card", "Bank")):
        account = company.default_cash_account if name == "Cash" else company.default_bank_account
        if name in ("Easypaisa", "JazzCash") and company.default_bank_account:
            parent = frappe.db.get_value("Account", company.default_bank_account, "parent_account")
            found = frappe.db.get_value("Account", {"company": company.name, "account_name": f"{name} Wallet"})
            if not found:
                acc = frappe.get_doc({"doctype": "Account", "account_name": f"{name} Wallet", "company": company.name,
                                      "parent_account": parent, "account_type": "Bank", "account_currency": company.default_currency})
                acc.insert(ignore_permissions=True)
                found = acc.name
            account = found
        doc = frappe.get_doc("Mode of Payment", name) if frappe.db.exists("Mode of Payment", name) else frappe.new_doc("Mode of Payment")
        doc.mode_of_payment = name
        doc.type = kind
        doc.enabled = 1
        if account and not any(row.company == company.name for row in doc.accounts):
            doc.append("accounts", {"company": company.name, "default_account": account})
        doc.save(ignore_permissions=True)


def role_profiles():
    profiles = {
        "Covison Shop Manager": ["Mobile Shop Manager", "Sales Manager", "Purchase Manager", "Stock Manager", "Accounts Manager"],
        "Covison Salesperson": ["Mobile Salesperson", "Sales User"],
        "Covison Purchaser": ["Mobile Purchaser", "Purchase User", "Stock User"],
        "Covison Cashier": ["Mobile Cashier", "Sales User", "Accounts User"],
        "Covison Technician": ["Mobile Technician", "Support Team"],
    }
    for name, roles in profiles.items():
        doc = frappe.get_doc("Role Profile", name) if frappe.db.exists("Role Profile", name) else frappe.new_doc("Role Profile")
        doc.role_profile = name
        doc.set("roles", [{"role": role} for role in roles if frappe.db.exists("Role", role)])
        doc.save(ignore_permissions=True)


def native_permissions():
    from frappe.permissions import add_permission, update_permission_property
    rules = {
        "Mobile Salesperson": {"Sales Invoice": "write", "POS Invoice": "write", "POS Opening Entry": "write",
                               "POS Closing Entry": "write", "Customer": "write", "Serial No": "read",
                               "Warehouse": "read", "POS Profile": "read", "Mode of Payment": "read"},
        "Mobile Purchaser": {"Purchase Order": "write", "Purchase Receipt": "write", "Supplier": "write",
                             "Item": "write", "Purchase Invoice": "write", "Serial No": "read", "Warehouse": "read"},
        "Mobile Cashier": {"Sales Invoice": "write", "POS Invoice": "write", "POS Opening Entry": "write",
                           "POS Closing Entry": "write", "Payment Entry": "write", "Account": "read",
                           "Serial No": "read", "Warehouse": "read", "POS Profile": "read", "Mode of Payment": "read"},
    }
    rules["Mobile Shop Manager"] = {dt: "write" for dt in ("Sales Invoice", "Purchase Invoice", "Purchase Receipt", "Payment Entry", "Journal Entry", "Warranty Claim", "Customer", "Supplier", "Item")}
    rules["Mobile Shop Manager"].update({dt: "read" for dt in ("Company", "Account", "Warehouse", "Serial No", "Mode of Payment")})
    rules["Mobile Cashier"].update({"Journal Entry": "write", "Purchase Invoice": "read", "Purchase Receipt": "read", "Customer": "read", "Supplier": "read", "Mode of Payment": "read", "Company": "read"})
    rules["Mobile Purchaser"].update({"Company": "read", "Account": "read", "Mode of Payment": "read"})
    rules["Mobile Salesperson"].update({"Warranty Claim": "write", "Company": "read"})
    for role, doctypes in rules.items():
        for dt, access in doctypes.items():
            if not frappe.db.exists("Custom DocPerm", {"parent": dt, "role": role, "permlevel": 0}):
                add_permission(dt, role)
            if access == "write":
                for ptype in ("read", "write", "create", "print"):
                    update_permission_property(dt, role, 0, ptype, 1)
                if frappe.get_meta(dt).is_submittable:
                    update_permission_property(dt, role, 0, "submit", 1)

    # Counter staff must let the native stock controller create/submit bundles, but
    # do not receive read/report/export access to bundle valuation data.
    for role in ("Mobile Cashier", "Mobile Salesperson"):
        dt = "Serial and Batch Bundle"
        if not frappe.db.exists("Custom DocPerm", {"parent": dt, "role": role, "permlevel": 0}):
            add_permission(dt, role)
        for ptype in ("create", "write", "submit", "select"):
            update_permission_property(dt, role, 0, ptype, 1)
        for ptype in ("read", "report", "export", "print", "email", "delete", "cancel"):
            update_permission_property(dt, role, 0, ptype, 0)


def retail_access():
    # The native POS page is restricted to Retail and is otherwise omitted from
    # the permitted-page list used by desk navigation.
    domains = frappe.get_single("Domain Settings")
    if not any(row.domain == "Retail" for row in domains.active_domains):
        domains.set_active_domains(["Retail"])


def navigation():
    from frappe.desk.doctype.dock.dock import get_site_dock, save_site_dock
    rows = get_site_dock("erpnext")
    if not any(r.get("link_type") == "Sidebar" and r.get("link_to") == "Mobile Shop" for r in rows):
        rows.insert(0, {"link_type": "Sidebar", "link_to": "Mobile Shop", "hidden": 0})
    pos = next((r for r in rows if r.get("link_type") == "Sidebar" and r.get("link_to") == "Point of Sale"), None)
    if pos is None:
        rows.insert(1, {"link_type": "Sidebar", "link_to": "Point of Sale", "icon": "🛒", "title": "POS", "hidden": 0})
    else:
        pos.update(hidden=0, title="POS", icon="🛒")
    for row in rows:
        if row.get("link_to") in ("Manufacturing", "Projects", "Assets", "Quality", "CRM", "Support", "Accounts", "Selling", "Buying", "Stock", "Setup"):
            row["hidden"] = 1
    save_site_dock("erpnext", rows)


def receipt_formats():
    html = (Path(__file__).parent / "receipt.html").read_text()
    for name, dt in (("Covison Mobile Receipt", "Sales Invoice"), ("Covison POS Receipt", "POS Invoice")):
        doc = frappe.get_doc("Print Format", name) if frappe.db.exists("Print Format", name) else frappe.new_doc("Print Format")
        doc.update({"name": name, "doc_type": dt, "module": "Mobile Shop", "standard": "No", "custom_format": 1,
                    "print_format_type": "Jinja", "html": html, "disabled": 0})
        doc.save(ignore_permissions=True)
    for dt, name in (("Sales Invoice", "Covison Mobile Receipt"), ("POS Invoice", "Covison POS Receipt")):
        make_property_setter(dt, None, "default_print_format", name, "Data", for_doctype=True)
    company_name = frappe.db.get_single_value("Global Defaults", "default_company")
    company = frappe.get_doc("Company", company_name)
    warehouse = frappe.db.get_value("Warehouse", {"company": company.name, "warehouse_name": "Stores", "is_group": 0})
    if warehouse and not frappe.db.exists("POS Profile", "Covison Mobile Shop"):
        frappe.get_doc({"doctype": "POS Profile", "name": "Covison Mobile Shop", "company": company.name,
                        "warehouse": warehouse, "currency": company.default_currency,
                        "selling_price_list": "Standard Selling PKR", "write_off_account": company.round_off_account,
                        "write_off_cost_center": company.cost_center, "write_off_limit": 0,
                        "allow_partial_payment": 1, "allow_rate_change": 1, "allow_discount_change": 1,
                        "hide_unavailable_items": 1, "update_stock": 1, "ignore_pricing_rule": 1,
                        "print_format": "Covison Mobile Receipt" if frappe.db.get_single_value("POS Settings", "invoice_type") == "Sales Invoice" else "Covison POS Receipt",
                        "payments": [{"mode_of_payment": mode, "default": int(mode == "Cash")}
                                     for mode in ("Cash", "Bank Transfer", "Easypaisa", "JazzCash", "Card")]}).insert(ignore_permissions=True)
    if frappe.db.exists("POS Profile", "Covison Mobile Shop"):
        profile = frappe.get_doc("POS Profile", "Covison Mobile Shop")
        profile.print_format = "Covison Mobile Receipt" if frappe.db.get_single_value("POS Settings", "invoice_type") == "Sales Invoice" else "Covison POS Receipt"
        profile.save(ignore_permissions=True)
