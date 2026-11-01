# Applying the tested work to the main CRM

Initial read-only assessment completed on 31 October 2026. The user subsequently selected Path A, and the guarded local main-address promotion completed. See [the switch record](artifacts/main-crm-promotion.json). Business records and tax settings were preserved.

## Current installations

| Installation | Address / exposure | Current role |
| --- | --- | --- |
| Original Covison CRM on Frappe / ERPNext | Archived; application stopped | Preserved original and rollback backup |
| Supabase + Django Covison CRM | `http://localhost:8080/desk/mobile-shop` and LAN port 8080; 8081 alias retained | Selected main CRM |
| Older standalone Django prototype | No published host port detected | Retained development prototype; not the selected deployment |

The original was running during the initial read-only assessment and is now backed up and stopped. Its eight inspected inventory/accounting totals match the earlier migration manifest: device/IMEI counts, stock quantity/value, ledger debit/credit and customer/supplier invoice outstanding. These aggregate comparisons do not establish a fresh full-record reconciliation or authorize a production switch.

## Path A — use Supabase + Django as the main CRM

This follows the backend architecture previously selected for the project. The current implementation is a separate application; it must be promoted through a controlled migration rather than installed as a Frappe module.

1. Preserve current installations and backup/rollback materials.
2. Complete actual shop/staff setup, the demo-data boundary, client tax requirements and required feature acceptance. Current preflight is not production-ready.
3. Include the original standalone tax-template configuration in the fresh migration export. The updated exporter now includes standalone Sales/Purchase Taxes and Charges Templates alongside invoice tax rows. Preserve those templates as source evidence and retain their active/inactive states; do not silently enable profiles in the replacement.
4. During the agreed trading pause, make a fresh consistent export and import into separate staging. Verify all required records, stock, balances, operator access, original references and approved tax calculations. Do not merge independently traded databases or overwrite the current preview from an unreviewed export.
5. Complete final-server/HTTPS, four-premises LAN, hardware, independent backup/recovery and client acceptance checks.
6. Switch staff to the accepted replacement and keep the original read-only, with the rollback procedure and transaction boundary recorded.

See [the handover sequence](CLIENT_HANDOVER.md) and [current rollout findings](artifacts/ROLLOUT_STATUS.md). The forms in the replacement are not a claim of pixel-identical Frappe screens or unrestricted native accounting parity.

## Path B — retain the original Frappe CRM and configure tax there

The installed Sales Invoice and POS Invoice definitions already contain tax-template, tax-table, amount/percentage discount and discount-basis fields. This path keeps the original backend and uses its native invoice configuration.

The source contains four sales-tax templates: one enabled **Pakistan Tax - TM** template (not marked as default) and three disabled **US ST** templates. Their stored details are recorded in [the assessment](artifacts/real-crm-assessment.json). These are existing source settings, not evidence of current client-approved tax treatment.

Review the client's instructions against the original setup, back up the original, and validate invoice/receipt/return examples in a separate test copy before changing defaults or enabling rules. Do not copy Django transaction code into the Frappe app. Broader Supabase-only features would need a separately scoped Frappe implementation if this path is selected.

## Decision needed

The user selected **Supabase + Django as the main CRM**. The local address switch is complete; Frappe is preserved for guarded rollback. Tax rates, invoice labels and price-inclusion treatment still need actual client instructions for either path.
