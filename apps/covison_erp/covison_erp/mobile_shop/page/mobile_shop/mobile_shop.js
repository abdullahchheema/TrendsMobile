frappe.pages["mobile-shop"].on_page_load = function(wrapper) {
  const page = frappe.ui.make_app_page({parent: wrapper, title: "Mobile Shop", single_column: true});
  const app = new CovisonShop(page);
  wrapper.covison = app;
};
frappe.pages["mobile-shop"].on_page_show = function(wrapper) { wrapper.covison?.load(); };

class CovisonShop {
  constructor(page) {
    this.page = page;
    page.set_primary_action(__("Open POS"), () => frappe.set_route("point-of-sale"));
    this.view = "devices";
    this.request = 0;
    this.root = $(`<div class="covison-shop">
      <div class="cov-demo-notice" role="status" hidden><strong>Demo dataset</strong> · Synthetic IMEIs and sample transactions. PTA verification remains unchecked.</div>
      <section class="cov-hero" aria-label="Dashboard overview">
        <div class="cov-hero-copy"><div class="cov-eyebrow">RETAIL OPERATIONS</div><h1>Mobile Shop Dashboard</h1>
          <div class="cov-hero-actions"><button class="cov-btn cov-btn-light" data-action="pos">Open POS →</button>
            <button class="cov-btn cov-btn-outline" data-action="buy">＋ Buy a phone</button>
            <button class="cov-btn cov-btn-outline" data-action="exchange">⇄ Phone exchange</button>
            <button class="cov-btn cov-btn-outline" data-action="invoices">Sales & payments ↗</button></div>
        </div><div class="cov-hero-mark"><img src="/assets/covison_erp/images/covison-mark.svg" alt=""><div><strong>Covison ERP</strong><span>Pakistan · PKR</span></div></div>
      </section>
      <div class="cov-stats" aria-live="polite"></div>
      <div class="cov-shortcuts">
        <button data-action="staff">Staff names</button><button data-action="staff-summary">Staff activity</button><button data-action="devices">Phone inventory</button><button data-action="customers">Customers</button>
        <button data-action="suppliers">Suppliers</button><button data-action="warranty">Warranty claims</button>
        <button data-action="payments">Payments</button><button data-action="closing">Daily closing</button><button data-action="ageing">Stock age & prices</button><button data-action="balances">Customer balances</button><button data-action="payables">Supplier balances</button><button data-action="inventory-report">Stock report</button>
        <button data-action="profit-report">Device profit</button><button data-action="pos">POS</button>
      </div>
      <section class="cov-inventory">
        <div class="cov-section-head"><div><div class="cov-eyebrow">YOUR INVENTORY</div><h2>Find the right phone</h2></div>
          <div class="cov-section-actions"><button class="cov-btn" data-action="new-model">＋ Model</button><button class="cov-btn" data-action="new-variant">＋ Product variant</button><button class="cov-btn" data-action="refresh">Refresh</button></div></div>
        <div class="cov-filters">
          <input type="search" class="cov-search" placeholder="Search model, storage, IMEI or device ID…" aria-label="Search phones">
          <select data-filter="status" aria-label="Stock status"><option>Available</option><option>Reserved</option><option>Sold</option><option>Unavailable</option><option>All</option></select>
          <select data-filter="model" aria-label="Phone model"><option value="">All models</option></select>
          <select data-filter="condition" aria-label="Condition"><option value="">All conditions</option><option>Excellent</option><option>Very Good</option><option>Good</option><option>Fair</option><option>Not Inspected</option><option>For Parts</option></select>
          <select data-filter="pta_status" aria-label="PTA status"><option value="">All PTA statuses</option><option>Not Checked</option><option>Compliant</option><option>Non-compliant</option><option>Blocked</option><option>Unknown</option></select>
        </div>
        <div class="cov-inventory-meta"><span class="cov-result-count" aria-live="polite"></span><div class="cov-view-toggle"><button data-view="devices" class="active">Individual phones</button><button data-view="models">By model & variant</button></div></div>
        <div class="cov-results" aria-live="polite"><div class="cov-empty">Loading phone inventory…</div></div>
      </section>
      <div class="cov-footer"><span>Each device keeps its own identity, condition and price.</span><a href="/desk/mobile-device">Open full device register ↗</a></div>
    </div>`).appendTo(page.main);
    this.root.on("click", "[data-action]", (e) => this.action(e.currentTarget.dataset.action, e.currentTarget.dataset.name));
    this.root.on("click", "[data-view]", (e) => {this.view = e.currentTarget.dataset.view; this.render();});
    this.root.on("click", "[data-model]", (e) => {this.root.find('[data-filter="model"]').val(e.currentTarget.dataset.model); this.view = "devices"; this.load();});
    this.root.on("change", "[data-filter]", () => this.load());
    this.root.find(".cov-search").on("input", frappe.utils.debounce(() => this.load(), 250));
    this.load();
  }
  escape(value) { return String(value ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
  money(value) {return `PKR ${new Intl.NumberFormat("en-PK", {maximumFractionDigits: 2}).format(value || 0)}`;}
  async load() {
    const request = ++this.request;
    const args = {search: this.root.find(".cov-search").val()};
    this.root.find("[data-filter]").each((_, el) => {args[el.dataset.filter] = el.value;});
    try {
      const response = await frappe.call({method: "covison_erp.api.dashboard", args});
      if (request !== this.request) return;
      this.data = response.message;
      const selected = this.root.find('[data-filter="model"]').val();
      this.root.find('[data-filter="model"]').html(`<option value="">All models</option>` + this.data.models.map(m => `<option value="${this.escape(m)}">${this.escape(m)}</option>`).join("")).val(selected);
      this.render();
    } catch (e) {if (request === this.request) this.root.find(".cov-results").html('<div class="cov-empty">Inventory could not be loaded. Check your access or use Refresh to retry.</div>');}
  }
  render() {
    if (!this.data) return;
    this.root.find(".cov-demo-notice").prop("hidden", !this.data.demo_data);
    const s = this.data.summary;
    const metrics = [["Phones in stock", s.available, "Individual devices available"],
      [this.data.can_see_costs ? "Inventory value" : "Device sales today", this.money(this.data.can_see_costs ? s.stock_value : s.sales_today), "Pakistani rupees"],
      ["Inspection pending", s.pending_inspection, `${s.missing_imei} phones need IMEI registration`],
      ["Phones sold today", s.sold_today, "Posted device sales"]];
    this.root.find(".cov-stats").html(metrics.map(([title, value, note], i) => `<article class="cov-stat"><div><span class="cov-stat-dot cov-dot-${i}"></span>${title}</div><strong>${value}</strong><small>${note}</small></article>`).join(""));
    const phones = this.data.devices;
    this.root.find(".cov-result-count").text(`${phones.length} ${phones.length === 1 ? "phone" : "phones"} found`);
    this.root.find("[data-view]").each((_, el) => el.classList.toggle("active", el.dataset.view === this.view));
    this.root.find('[data-action="profit-report"]').toggle(this.data.can_see_costs);
    this.root.find('[data-action="staff-summary"]').toggle((frappe.user_roles || []).some(r=>['System Manager','Mobile Shop Manager'].includes(r)));
    for (const [action, dt] of Object.entries({buy:"Mobile Intake",exchange:"Mobile Exchange",closing:"Mobile Daily Closing","new-model":"Mobile Model","new-variant":"Item"}))
      this.root.find(`[data-action="${action}"]`).toggle((frappe.boot.user?.can_create || []).includes(dt));
    if (!phones.length) {this.root.find(".cov-results").html('<div class="cov-empty"><strong>No phones match these filters.</strong><p>Try another model, condition or stock status.</p></div>'); return;}
    if (this.view === "models") {
      const groups = new Map();
      for (const phone of phones) {
        const key = `${phone.model}|${phone.storage}|${phone.color}`;
        if (!groups.has(key)) groups.set(key, {phone, phones: []});
        groups.get(key).phones.push(phone);
      }
      this.root.find(".cov-results").html(`<div class="cov-model-grid">${[...groups.values()].map(({phone:p, phones:list}) => {
        const prices = list.map(d => Number(d.asking_price)).filter(v => v > 0);
        const range = !prices.length ? "Set device prices" : Math.min(...prices) === Math.max(...prices) ? this.money(prices[0]) : `${this.money(Math.min(...prices))} – ${this.money(Math.max(...prices))}`;
        return `<button class="cov-model-card" data-model="${this.escape(p.model)}"><span class="cov-model-icon">▯</span><span class="cov-device-count">${list.length} ${list.length === 1 ? "phone" : "phones"}</span><h3>${this.escape(p.model || p.device_name)}</h3><p>${this.escape(p.storage)} · ${this.escape(p.color)}</p><strong>${range}</strong><small>View individual devices →</small></button>`;
      }).join("")}</div>`);
      return;
    }
    this.root.find(".cov-results").html(`<div class="cov-table-scroll"><table class="cov-table"><thead><tr><th>Phone / variant</th><th>Device / IMEI</th><th>Condition</th><th>PTA</th><th>Asking price</th>${this.data.can_see_costs ? "<th>Device cost</th>" : ""}<th></th></tr></thead><tbody>${phones.map(p => `<tr>
      <td><a href="/desk/mobile-device/${encodeURIComponent(p.name)}" class="cov-phone-title">${this.escape(p.model || p.device_name)}</a><small>${this.escape(p.storage)} · ${this.escape(p.color)}</small></td>
      <td><span class="cov-device-id">${this.escape(p.name)}</span><small class="${p.imei_1 ? "" : "cov-attention"}">${this.escape(p.imei_1 || "IMEI not recorded")}</small></td>
      <td><span>${this.escape(p.condition_grade)}</span><small>${p.battery_health_checked ? `Battery ${this.escape(p.battery_health)}%` : "Battery not recorded"}</small></td>
      <td><span class="cov-badge ${p.pta_status === "Compliant" ? "cov-badge-green" : p.pta_status === "Blocked" ? "cov-badge-red" : ""}">${this.escape(p.pta_status)}</span></td>
      <td><strong>${p.asking_price ? this.money(p.asking_price) : "—"}</strong><small>${p.asking_price ? "Per phone" : "Set asking price"}</small></td>
      ${this.data.can_see_costs ? `<td>${this.money(p.purchase_cost)}</td>` : ""}
      <td class="cov-row-actions"><button class="cov-btn cov-btn-small" data-action="edit" data-name="${this.escape(p.name)}">Details</button>${p.status === "Available" ? `<button class="cov-btn cov-btn-small cov-btn-primary" data-action="sell" data-name="${this.escape(p.name)}">Sell</button>` : `<span class="cov-badge">${this.escape(p.status)}</span>`}</td>
    </tr>`).join("")}</tbody></table></div>`);
  }
  action(action, name) {
    const routes = {staff:["List","Mobile Staff"], "staff-summary":["query-report","Staff Summary"], devices:["List","Mobile Device"], customers:["List","Customer"], suppliers:["List","Supplier"],
      warranty:["List","Warranty Claim"], payments:["List","Payment Entry"], invoices:["List","Sales Invoice"],
      "inventory-report":["query-report","Mobile Inventory"], "profit-report":["query-report","Device Profit"], pos:["point-of-sale"], closing:["List","Mobile Daily Closing"], ageing:["query-report","Mobile Stock Ageing"], balances:["query-report","Accounts Receivable"], payables:["query-report","Accounts Payable"]};
    if (routes[action]) return frappe.set_route(...routes[action]);
    if (action === "refresh") return this.load();
    if (action === "edit") return frappe.set_route("Form", "Mobile Device", name);
    if (action === "buy") return frappe.new_doc("Mobile Intake");
    if (action === "exchange") return frappe.new_doc("Mobile Exchange");
    if (action === "new-model") return frappe.new_doc("Mobile Model");
    if (action === "new-variant") return frappe.new_doc("Item", {covison_is_mobile:1, has_serial_no:1,
      use_serial_no_wise_valuation:1, serial_no_series:"COV-PHN-.######", stock_uom:"Nos", item_group:"Used Phones"});
    if (action === "sell") {
      const phone = this.data.devices.find(p => p.name === name);
      if (!phone.imei_1) return frappe.msgprint({title: "IMEI required", message: "Open Details and record the actual IMEI before selling this phone.", indicator:"orange"});
      frappe.prompt([
        {fieldname:"customer",label:"Customer",fieldtype:"Link",options:"Customer",reqd:1},
        {fieldname:"sold_by",label:"Sold by",fieldtype:"Link",options:"Mobile Staff",get_query:()=>({filters:{company:phone.company || frappe.defaults.get_default("company"),enabled:1,can_sell:1}})},
        {fieldname:"rate",label:"Selling Price (PKR)",fieldtype:"Currency",default:phone.asking_price,reqd:1},
      ], async values => {
        const response = await frappe.call({method:"covison_erp.api.create_sale", args:{device:name,...values}, freeze:true});
        frappe.set_route("Form","Sales Invoice",response.message.name);
      }, `Sell ${phone.model || "phone"}`, "Create Draft Invoice");
    }
  }
}
