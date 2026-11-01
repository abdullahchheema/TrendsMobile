frappe.ui.form.on('Mobile Intake', {
  setup(frm) {
    frm.set_query('item_code',()=>({filters:{covison_is_mobile:1,has_variants:0,disabled:0}}));
    frm.set_query('warehouse',()=>({filters:{company:frm.doc.company,is_group:0,disabled:0}}));
  },
  refresh(frm) {
    if (frm.is_new() && !frm.doc.company) frm.set_value('company',frappe.defaults.get_default('company'));
    frm.dashboard.set_headline_alert(frm.doc.docstatus===0 ? 'Save the assessment, then Submit to receive this exact phone into stock. A supplier bill records the payable separately.' : 'Phone received. Open the supplier bill to record the amount owed and payment.');
    if (frm.doc.purchase_receipt) {
      frm.add_custom_button('Purchase Receipt',()=>covison.open('Purchase Receipt',frm.doc.purchase_receipt));
      if(frm.doc.docstatus===1) frm.add_custom_button('Supplier Bill',async ()=>covison.open('Purchase Invoice',await covison.call('purchase_bill',{receipt:frm.doc.purchase_receipt})));
    }
    if(frm.doc.mobile_device) frm.add_custom_button('Phone in Stock',()=>covison.open('Mobile Device',frm.doc.mobile_device));
    frm.add_custom_button('Official PTA Verification',()=>window.open('https://dirbs.pta.gov.pk/','_blank','noopener'));
  }
});
