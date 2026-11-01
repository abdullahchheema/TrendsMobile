# Cash handed and change in POS

The Supabase preview now records cash handed separately from the amount applied to the invoice. Existing Cash account/payment setup is used; no new accounts or opening balances are needed. Use an open, counted POS shift under the correct cashier as before.

## At the counter

1. Select the customer, seller and exact phone/accessory. Check the negotiated selling price and invoice total.
2. Select the payment method. In **Applied to invoice**, enter the amount that should settle this invoice using that method.
3. For cash, enter the customer's notes/coins in **Cash handed**. The screen calculates change. Leaving Cash handed blank means the customer handed exactly the applied amount.
4. For a bank/card/wallet payment, enter its applied amount and actual transaction reference. Cash handed/change controls are hidden and disabled for that row.
5. Use **Add split payment** for another method, or **Remove** to clear/delete an unwanted row. Verify all applied amounts, give the calculated change and complete the sale.

For a PKR 4,500 invoice, apply PKR 4,500 and enter PKR 5,000 cash handed: return PKR 500. Do not enter the full PKR 5,000 in Applied to invoice; that would overpay the invoice and is rejected. Use a separate customer advance for money intentionally retained beyond the invoice balance.

The server checks the amounts again. Cash handed must cover the applied cash, and payment/cash amounts support at most two decimal places. Bank/card rows cannot request cash change. If any payment fails, the entire checkout rolls back, including stock, invoice, payments and ledger entries.

## Examples

For an invoice totalling PKR 4,500:

| Payment | Applied | Cash handed | Change returned | Till increase | Invoice remaining |
| --- | ---: | ---: | ---: | ---: | ---: |
| Exact cash | 4,500 | 4,500 or blank | 0 | 4,500 | 0 |
| Cash with change | 4,500 | 5,000 | 500 | 4,500 | 0 |
| Partial cash payment | 1,000 | 2,000 | 1,000 | 1,000 | 3,500 |
| Cash 3,500 + bank 1,000 | 4,500 total | 4,000 | 500 | 3,500 | 0 |
| Credit sale: all payment fields blank | 0 | — | 0 | 0 | 4,500 |

**Only retained/applied cash increases the till.** Cash handed and change are handover evidence, not extra income, an expense or additional ledger postings. Bank/card amounts remain outside the physical cash count. The same fields work in the draft Sales Invoice counter checkout.

## Receipts and closing

New cash payment records preserve handed cash and computed change. Sale/payment details show the settlement. The 58 mm, 80 mm and A4 receipt layouts show cash handed, change returned, net received and invoice outstanding. Existing imported receipt payment/change information is retained.

Till reconciliation uses opening cash plus net cash received minus cash paid/refunded. Its payment details include handed cash/change where recorded, and the former ambiguous “Cash change” column is now labelled **Net till movement**. Closing does not treat a PKR 5,000 note as PKR 5,000 retained if PKR 500 was returned.

Cancelled payments are excluded from the current receipt totals and open-till balance. Their original cash evidence remains in the cancelled payment's history. Closed-shift payments retain the existing cancellation protection. Refunds continue to use their separate payment workflow; cash tender/change fields cannot turn a bank payment or refund into an extra cash withdrawal.

## Interrupted sale

Keep an uncertain-result POS page open. Its editor, cash fields and split rows freeze while the saved request is pending. Reconnect and use **Retry same sale**, which sends the identical request key, applied amounts and cash handed. If necessary, sign in in another tab first. Review recent invoices before starting a fresh sale. There is no independent offline sales queue; refreshing or closing an unsent page can discard the cart.

Browser change previews use integer paisa arithmetic; PostgreSQL/Django Decimal calculations remain authoritative. Automated browser tests use mocked checkouts. Real payment/stock/retry tests run in disposable databases. Physical cash handling and printer output still require shop acceptance. The original Frappe CRM has not been switched, and accessory-return UI/native accounting parity remains separate work.
