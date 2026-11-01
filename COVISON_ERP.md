# Covison ERP mobile shop customization

The local application is available at http://localhost:8080/desk/mobile-shop.
This section describes the original Frappe site. The separate shared-LAN Supabase/Django preview is documented in [supabase/README.md](supabase/README.md); no cutover has been performed.

## Included

- Covison ERP login, application title, logos, navigation and receipt branding.
- Mobile workspace with individual-device and model/variant views, search and filters.
- Mobile Model masters, storage/color/RAM variant attributes, and mobile-specific Item fields.
- Native ERPNext serial tracking and serial-specific inventory valuation.
- One Mobile Device per physical phone, with dual IMEI, inspection checklist, battery health,
  carrier/activation lock, PTA evidence, condition, accessories and warranty fields.
- Separate asking, wholesale and manager-controlled minimum prices for each phone.
- Exact-device invoice selection; valid unique IMEI required before selling.
- Duplicate IMEI protection across both SIM slots and all device records.
- Native POS retained with a dedicated POS dock shortcut and prominent Open POS buttons. Retail domain is active.
- POS reservations for deferred posting, while retaining the existing immediate Sales Invoice mode.
- Stock and posted device-margin reports; sales returns reverse the original margin.
- Branded invoice/return receipts with device details, payment and balance.
- Cash, bank transfer, Easypaisa, JazzCash and card payment records. Wallet methods use dedicated
  ledger accounts; no external payment-provider connection or online PTA integration is installed.
- Guided **Buy a Phone** intake, including seller, inspection, actual IMEIs, PTA results, costs and prices.
  Submitting receives one exact phone through a native Purchase Receipt. Supplier Bill creates an
  idempotent draft Purchase Invoice for review and payment.
- Counter Checkout posts a reviewed phone Sales Invoice and actual split payments through native
  Payment Entries, including partial/credit sales and required non-cash transfer references.
- Exchange settlement links the trade-in customer, submitted receipt, supplier bill and outgoing sale.
  It offsets outstanding receivable/payable through a Journal Entry and records the remaining actual
  difference through a Payment Entry. Both directions and equal-value trades are supported.
  Posted settlement references cannot be edited; reversing a settlement flags it for review.
- Daily Closing reconciles configured company cash, bank and wallet accounts against actual balances.
  Shared accounts appear once. POS shifts must be closed first. Differences require explanations.
  The closing is a dated snapshot, not an automatic adjustment; later backdated postings can change balances.
- Exact-phone returns create native draft credit notes and require returned condition and reason.
  Submitted returns restore stock and reset inspection to Pending. Native payment allocation handles
  credits/refunds; warranty claims link original sale, return, replacement and resolution.
- Repair / service costs create non-stock supplier bills tied to the individual phone. Device Economics
  counts posted repair expenses once, separately from capitalized valuation. Native invoice returns reverse costs.
- Stock Age & Prices flags missing asking prices and slow stock (default 30 days, configurable).
  Customer and supplier outstanding reports use native invoice/payment allocations.
- Separate PTA results/evidence for both IMEIs, regional version and seller-market descriptions.
  Official PTA verification link is provided; verification remains manual.
- Shop Settings controls receipt address/contact, checking-warranty terms, optional Urdu note and stock-age threshold.
  Device faults/warranty details and receipt terms are snapshotted onto invoices. Receipts show split payments.
- Staff directory with names, designation, company/shop, responsibilities and optional login link.
  Purchased-by, sold-by, payment collector/handler and closing staff are tracked independently of the
  login posting the transaction. Names are snapshotted onto posted documents and receipts.
- Staff Activity and Staff Summary reports distinguish net device purchases/sales from money collected
  and paid. Supplier bills do not duplicate purchase receipts; consolidated POS invoices do not duplicate
  original POS sales. Accounting offsets and internal transfers are excluded from collection/payment totals.
- Staff role profiles. No existing user has been assigned additional roles automatically.

## Initial stock migration (historical)

The 30 phones across 15 iPhone variants were converted to serial tracking by cancelling and
amending the five active PKR purchase receipts. The purchase orders, suppliers and quantities
were retained. Quantity remains 30 and value remains PKR 2,441,862.

Internal identifiers COV-PHN-000001 through COV-PHN-000030 are stock identifiers, not IMEIs.
Actual IMEIs, battery data, verification results and device selling prices must be entered.
At the initial migration, no invented IMEI, battery percentage, PTA approval or selling price was added.
A later, explicitly requested demonstration import now provides synthetic IMEIs and sample prices.
See the current demonstration dataset below; actual physical-device details still require verification.

US state tax templates are disabled and no tax rate has been assumed for this client. Confirm
the client's applicable taxes and warranty terms before using the system for live business.
The main dock now opens the grouped Mobile Shop sidebar: Dashboard, Phones, Buy, Sell/POS,
Exchanges, Customers & Suppliers, Payments & Expenses, Warranty, Reports and Settings.
Manufacturing, Projects, Assets, Quality, CRM, generic Support and the general ERP workspace shortcuts
are hidden. Their records and permitted native forms remain accessible through search.
Shop Settings is restricted to managers. Optional customer repair job cards, instalments, commissions,
FBR integration and four-shop offline synchronization are not enabled.

