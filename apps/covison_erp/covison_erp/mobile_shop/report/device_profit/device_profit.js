frappe.query_reports["Device Profit"] = {filters: [
  {fieldname:"from_date",label:"From Date",fieldtype:"Date",default:frappe.datetime.month_start(),reqd:1},
  {fieldname:"to_date",label:"To Date",fieldtype:"Date",default:frappe.datetime.get_today(),reqd:1},
]};
