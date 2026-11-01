frappe.query_reports['Staff Activity']={filters:[
  {fieldname:'company',label:'Company',fieldtype:'Link',options:'Company',default:frappe.defaults.get_default('company'),reqd:1},
  {fieldname:'from_date',label:'From Date',fieldtype:'Date',default:frappe.datetime.month_start(),reqd:1},
  {fieldname:'to_date',label:'To Date',fieldtype:'Date',default:frappe.datetime.get_today(),reqd:1},
  {fieldname:'staff',label:'Staff Member',fieldtype:'Link',options:'Mobile Staff'},
]};
