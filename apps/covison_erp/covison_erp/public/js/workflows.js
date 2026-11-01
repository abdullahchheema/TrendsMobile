/* Counter workflows: review drafts, then post explicit financial actions. */
frappe.provide('covison');
covison.call = async (method, args) => (await frappe.call({method:'covison_erp.workflows.' + method, args, freeze:true})).message;
covison.open = (dt, name) => frappe.set_route('Form', dt, name);
covison.can = (dt, action='create') => (frappe.boot.user?.can_create || []).includes(dt) && action === 'create';
covison.checkout = function(invoice, total) {
  const dialog = new frappe.ui.Dialog({title:'Take payment & post sale', size:'large', fields:[
    {fieldtype:'HTML', options:'<p>Record only payments actually received. Leave the table empty for a credit sale, or enter a partial payment to leave a balance. Non-cash payments need their transaction references.</p>'},
    {fieldname:'sold_by',label:'Sold by',fieldtype:'Link',options:'Mobile Staff'},
    {fieldname:'collected_by',label:'Payment collected by',fieldtype:'Link',options:'Mobile Staff'},
    {fieldname:'payments', label:'Payments received (PKR)', fieldtype:'Table', in_place_edit:true, fields:[
      {fieldname:'mode_of_payment',label:'Method',fieldtype:'Link',options:'Mode of Payment',reqd:1,in_list_view:1},
      {fieldname:'amount',label:'Amount',fieldtype:'Currency',reqd:1,in_list_view:1},
      {fieldname:'reference',label:'Transaction reference',fieldtype:'Data',in_list_view:1},
    ], data:[]},
    {fieldname:'confirmed',label:'I have checked the device, invoice and payments received',fieldtype:'Check',reqd:1},
  ], primary_action_label:'Post sale & payments', primary_action:async values=>{
    if (!values.confirmed) return;
    const result = await covison.call('checkout',{invoice,sold_by:values.sold_by,collected_by:values.collected_by,payments:JSON.stringify(values.payments || [])});
    dialog.hide(); covison.open('Sales Invoice',result.name); cur_frm?.reload_doc();
    frappe.show_alert({message:'Sale and recorded payments posted',indicator:'green'});
  }});
  const company=cur_frm?.doc?.company || frappe.defaults.get_default('company');
  for(const [field,capability] of [['sold_by','can_sell'],['collected_by','can_handle_payments']])
    dialog.fields_dict[field].get_query=()=>({filters:{company,enabled:1,[capability]:1}});
  frappe.call({method:'covison_erp.staff.defaults',args:{company}}).then(({message:d})=>{
    if(d)dialog.set_values({sold_by:cur_frm?.doc?.covison_sold_by || d.sold_by,collected_by:d.collected_by});
  });
  dialog.show();
};
covison.return_phone = function(invoice, devices) {
  frappe.prompt([
    {fieldname:'device',label:'Phone being returned',fieldtype:'Select',options:devices.join('\n'),reqd:1},
    {fieldname:'condition',label:'Received condition / accessories',fieldtype:'Small Text',reqd:1},
    {fieldname:'reason',label:'Return reason',fieldtype:'Small Text',reqd:1},
  ], async values=>covison.open('Sales Invoice',await covison.call('return_phone',{invoice,...values})),
  'Return the exact phone','Create draft return');
};
frappe.ui.form.on('Sales Invoice', {
  refresh(frm) {
    const phones = (frm.doc.items || []).filter(r=>r.covison_device).map(r=>r.covison_device);
    if (!phones.length) return;
    if (!frm.is_new() && frm.doc.docstatus===0 && !frm.doc.is_return && !frm.doc.is_pos && frm.perm.some(p=>p.submit))
      frm.add_custom_button('Counter Checkout',()=>covison.checkout(frm.doc.name,frm.doc.grand_total));
    if (frm.doc.docstatus===1 && !frm.doc.is_return)
      frm.add_custom_button('Return a Phone',()=>covison.return_phone(frm.doc.name,phones));
  }
});
frappe.ui.form.on('Purchase Receipt', {
  refresh(frm) {
    if (frm.doc.docstatus===1 && !frm.doc.is_return)
      frm.add_custom_button('Supplier Bill',async ()=>covison.open('Purchase Invoice',await covison.call('purchase_bill',{receipt:frm.doc.name})));
  }
});
frappe.ui.form.on('Warranty Claim', {
  refresh(frm) {
    if (frm.doc.covison_sales_invoice && frm.doc.covison_device)
      frm.add_custom_button('Create Phone Return',()=>covison.return_phone(frm.doc.covison_sales_invoice,[frm.doc.covison_device]));
  },
  async covison_device(frm) {
    if (!frm.doc.covison_device) return;
    const {message:phone} = await frappe.db.get_value('Mobile Device',frm.doc.covison_device,['serial_no','customer','sales_invoice','company']);
    if (phone) frm.set_value({serial_no:phone.serial_no,customer:phone.customer,company:phone.company,covison_sales_invoice:phone.sales_invoice});
  }
});

// Responsibility fields are shared by native forms and the phone intake screen.
for (const dt of ['Mobile Intake','Purchase Receipt','Purchase Invoice','Purchase Order','Sales Invoice','POS Invoice','Payment Entry','Mobile Daily Closing']) {
  frappe.ui.form.on(dt,{
    setup(frm){
      for(const [field,capability] of Object.entries({covison_purchased_by:'can_buy',covison_sold_by:'can_sell',covison_collected_by:'can_handle_payments',covison_handled_by:'can_handle_payments',covison_closed_by:'can_handle_payments'})){
        if(frm.fields_dict[field])frm.set_query(field,()=>({filters:{company:frm.doc.company,enabled:1,[capability]:1}}));
      }
    }
  });
}
