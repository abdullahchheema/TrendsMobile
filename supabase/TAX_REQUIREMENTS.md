# Step 2 — client tax requirements

Status: **Prepared; awaiting actual client/accountant instructions.** This worksheet records business requirements. It does not supply tax advice, statutory rates or a legal classification of a transaction.

The preview currently has **zero configured tax rules**. Its default **No tax added** means the software adds no amount; it does not establish that a sale is exempt or that the client approved that treatment. Existing invoices have not been changed.

## Details to provide

Complete each applicable row. Mark a category not applicable only when the client confirms it.

| Transaction category | Approved treatment / calculation basis | Rate, if applicable | Do displayed prices include tax? | Exact invoice label | Applicable shop codes | Client/accountant reference |
| --- | --- | --- | --- | --- | --- | --- |
| New phones | Pending | Pending | Pending | Pending | Pending | Pending |
| Used/refurbished phones | Pending | Pending | Pending | Pending | Pending | Pending |
| Accessories and spare parts | Pending | Pending | Pending | Pending | Pending | Pending |
| Repairs and service charges | Pending | Pending | Pending | Pending | Pending | Pending |
| Supplier purchases / input tax | Pending | Pending | Pending | Pending | Pending | Pending |

For any used-phone treatment calculated on a margin, specify the approved definition of the margin and the required purchase/sale evidence. A selling-price percentage rule is a different calculation and must not be substituted for it.

Also record:

- Whether an invoice may combine items with different tax treatments/rates.
- Whether withholding, special rounding, government invoice submission or additional invoice fields/reports are required.
- How discounts and partial returns should be treated in the approved calculation, with an example invoice if available.
- The name of the person providing the instructions, their reference and the effective date.
- Any explicit decision to add no tax, including the applicable transaction categories and shops.

No passwords, API keys, identity documents or bank credentials are needed in this worksheet.

## Fit with the current software

| Requirement | Current capability | Next action |
| --- | --- | --- |
| One exclusive percentage on an entire discounted sales invoice | Implemented; owner rules are scoped to a shop | Configure only after the actual rate, label, reference and shop are known |
| Approved decision to add no tax | Software can add no tax | Record the client's instruction; do not infer exemption |
| Several category rules used on separate invoices | Multiple named shop rules can be configured | Operator selects the correct approved rule; category selection is not automatic |
| Different rates/treatments on lines within one invoice | Not implemented | Implement and test if required |
| Tax-inclusive selling prices | Not implemented | Implement extraction and invoice/return calculations if required |
| Used-phone margin calculation | Not implemented | Implement the client's specified basis and evidence if required |
| Purchase/input taxes or withholding | Not implemented | Implement the required purchase, ledger and report workflows if required |
| Government invoice integration or additional mandated fields | Not implemented | Obtain the exact required integration/field specification |

Existing sales discounts are applied before the exclusive tax calculation. Submitted sales capture their original net amounts, discount, tax and output account; physical returns reverse those captured amounts. No treatment is inferred from an iPhone model, its condition, IMEI or PTA status.

## After the instructions arrive

1. Check the completed requirements against the capability table and identify any additional implementation needed.
2. Confirm the actual shop codes. The current preview contains only the imported `PK-001` shop; the client's four-shop setup is still pending.
3. Exercise the approved invoice examples and partial returns in a disposable test environment. Compare the invoice, net revenue, tax liability, customer balance and refund/credit amounts to the approved examples.
4. Once the relevant shop's POS shifts are closed, configure the supported rules through **Shop setup → Sales tax rules**, with a separate output-tax account and the client's reference. The existing legacy shift needs actual closing evidence.
5. Review the resulting invoices/receipts with the client before production acceptance. Keep the original CRM and its posted records intact until the separate cutover prerequisites are satisfied.

For a structured copy of the unanswered fields, use [tax-requirements.json](tax-requirements.json). Operating details are in the [invoice pricing guide](../offline/INVOICE_PRICING_GUIDE.md); the remaining rollout sequence is in [CLIENT_HANDOVER.md](CLIENT_HANDOVER.md).
