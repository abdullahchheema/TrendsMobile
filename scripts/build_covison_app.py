"""Generate Frappe schemas and a dependency-free wheel for the local extension."""
import base64
import hashlib
import io
import csv
import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps/covison_erp/covison_erp"


def field(name, label, kind="Data", **kw):
    return dict(fieldname=name, label=label, fieldtype=kind, **kw)


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def schema(name, fields, **extra):
    perms = [dict(role=role, read=1, write=1, create=1, report=1, export=1, print=1, email=1)
             for role in ("System Manager", "Stock Manager", "Mobile Shop Manager")]
    if name == "Mobile Device":
        for role in ("Mobile Salesperson", "Mobile Purchaser", "Mobile Technician", "Stock User", "Sales User"):
            perms.append(dict(role=role, read=1, write=1, report=1, print=1))
        for role in ("System Manager", "Stock Manager", "Mobile Shop Manager", "Mobile Purchaser", "Purchase Manager"):
            perms.append(dict(role=role, permlevel=1, read=1, write=1))
    else:
        for role in ("Sales User", "Purchase User", "Stock User", "Mobile Salesperson", "Mobile Purchaser"):
            perms.append(dict(role=role, read=1, report=1))
    doc = dict(doctype="DocType", name=name, module="Mobile Shop", engine="InnoDB",
               field_order=[f["fieldname"] for f in fields], fields=fields, permissions=perms,
               track_changes=1, sort_field="modified", sort_order="DESC", **extra)
    slug = name.lower().replace(" ", "_")
    write_json(APP / "mobile_shop/doctype" / slug / f"{slug}.json", doc)


schema("Mobile Model", [
    field("model_name", "Model", reqd=1, unique=1, in_list_view=1),
    field("brand", "Brand", "Link", options="Brand", reqd=1, in_list_view=1),
    field("description", "Description", "Small Text"),
], autoname="field:model_name", title_field="model_name")

schema("Mobile IMEI", [
    field("imei", "IMEI", reqd=1, unique=1),
    field("mobile_device", "Mobile Device", "Data", reqd=1),
], autoname="field:imei")