## Build and start

```sh
python3 scripts/build_covison_app.py
docker build --pull=false -f images/covison/Dockerfile -t covison/erpnext:16.52.1-v1 .
docker compose -f pwd.yml -f compose.covison.yaml up -d
docker exec erp-mobileshop-main-backend-1 bench --site frontend install-app covison_erp
docker exec erp-mobileshop-main-backend-1 bench --site frontend migrate
```

Use **both compose files** when starting or recreating this existing site. The Covison image
contains the custom app and its static asset link in every application container. The database
and site files use the existing persistent volumes. Starting an image without the installed app will
fail to load this customized site. Do not remove volumes during normal maintenance.

The initial stock conversion is an explicit one-time operation:

```sh
docker exec erp-mobileshop-main-backend-1 bench --site frontend execute covison_erp.migration.enable_existing_device_tracking
```

It is guarded against reruns once device records exist. It must not be repurposed for client
inventory without reviewing its fixed preconditions and taking a new backup.

## Verification

Run transaction checks only on a separately restored QA site; the runner rolls back test records:

```sh
docker cp tests/covison/. erp-mobileshop-main-backend-1:/tmp/
docker exec erp-mobileshop-main-backend-1 bench --site covison-qa.local set-config covison_qa 1
docker exec erp-mobileshop-main-backend-1 bench --site covison-qa.local execute 'exec(open("/tmp/verify_full_crm.py").read(), {})' 
```

The final installed image passed 27 integration checks on 2026-10-28, including both exchange
payment directions, equal-value trades, split/partial checkout, intake cancellation/amendment,
exact-device returns, cost visibility, cashier posting permissions, daily closing and repair-cost accounting.
After rollback, all 30 original phones remained available at PKR 2,441,862, with no test transactions left.
Login and custom asset endpoints returned HTTP 200.

Transaction fixtures now refuse to run on the live `frontend` site. Use a disposable QA copy with
`covison_qa` enabled. The current whole-CRM runner is `tests/covison/verify_full_crm.py`; copy all files
from `tests/covison/` into `/tmp/` in the backend container, then execute that runner on the QA site.
It deduplicates inherited test cases and uses native Frappe test mode for rollback-safe POS closing.
Staff defaults and copied open shifts are reset inside each test transaction, then rolled back.
The fixtures still require the original 30 unsold phones and are not a general live-business test dataset.
See `artifacts/qa/QA_REPORT.md` for the latest whole-CRM results, limitations and data-readiness findings.

The upstream ERPNext and Frappe source/license files are retained. Branding is applied through
the custom app and supported settings; core application code is not modified.

## Counter staff workflow

1. **Buy → Buy a Phone:** choose the actual supplier, warehouse and variant, then complete inspection,
   actual IMEI and agreed PKR cost. Submit to receive. Use Supplier Bill, review/submit it, then record payment.
2. **Dashboard → Sell:** find or scan an IMEI, choose the customer and negotiated price. Review the draft
   invoice and disclosed condition. Counter Checkout records actual payments and posts the sale.
   A blank payment table leaves the sale on credit; native Payment Entry records later receipts and advances.
3. **Exchange:** receive the incoming phone with source Customer Trade-In and the correct customer.
   Submit its supplier bill and the outgoing sale, link them in Phone Exchanges, then use Settle Exchange.
   For a non-cash difference, enter the real transfer reference. Do not also pay the same difference manually.
4. **Warranty / Return:** open the original sale, choose Return a Phone and record condition/reason.
   Review and submit the credit note; record any cash refund through the native payment workflow.
   Use Warranty Claims for the complaint and resolution, linking any return and replacement sale.
5. **Daily Closing:** finish POS shifts, load company account balances, enter actual cash / statement
   balances, explain differences, and submit. A manager reviews discrepancies; no balancing entry is invented.

Before customer handover, fill actual shop contact/terms, configure appropriate taxes with the client's
accountant, assign individual staff users to role profiles, and replace/verify the sample inventory details.
The automated tests validate transactions and permissions; browser visual QA was unavailable in this session.

## Staff names and responsibility

Open **Staff → Staff Names** (or the Staff names button on the dashboard). A manager adds the actual
name, company and optional shop, then ticks Sells Devices, Buys Devices and/or Collects / Pays Money.
Link an existing login to select that staff member automatically. This directory does not create user
accounts or grant system permissions. Deactivate a staff record rather than deleting its history.

- **Buy a Phone / Purchase Receipt / Supplier Bill:** Purchased by identifies the buying staff member.
  Guided intake carries that staff link into the receipt and supplier bill.
- **Sales / POS:** Sold by identifies the seller. Payment collected / refunded by identifies the counter
  handler for native POS payments. POS exposes both names under **Additional Information** on the payment screen.
