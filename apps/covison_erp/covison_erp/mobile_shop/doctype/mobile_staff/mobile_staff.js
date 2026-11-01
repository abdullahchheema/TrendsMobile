frappe.ui.form.on('Mobile Staff',{
  setup(frm){frm.set_query('warehouse',()=>({filters:{company:frm.doc.company,is_group:0,disabled:0}}));},
  refresh(frm){
    if(frm.is_new()&&!frm.doc.company)frm.set_value('company',frappe.defaults.get_default('company'));
    if(!frm.is_new()&&(frappe.user_roles||[]).some(r=>['System Manager','Mobile Shop Manager'].includes(r))){
      frm.add_custom_button('Activity',()=>frappe.set_route('query-report','Staff Activity',{company:frm.doc.company,staff:frm.doc.name}));
      frm.add_custom_button('Summary',()=>frappe.set_route('query-report','Staff Summary',{company:frm.doc.company,staff:frm.doc.name}));
    }
  }
});
