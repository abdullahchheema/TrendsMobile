frappe.listview_settings["Mobile Device"] = {
  add_fields: ["status", "inspection_status", "imei_1"],
  get_indicator(doc) {
    if (doc.status === "Sold") return [__("Sold"), "blue", "status,=,Sold"];
    if (!doc.imei_1) return [__("IMEI Required"), "orange", "imei_1,is,not set"];
    return [__(doc.status), doc.status === "Available" ? "green" : "gray", `status,=,${doc.status}`];
  },
};
