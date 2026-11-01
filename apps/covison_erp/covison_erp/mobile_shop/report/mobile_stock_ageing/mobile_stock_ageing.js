frappe.query_reports['Mobile Stock Ageing'] = {filters:[
  {fieldname:'warehouse',label:'Shop / Warehouse',fieldtype:'Link',options:'Warehouse'},
  {fieldname:'review_only',label:'Only phones needing review',fieldtype:'Check',default:1},
]};
