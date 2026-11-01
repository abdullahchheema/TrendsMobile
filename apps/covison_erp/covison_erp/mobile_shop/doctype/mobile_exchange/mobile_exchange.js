frappe.ui.form.on('Mobile Exchange', {
  refresh(frm) {
    if(frm.is_new() && !frm.doc.company) frm.set_value('company',frappe.defaults.get_default('company'));
    if(!frm.is_new() && !frm.doc.journal_entry && !frm.doc.payment_entry) {
      frm.add_custom_button('Incoming Supplier Bill',async ()=>{
        const bill = await covison.call('purchase_bill',{receipt:frm.doc.purchase_receipt});
        await frm.set_value('purchase_invoice',bill); await frm.save(); covison.open('Purchase Invoice',bill);
      });
      frm.add_custom_button('Settle Exchange',()=>frappe.prompt([
        {fieldtype:'HTML',options:'<p>This posts an accounting offset between the customer and supplier invoices, then records any remaining difference. Both invoices must be submitted. Enter only a payment actually made or received.</p>'},
        {fieldname:'mode_of_payment',label:'Payment method for difference',fieldtype:'Link',options:'Mode of Payment'},
        {fieldname:'handled_by',label:'Difference payment handled by',fieldtype:'Link',options:'Mobile Staff',get_query:()=>({filters:{company:frm.doc.company,enabled:1,can_handle_payments:1}})},
        {fieldname:'reference',label:'Actual transfer / transaction reference',fieldtype:'Data'},
        {fieldname:'confirmed',label:'I have checked the invoices and actual payment',fieldtype:'Check',reqd:1},
      ], async values=>{if(values.confirmed){await covison.call('settle_exchange',{exchange:frm.doc.name,mode_of_payment:values.mode_of_payment,reference:values.reference,handled_by:values.handled_by});frm.reload_doc();}},'Settle phone exchange','Post settlement'));
    }
    for (const [field,dt,label] of [['journal_entry','Journal Entry','Exchange Offset'],['payment_entry','Payment Entry','Difference Payment']])
      if(frm.doc[field]) frm.add_custom_button(label,()=>covison.open(dt,frm.doc[field]));
  },
  setup(frm) {
    frm.set_query('purchase_receipt',()=>({filters:{docstatus:1,company:frm.doc.company,covison_trade_in_customer:frm.doc.customer}}));
    frm.set_query('sales_invoice',()=>({filters:{company:frm.doc.company,customer:frm.doc.customer,is_return:0,docstatus:['!=',2]}}));
  }
});