- **Counter Checkout:** choose seller and collector separately; split Payment Entries use the collector's name.
- **Payments / Advances / Refunds:** Payment handled by identifies the staff member handling the money.
- **Exchange settlement:** choose the staff member handling the actual difference payment.
- **Daily Closing:** Cash / account closing by identifies who performed the reconciliation.

Once active staff exist for a company, new posted transactions require an eligible staff member for their
responsibility. Drafts can be prepared before selection. Previous transactions remain unassigned unless
there is evidence of who handled them. Sample staff names were subsequently added at the user’s request;
historical purchases, sales and payments were not assigned to those sample people.

Staff Activity provides document links, named responsibility, payment method and posted/recorded login.
Staff Summary groups net purchases, phone quantities, net sales, actual collections and payments by staff.
Reports are restricted to shop managers and respect readable native documents. Report values use the
selected company's base currency. Returns are attributed to the staff named on the return transaction.
Run the additional rollback-based staff checks with:

```sh
docker cp tests/covison/verify_staff.py erp-mobileshop-main-backend-1:/tmp/verify_staff.py
docker exec erp-mobileshop-main-backend-1 bench --site covison-qa.local execute 'exec(open("/tmp/verify_staff.py").read(), {})'
```

The staff suite passed 17 checks, including linked-login defaults, independent seller/collector attribution,
purchase-to-bill propagation, POS collection totals, immutable name snapshots and manager-only reports.

## Sample staff requested for demonstration

Six active sample records are assigned to Stores - TM: Ahmed Raza (manager; buying, selling and payments),
Ali Hassan (sales and collections), Bilal Ahmed (sales), Usman Khan (purchases and payments),
Muhammad Hamza (cashier) and Ayesha Malik (cashier). These are sample identities, not verified employees.
No user accounts or login privileges were created. Staff names must now be selected when posting relevant
transactions. Replace the samples with the client's actual staff before handover.

`scripts/seed_sample_staff.py` creates these entries idempotently by company and name, preserving existing matches.

## QA corrections

- Sales returns and consolidated POS invoices preserve the original sale's device/warranty snapshots.
- Changing or clearing an IMEI clears its previous PTA result/evidence before verification validation.
- Phone POS invoices require stock tracking so shift consolidation can post the correct physical device.
- Competing checkouts keep the one-phone/one-sale rule and report a clear refresh message on database contention.

## First demonstration dataset

At the user's request, a clearly labelled sample batch now populates the mobile-market workflows and
all 13 shop reports. There are 36 phone profiles with 46 synthetic IMEIs, 31 available phones valued at
PKR 2,426,713, six new intakes, eleven goods bills, two repair bills and eighteen payments. Six sales,
one return/refund and one settled exchange provide profit, customer-balance and staff-activity examples.
All PTA results remain Not Checked. Original stock was not received twice.

The dashboard and sales receipts display demo labels. Historical purchase dates now carry through
Mobile Intake into Purchase Receipt, and native stock revaluation reconciles the older sample purchases.
One separate demo POS shift is closed; the shop's existing shift is preserved. Daily Closing is a draft preview.

See [dataset details](artifacts/demo_data/DATASET.md), `scripts/seed_mobile_demo_dataset.py` (explicit/idempotent)
and `scripts/verify_mobile_demo_dataset.py` (read-only seeded-state checks). The earlier QA snapshots document
the pre-demo 30-phone state and must not be treated as the current inventory totals.

## Complete section demonstration

The follow-up batch `COV-DEMO-SECTIONS-20261029-B` fills every visible Mobile Shop and Point of Sale section.
There are now 39 phone records (33 available, six sold), 52 synthetic IMEIs, new/used/refurbished phones,
accessories, spare parts, supplier purchases, sales, linked customer/supplier contacts and demo addresses,
advances, seven sample staff, warranty cases in three statuses, a display-counter stock transfer,
receipt settings and a prepared purchase draft. Device inspection results are explicitly simulated;
PTA status remains Not Checked. No actual staff login accounts were created.

The historical daily closing is submitted for a date before the existing open POS shift. The current
shift remains open and its daily closing preview remains a draft. All 13 reports contain data.
Available phone stock reconciles to PKR 2,563,713; stock including accessories/spares totals PKR 2,593,163.

See [section coverage](artifacts/demo_data/sections/SECTIONS.md) and its record references/verification.
The explicit idempotent importer is `scripts/fill_demo_sections.py`; `scripts/verify_demo_sections.py`
checks this exact snapshot. `scripts/check_demo_draft_rollback.py` tests the prepared intake only on
an isolated `covison_qa` copy and rolls back the transaction. Importers never run automatically on migration.
Demo provenance now carries through related invoices, receipts, stock transfers, payments and returns.
# Supabase backend preview

The new, separate backend uses self-hosted Supabase with a Django transaction API on one shared LAN server.
See [server setup and verification](supabase/README.md). The existing Frappe/MariaDB CRM described below remains intact;
no cutover has been performed.

