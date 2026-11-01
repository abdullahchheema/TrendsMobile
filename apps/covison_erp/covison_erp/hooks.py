app_name = "covison_erp"
app_title = "Covison ERP"
app_publisher = "Covison"
app_description = "Mobile shop inventory, individual device pricing and retail workflows"
app_email = ""
app_license = "GPL-3.0-or-later"
app_logo_url = "/assets/covison_erp/images/covison-mark.svg"
required_apps = ["erpnext"]
before_install = "covison_erp.setup.before_install"
app_include_js = ["/assets/covison_erp/js/covison.js", "/assets/covison_erp/js/workflows.js"]
app_include_css = ["/assets/covison_erp/css/covison.css"]
web_include_css = ["/assets/covison_erp/css/website.css"]
after_install = "covison_erp.setup.install"
after_migrate = "covison_erp.setup.install"
boot_session = "covison_erp.branding.boot_session"
doc_events = {
    "Serial and Batch Bundle": {"before_validate": "covison_erp.devices.validate_counter_bundle"},
    "Item": {"validate": "covison_erp.devices.validate_item"},
    "Mobile Device": {"validate": "covison_erp.devices.validate_device"},
    "Purchase Receipt": {
        "on_submit": "covison_erp.devices.sync_transaction",
        "on_cancel": "covison_erp.devices.sync_transaction",
    },
    "Sales Invoice": {
        "before_validate": "covison_erp.workflows.receipt_snapshot",
        "validate": ["covison_erp.devices.validate_sale", "covison_erp.workflows.validate_return"],
        "before_submit": "covison_erp.devices.lock_sale_devices",
        "on_submit": "covison_erp.devices.sync_transaction",
        "on_cancel": "covison_erp.devices.sync_transaction",
    },
    "POS Invoice": {
        "before_validate": "covison_erp.workflows.receipt_snapshot",
        "validate": ["covison_erp.devices.validate_sale", "covison_erp.workflows.validate_return"],
        "before_submit": "covison_erp.devices.lock_sale_devices",
        "on_submit": "covison_erp.devices.sync_transaction",
        "on_cancel": "covison_erp.devices.sync_transaction",
    },
    "Delivery Note": {
        "before_submit": "covison_erp.devices.lock_sale_devices",
        "validate": "covison_erp.devices.validate_sale",
        "on_submit": "covison_erp.devices.sync_transaction",
        "on_cancel": "covison_erp.devices.sync_transaction",
    },
    "Journal Entry": {"on_cancel": "covison_erp.workflows.settlement_cancelled"},
    "Payment Entry": {"on_cancel": "covison_erp.workflows.settlement_cancelled"},
    "Purchase Invoice": {"validate": "covison_erp.workflows.validate_expenses"},
    "Warranty Claim": {"validate": "covison_erp.workflows.validate_warranty"},
    "Stock Entry": {
        "on_submit": "covison_erp.devices.sync_transaction",
        "on_cancel": "covison_erp.devices.sync_transaction",
    },
}

jinja = {"methods": ["covison_erp.workflows.receipt_payments"]}

# Track the responsible staff member independently of the document owner's login.
for _doctype in ("Mobile Intake", "Purchase Receipt", "Purchase Invoice", "Purchase Order", "Sales Invoice", "POS Invoice", "Payment Entry", "Mobile Daily Closing"):
    _events = doc_events.setdefault(_doctype, {})
    for _event, _method in (("validate", "covison_erp.staff.validate_transaction"), ("before_submit", "covison_erp.staff.before_submit")):
        _existing = _events.get(_event, [])
        _events[_event] = ([_existing] if isinstance(_existing, str) else list(_existing)) + [_method]

for _doctype in ("Mobile Intake", "Purchase Receipt", "Purchase Invoice", "Purchase Order", "Sales Invoice", "POS Invoice", "Payment Entry", "Journal Entry", "Stock Entry", "Delivery Note", "Mobile Exchange", "Warranty Claim", "POS Opening Entry", "POS Closing Entry"):
    _events = doc_events.setdefault(_doctype, {})
    _existing = _events.get("before_validate", [])
    _events["before_validate"] = ([_existing] if isinstance(_existing, str) else list(_existing)) + ["covison_erp.demo.mark_transaction"]