schema("Mobile Device", [
    field("identity", "Phone Identity", "Section Break"),
    field("serial_no", "Device ID / Serial No", "Link", options="Serial No", reqd=1, unique=1,
          in_list_view=1, read_only_depends_on="eval:!doc.__islocal"),
    field("item_code", "Product Variant", "Link", options="Item", read_only=1, reqd=1),
    field("device_name", "Phone", read_only=1, in_list_view=1),
    field("model", "Model", "Link", options="Mobile Model", read_only=1, in_standard_filter=1),
    field("storage", "Storage", read_only=1, in_list_view=1),
    field("color", "Color", read_only=1),
    field("identity_col", "", "Column Break"),
    field("imei_1", "IMEI 1", unique=1, in_list_view=1, description="Enter the actual 15-digit IMEI from the phone."),
    field("imei_2", "IMEI 2", unique=1, description="Leave blank for a single-IMEI phone."),
    field("manufacturer_serial", "Manufacturer Serial Number"),
    field("regional_version", "Regional Version"),
    field("market_description", "Seller / Market Description", description="Seller wording such as JV; independent of verified carrier lock and PTA results."),
    field("company", "Company", "Link", options="Company", read_only=1),
    field("warehouse", "Current Shop / Warehouse", "Link", options="Warehouse", read_only=1, in_standard_filter=1),
    field("status", "Stock Status", "Select", options="Available\nReserved\nSold\nUnavailable", read_only=1, in_list_view=1),
    field("inspection", "Condition & Inspection", "Section Break"),
    field("device_type", "Phone Type", "Select", options="New\nUsed\nRefurbished", default="Used", in_standard_filter=1),
    field("condition_grade", "Condition", "Select", options="Not Inspected\nExcellent\nVery Good\nGood\nFair\nFor Parts", default="Not Inspected", in_list_view=1),
    field("battery_health", "Battery Health (%)", "Percent"),
    field("battery_health_checked", "Battery Health Recorded", "Check", default=0),
    field("inspection_status", "Inspection Status", "Select", options="Pending\nPassed\nRepair Needed\nFailed", default="Pending", in_standard_filter=1),
    field("inspection_date", "Inspection Date", "Date"),
    field("display_condition", "Display", "Select", options="Not Checked\nOriginal / Working\nReplaced / Working\nDefective", default="Not Checked"),
    field("biometrics", "Face ID / Touch ID", "Select", options="Not Checked\nWorking\nNot Working\nNot Applicable", default="Not Checked"),
    field("camera_test", "Cameras", "Select", options="Not Checked\nPassed\nFailed", default="Not Checked"),
    field("charging_test", "Charging", "Select", options="Not Checked\nPassed\nFailed", default="Not Checked"),
    field("inspection_col", "", "Column Break"),
    field("carrier_lock", "Carrier Lock", "Select", options="Not Checked\nFactory Unlocked\nCarrier Locked", default="Not Checked"),
    field("activation_lock", "Activation Lock / iCloud", "Select", options="Not Checked\nOff\nOn", default="Not Checked"),
    field("sim_configuration", "SIM Configuration", "Select", options="Not Checked\nPhysical SIM\nPhysical SIM + eSIM\nDual Physical SIM\neSIM Only", default="Not Checked"),
    field("audio_test", "Speaker / Microphone", "Select", options="Not Checked\nPassed\nFailed", default="Not Checked"),
    field("network_test", "Calls / Mobile Network", "Select", options="Not Checked\nPassed\nFailed", default="Not Checked"),
    field("repair_history", "Repairs / Replaced Parts", "Small Text"),
    field("included_accessories", "Included Accessories", "Small Text"),
    field("inspection_notes", "Inspection Notes", "Small Text"),
    field("pta_section", "PTA / DIRBS Verification", "Section Break"),
    field("pta_status", "PTA Status — IMEI 1", "Select", options="Not Checked\nCompliant\nNon-compliant\nBlocked\nUnknown", default="Not Checked", in_standard_filter=1, in_list_view=1),
    field("pta_verified_on", "Verified On", "Date"),
    field("pta_verified_by", "Verified By", "Link", options="User"),
    field("pta_evidence", "Verification Evidence", "Attach"),
    field("pta_status_2", "PTA Status — IMEI 2", "Select", options="Not Checked\nCompliant\nNon-compliant\nBlocked\nUnknown", default="Not Checked", depends_on="eval:doc.imei_2"),
    field("pta_verified_on_2", "IMEI 2 Verified On", "Date", depends_on="eval:doc.imei_2"),
    field("pta_evidence_2", "IMEI 2 Verification Evidence", "Attach", depends_on="eval:doc.imei_2"),
    field("pta_notes", "Verification Notes", "Small Text", description="Record the official result; this form does not perform an online PTA lookup."),
    field("pricing", "Individual Device Pricing", "Section Break"),
    field("currency", "Currency", "Link", options="Currency", default="PKR", read_only=1),
    field("purchase_cost", "Purchase / Valuation Cost", "Currency", options="currency", permlevel=1, read_only=1),
    field("asking_price", "Asking Price", "Currency", options="currency", in_list_view=1),
    field("minimum_price", "Minimum Selling Price", "Currency", options="currency", permlevel=1),
    field("wholesale_price", "Wholesale Price", "Currency", options="currency"),
    field("warranty_days", "Shop Warranty (Days)", "Int", default=0),
    field("warranty_terms", "Device Checking / Warranty Terms", "Small Text"),
    field("disclosed_faults", "Faults Disclosed to Buyer", "Small Text"),
    field("history", "Purchase & Sale History", "Section Break"),
    field("supplier", "Supplier", "Link", options="Supplier", read_only=1, permlevel=1),
    field("purchase_receipt", "Purchase Receipt", "Link", options="Purchase Receipt", read_only=1, permlevel=1),
    field("customer", "Customer", "Link", options="Customer", read_only=1),
    field("sales_invoice", "Sales Invoice", "Link", options="Sales Invoice", read_only=1),
    field("pos_invoice", "POS Invoice Pending Closing", "Link", options="POS Invoice", read_only=1),
    field("sold_price", "Final Selling Price", "Currency", options="currency", read_only=1),
    field("sold_on", "Sold On", "Date", read_only=1),
], autoname="field:serial_no", title_field="device_name", search_fields="imei_1,imei_2,device_name,model,item_code",
   image_field="", allow_rename=0)

