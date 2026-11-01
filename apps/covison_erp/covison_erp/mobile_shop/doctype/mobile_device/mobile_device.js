frappe.ui.form.on("Mobile Device", {
  refresh(frm) {
    const tracked = frm.doc.status === "Available";
    frm.dashboard.set_headline_alert(
      !frm.doc.imei_1 ? __("Enter this phone's actual IMEI before selling. Device IDs are internal stock identifiers.") :
      frm.doc.inspection_status !== "Passed" ? __("Inspection is incomplete. Review the condition and verification details.") : ""
    );
    if (tracked && frm.doc.imei_1) {
      frm.add_custom_button(__("Create Sale"), () => {
        frappe.prompt([
          {fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer", reqd: 1},
          {fieldname:"sold_by",label:"Sold by",fieldtype:"Link",options:"Mobile Staff",get_query:()=>({filters:{company:frm.doc.company,enabled:1,can_sell:1}})},
          {fieldname: "rate", label: __("Selling Price (PKR)"), fieldtype: "Currency", reqd: 1, default: frm.doc.asking_price},
        ], async (values) => {
          const response = await frappe.call({method: "covison_erp.api.create_sale", args: {device: frm.doc.name, ...values}, freeze: true});
          frappe.set_route("Form", "Sales Invoice", response.message.name);
        }, __("Sell This Phone"), __("Create Draft Invoice"));
      });
    }
    if (frm.doc.customer) frm.add_custom_button(__("Warranty Claim"), () => frappe.new_doc("Warranty Claim", {
      covison_sales_invoice: frm.doc.sales_invoice, covison_device: frm.doc.name, serial_no: frm.doc.serial_no, customer: frm.doc.customer, company: frm.doc.company,
    }));
    frm.add_custom_button("Official PTA Verification", () => window.open("https://dirbs.pta.gov.pk/", "_blank", "noopener"));
    if ((frappe.user_roles || []).some(r=>["System Manager","Mobile Shop Manager","Mobile Purchaser","Purchase Manager"].includes(r))) {
      frm.add_custom_button("Record Repair / Service Cost",()=>frappe.prompt([
        {fieldname:"supplier",label:"Repairer / Supplier",fieldtype:"Link",options:"Supplier",reqd:1},
        {fieldname:"amount",label:"Cost (PKR)",fieldtype:"Currency",reqd:1},
        {fieldname:"description",label:"Work performed",fieldtype:"Small Text",reqd:1},
      ], async values=>covison.open("Purchase Invoice",await covison.call("device_expense",{device:frm.doc.name,...values})),"Record device expense","Create draft supplier bill"));
    }
    frm.add_custom_button(__("Stock History"), () => frappe.set_route("query-report", "Stock Ledger", {item_code: frm.doc.item_code}));
    frm.set_query("serial_no", () => ({filters: {status: "Active"}}));
  },
  pta_status(frm) {
    if (frm.doc.pta_status !== "Not Checked") {
      if (!frm.doc.pta_verified_on) frm.set_value("pta_verified_on", frappe.datetime.get_today());
      if (!frm.doc.pta_verified_by) frm.set_value("pta_verified_by", frappe.session.user);
    }
  },
});
