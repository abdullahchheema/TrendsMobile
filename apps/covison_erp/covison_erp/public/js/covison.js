/* Covison's additions leave ERPNext's native forms and posting workflow intact. */
frappe.provide("covison");
covison.about = () => frappe.msgprint({
  title: __("Covison ERP"),
  message: `<div style="padding:16px"><img src="/assets/covison_erp/images/covison-wordmark.svg" width="220" alt="Covison ERP">
    <p style="margin-top:20px">Mobile retail, device inventory and accounts for your shop.</p>
    <p>Version 1.0.0 · PKR · Pakistan</p>
    <p class="text-muted small">Built on ERPNext and Frappe. Upstream licenses and source credits remain available in the project.</p></div>`,
});
$(document).ready(() => {
  const title = document.querySelector("title");
  const brandTitle = () => {
    if (!document.title.includes("Covison ERP")) {
      document.title = document.title.replace(/\s*[-|]\s*(ERPNext|Frappe)\s*$/i, "") + " | Covison ERP";
    }
  };
  if (title) new MutationObserver(brandTitle).observe(title, {childList: true});
  brandTitle();
});

for (const dt of ["Sales Invoice Item", "POS Invoice Item", "Delivery Note Item"]) {
  frappe.ui.form.on(dt, {
    async covison_device(frm, cdt, cdn) {
      const row = locals[cdt][cdn];
      if (!row.covison_device) return;
      const phone = await frappe.db.get_doc("Mobile Device", row.covison_device);
      await frappe.model.set_value(cdt, cdn, "item_code", phone.item_code);
      await frappe.model.set_value(cdt, cdn, {serial_no: phone.serial_no, use_serial_batch_fields: 1,
        warehouse: phone.warehouse, qty: frm.doc.is_return ? -1 : 1,
        covison_imei: phone.imei_1, covison_imei_2: phone.imei_2,
        covison_condition: phone.condition_grade, covison_pta_status: phone.pta_status,
        covison_battery: phone.battery_health_checked ? `${phone.battery_health}%` : "Not recorded",
        covison_warranty_days: phone.warranty_days});
      if (phone.asking_price && !frm.doc.is_return) await frappe.model.set_value(cdt, cdn, "rate", phone.asking_price);
    },
  });
}
frappe.ui.form.on("Item", {
  covison_is_mobile(frm) {
    if (frm.is_new() && frm.doc.covison_is_mobile) {
      frm.set_value({has_serial_no: 1, use_serial_no_wise_valuation: 1, serial_no_series: "COV-PHN-.######", stock_uom: "Nos"});
    }
  },
});
frappe.ui.form.on("Warranty Claim", {
  async covison_device(frm) {
    if (!frm.doc.covison_device) return;
    const phone = await frappe.db.get_doc("Mobile Device", frm.doc.covison_device);
    frm.set_value({serial_no: phone.serial_no, item_code: phone.item_code, customer: phone.customer, company: phone.company});
  },
});