schema("Mobile Exchange", [
    field("company", "Company", "Link", options="Company", reqd=1),
    field("customer", "Customer", "Link", options="Customer", reqd=1),
    field("purchase_receipt", "Incoming Phone Purchase Receipt", "Link", options="Purchase Receipt", reqd=1),
    field("sales_invoice", "Outgoing Phone Sales Invoice", "Link", options="Sales Invoice", reqd=1),
    field("currency", "Currency", "Link", options="Currency", read_only=1),
    field("incoming_value", "Incoming Phone Value", "Currency", options="currency", read_only=1),
    field("outgoing_value", "Outgoing Phone Value", "Currency", options="currency", read_only=1),
    field("net_difference", "Net Difference Before Settlement", "Currency", options="currency", read_only=1),
    field("settlement_direction", "Settlement Direction", read_only=1),
    field("notes", "Notes", "Small Text", description="Links the purchase and sale. Record and allocate payments on the original documents; this record does not post or settle balances."),
], autoname="COV-EX-.#####", title_field="customer")

write_json(APP / "mobile_shop/page/mobile_shop/mobile_shop.json", dict(
    doctype="Page", name="mobile-shop", page_name="mobile-shop", title="Mobile Shop",
    module="Mobile Shop", standard="Yes", roles=[{"role": r} for r in (
        "System Manager", "Stock Manager", "Stock User", "Sales User", "Purchase User",
        "Mobile Shop Manager", "Mobile Salesperson", "Mobile Purchaser", "Mobile Technician")]))

sidebar_items = [dict(type="Link", label="Open POS", link_type="Page", link_to="point-of-sale"),
                 dict(type="Link", label="Dashboard", link_type="Page", link_to="mobile-shop")]
groups = [
    ("Phones", [("Phone Inventory", "DocType", "Mobile Device"), ("Models", "DocType", "Mobile Model"), ("Variants & Accessories", "DocType", "Item")]),
    ("Buy", [("Buy a Phone", "DocType", "Mobile Intake"), ("Purchase Receipts", "DocType", "Purchase Receipt"), ("Supplier Bills / Expenses", "DocType", "Purchase Invoice")]),
    ("Sell / POS", [("Sales & Returns", "DocType", "Sales Invoice"), ("POS", "Page", "point-of-sale")]),
    ("Exchanges", [("Phone Exchanges", "DocType", "Mobile Exchange")]),
    ("Customers & Suppliers", [("Customers", "DocType", "Customer"), ("Suppliers", "DocType", "Supplier")]),
    ("Payments & Expenses", [("Payments / Advances", "DocType", "Payment Entry"), ("Daily Closing", "DocType", "Mobile Daily Closing"), ("POS Shifts", "DocType", "POS Closing Entry")]),
    ("Staff", [("Staff Names", "DocType", "Mobile Staff"), ("Staff Activity", "Report", "Staff Activity"), ("Staff Summary", "Report", "Staff Summary")]),
    ("Warranty", [("Warranty / Returns", "DocType", "Warranty Claim")]),
    ("Reports", [("Phone Stock", "Report", "Mobile Inventory"), ("Stock Age & Prices", "Report", "Mobile Stock Ageing"),
        ("Device Profit", "Report", "Device Profit"), ("Device Costs & Margin", "Report", "Mobile Device Economics"),
        ("Customer Balances", "Report", "Accounts Receivable"), ("Supplier Balances", "Report", "Accounts Payable"), ("General Ledger", "Report", "General Ledger"), ("Purchase Register", "Report", "Purchase Register"),
        ("Sales Register", "Report", "Sales Register"), ("Payment Ledger", "Report", "Payment Ledger"), ("Stock Balance", "Report", "Stock Balance")]),
    ("Settings", [("Shop Settings", "DocType", "Mobile Shop Settings")]),
]
for heading, links in groups:
    sidebar_items.append(dict(type="Section Break", label=heading, collapsible=1, keep_closed=int(heading in ("Reports", "Settings"))))
    for label, kind, target in links:
        sidebar_items.append(dict(type="Link", label=label, link_type=kind, link_to=target, child=1))
