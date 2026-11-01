frappe.ui.form.on('Mobile Daily Closing', {
  refresh(frm) {
    if(frm.is_new() && !frm.doc.company) frm.set_value('company',frappe.defaults.get_default('company'));
    if(frm.doc.docstatus===0) frm.add_custom_button('Load / Refresh Ledger Balances',async ()=>{
      if(!frm.doc.company || !frm.doc.closing_date) return frappe.msgprint('Select company and closing date first.');
      await frm.call('load_balances'); frm.refresh_field('balances'); frm.dirty();
    });
    frm.add_custom_button('POS Shift Closing',()=>frappe.set_route('List','POS Closing Entry'));
  }
});
