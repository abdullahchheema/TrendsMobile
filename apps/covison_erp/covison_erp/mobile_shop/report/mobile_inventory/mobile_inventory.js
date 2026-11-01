frappe.query_reports["Mobile Inventory"] = {filters: [
  {fieldname:"status",label:"Stock Status",fieldtype:"Select",options:"\nAvailable\nSold\nUnavailable",default:"Available"},
  {fieldname:"model",label:"Model",fieldtype:"Link",options:"Mobile Model"},
  {fieldname:"warehouse",label:"Shop / Warehouse",fieldtype:"Link",options:"Warehouse"},
  {fieldname:"pta_status",label:"PTA Status",fieldtype:"Select",options:"\nNot Checked\nCompliant\nNon-compliant\nBlocked\nUnknown"},
]};
