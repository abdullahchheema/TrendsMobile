from covison_erp.api import device_rows
from covison_erp.devices import can_see_costs


def execute(filters=None):
    filters = filters or {}
    query = {k: v for k, v in filters.items() if k in ("status", "model", "warehouse", "pta_status") and v}
    columns = [
        {"fieldname": "name", "label": "Device", "fieldtype": "Link", "options": "Mobile Device", "width": 170},
        {"fieldname": "device_name", "label": "Phone", "width": 260},
        {"fieldname": "imei_1", "label": "IMEI", "width": 160},
        {"fieldname": "storage", "label": "Storage", "width": 100},
        {"fieldname": "condition_grade", "label": "Condition", "width": 120},
        {"fieldname": "pta_status", "label": "PTA", "width": 120},
        {"fieldname": "status", "label": "Stock Status", "width": 110},
        {"fieldname": "warehouse", "label": "Shop", "fieldtype": "Link", "options": "Warehouse", "width": 170},
        {"fieldname": "asking_price", "label": "Asking Price (PKR)", "fieldtype": "Currency", "options": "currency", "width": 160},
    ]
    if can_see_costs():
        columns += [{"fieldname": "purchase_cost", "label": "Device Cost (PKR)", "fieldtype": "Currency", "options": "currency", "width": 160}]
    return columns, device_rows(query)