write_json(APP / "mobile_shop/sidebar/mobile_shop/mobile_shop.json", dict(
    doctype="Sidebar", name="Mobile Shop", title="Mobile Shop", module="Mobile Shop", app="covison_erp",
    header_icon="📱", standard=1, items=sidebar_items))
write_json(APP / "mobile_shop/sidebar/point_of_sale/point_of_sale.json", dict(
    doctype="Sidebar", name="Point of Sale", title="Point of Sale", module="Mobile Shop", app="covison_erp",
    header_icon="🛒", standard=1, items=[
        dict(type="Link", label="Open POS", link_type="Page", link_to="point-of-sale"),
        dict(type="Link", label="POS Opening / Start Shift", link_type="DocType", link_to="POS Opening Entry"),
        dict(type="Link", label="POS Closing / End Shift", link_type="DocType", link_to="POS Closing Entry"),
        dict(type="Link", label="Sales Receipts", link_type="DocType", link_to="Sales Invoice"),
        dict(type="Link", label="Mobile Shop", link_type="Page", link_to="mobile-shop"),
    ]))
write_json(APP / "dock/covison_erp/covison_erp.json", dict(
    doctype="Dock", name="covison_erp", app="covison_erp", user="", standard=1, mount_on="erpnext",
    items=[dict(link_type="Sidebar", link_to="Mobile Shop", icon="📱", title="Mobile Shop", added=1, hidden=0),
           dict(link_type="Sidebar", link_to="Point of Sale", icon="🛒", title="POS", added=1, hidden=0)]))

for name in ("Mobile Inventory", "Device Profit", "Mobile Stock Ageing", "Mobile Device Economics"):
    slug = name.lower().replace(" ", "_")
    write_json(APP / "mobile_shop/report" / slug / f"{slug}.json", dict(
        doctype="Report", name=name, report_name=name, ref_doctype="Mobile Device", report_type="Script Report",
        is_standard="Yes", module="Mobile Shop", add_total_row=0, disabled=0,
        roles=[{"role": r} for r in (("System Manager", "Stock Manager", "Mobile Shop Manager", "Purchase Manager")
               if name in ("Device Profit", "Mobile Device Economics") else ("System Manager", "Stock Manager", "Stock User", "Sales User", "Mobile Shop Manager"))]))

from covison_workflow_schemas import build
build(APP, field, schema, write_json)
from covison_staff_schemas import build as build_staff
build_staff(APP, field, schema, write_json)

# Pure-Python wheel; no external package build dependencies or network required.
dist = ROOT / "dist"
dist.mkdir(exist_ok=True)
wheel_path = dist / "covison_erp-1.0.0-py3-none-any.whl"
records = []
with ZipFile(wheel_path, "w", ZIP_DEFLATED) as archive:
    def add(name, data):
        archive.writestr(name, data)
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        records.append((name, "sha256=" + digest, str(len(data))))
    for path in sorted(APP.rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
            add("covison_erp/" + str(path.relative_to(APP)), path.read_bytes())
    meta = "covison_erp-1.0.0.dist-info"
    add(meta + "/METADATA", b"Metadata-Version: 2.1\nName: covison-erp\nVersion: 1.0.0\nSummary: Covison ERP mobile shop extension\nLicense: GPL-3.0-or-later\n")
    add(meta + "/WHEEL", b"Wheel-Version: 1.0\nGenerator: covison-build\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
    records.append((meta + "/RECORD", "", ""))
    buf = io.StringIO()
    csv.writer(buf).writerows(records)
    archive.writestr(meta + "/RECORD", buf.getvalue())
print(wheel_path)
