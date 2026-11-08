# Client handover and cutover sequence

This is the execution record for the requested six-step rollout. Complete each item with real client evidence. Preview tests do not certify the shops’ computers or network. Keep one live transaction system throughout cutover.

## 1. Accounting adjustments

The preview provides owner journals and linked reversals/replacements. Use [the journal guide](../offline/JOURNAL_GUIDE.md). Test a reclassification and a correction in the designated test environment, then check the ledger. Stock, party balances, payment custody and output tax stay in their dedicated workflows. Arbitrary posted invoice amendments are not provided by the journal form.

## 2. Approved tax treatment

Complete the [tax requirements worksheet](TAX_REQUIREMENTS.md), or its [structured copy](tax-requirements.json), with the client/accountant's actual instructions. Preparation is complete; the decisions remain pending.

Record the client’s treatment and reference for new phones, used/refurbished phones, accessories, repairs and purchase invoices. Obtain actual rate/label instructions and whether exclusive, inclusive, mixed-item, margin-based, withholding or government-invoice support is needed. Configure only supported, approved rules using [the pricing guide](../offline/INVOICE_PRICING_GUIDE.md). A deliberate no-tax decision also needs the client’s instruction; the software does not decide legal treatment.

Current implementation: one exclusive rate per sales invoice after discount. Tax remains unconfigured until actual instructions arrive. Additional required treatments must be implemented and tested before acceptance.

## 3. Actual shop configuration

Use owner **Shop setup** and [the setup guide](../offline/SHOP_SETUP_GUIDE.md). Obtain:

| Required detail | Client response / evidence |
| --- | --- |
| Four shop codes, names, addresses and business/company | Pending |
| Retain, rename or retire the imported “trends mobile” shop | Pending |
| Staff names, roles, shop assignments, login emails and warehouse defaults | Pending |
| Warehouses, tills and payment accounts/methods per shop | Pending |
| Actual opening stock/IMEIs, costs, cash and customer/supplier balances | Pending |
| Demonstration-data disposition and opening date | Pending |

Creating empty setup records does not create opening stock or money. Use actual staff logins for each role; do not share the owner login. Preserve existing posted history while agreeing a clean production-data boundary.

Run the read-only preflight after configuration:

```sh
python3 supabase/control.py exec -T crm-web python manage.py deployment_readiness
```

## 4. Equipment, LAN and recovery

Complete one [device acceptance record](../offline/DEVICE_ACCEPTANCE.md) per actual shop PC. Record server/PC OS, server CPU architecture, scanner and printer model/driver/paper. The current development image is Linux ARM64; build/load matching images for the actual server architecture. Confirm the final server address/hostname and configure HTTPS with certificate trust on each shop PC before production. Prove all four premises can reach the shared server. Internet-off operation requires the LAN and server to remain reachable; independent disconnected-shop trading is not implemented.

Record an existing independent backup destination, configure it using [the operations guide](OPERATIONS.md), then verify a copied backup’s checksums and disposable restoration. Rehearse cached-image startup, server reboot and complete recovery on the final/replacement machine, preserving private credentials and encryption keys. Do not use a same-disk second folder as evidence of independent recovery. Do not power-cycle this development computer as a substitute for a shop-server rehearsal.

## 5. Client acceptance

Use a designated disposable test environment and actual shop operators. Keep test records out of live stock and accounting. Record a dated result and operator for each case:

| Scenario | Required result | Client result / evidence |
| --- | --- | --- |
| Used-phone purchase, variants and dual IMEI | Exact device received once with actual cost, condition and PTA evidence | Pending |
| Supplier bill, part payment and settlement | Payable and responsible staff reconcile | Pending |
| POS phone/accessory sale, discount and approved tax | Correct total, stock, net revenue, liability and receipt | Pending |
| Cash/card/wallet split, cash handed and change | Only net cash enters the assigned till | Pending |
| Unpaid sale and customer advance | Debt and allocations match without extra cash | Pending |
| Partial return and refund/debt application | Original price/tax/cost reversed; no over-return or duplicate credit | Pending |
| Trade-in/exchange and warranty/repair | Correct linked documents, customer balance and stock/expense treatment | Pending |
| Inter-shop stock and internal cash/bank transfer | Correct ownership/custody; balanced ledger; no inflated sales | Pending |
| Owner journal, reversal and replacement | Original preserved; final ledger equals replacement | Pending |
| Uncertain POS result and retry | One invoice/payment/stock movement after reconnection | Pending |
| Shift cash count and daily closing | Cash difference explained; bank receipts excluded from till cash | Pending |
| Role isolation and reports | Each operator sees permitted shops/actions and reconciled totals | Pending |

Automated HTTP/browser results are supporting evidence; they do not fill the client-result column.

## 6. Final shop-server deployment

Supabase + Django serves port 8080 and its database is the authoritative source for all work. The following steps concern the final client/shop server and remain pending.

1. Agree the deployment window, data boundary, last document numbers and actual target server. Close/count tills and pause writes in the **current main Supabase CRM**.
2. Take a fresh complete Supabase backup with database, roles, files, encryption keys, protected environment and checksums. Include a matching application/runtime release for the actual target architecture.
3. Restore first into separate staging on the selected final server. Preserve authentication identities, storage and encryption keys. Review the actual LAN address, host/callback settings, HTTPS and selected release. Do not merge independently traded databases or overwrite a target already in use.
4. Reconcile per shop: exact devices/IMEIs, accessory quantities/values, AR/AP and advances/credits, cash/bank, tax, journals and till/daily closings. Record source backup hash, control totals, differences and dated acceptance.
5. Prove access from each actual shop PC, operator permissions, physical scanning/printing, independent backup copying and final-machine reboot/recovery before moving staff to the new address.
6. Make only the accepted target writable and keep the prior main installation as an archive. Check the first transactions and next closing. If the target has received no new transactions, a controlled rollback can resume the prior Supabase main installation. If new transactions exist, stop both and reconcile them before rollback; do not discard or duplicate them.

The previous Frappe application has been removed from this repository; the Supabase database is the only system of record.

Actual client details, physical acceptance, the independent backup destination and the final server remain prerequisites for shop production handover.
