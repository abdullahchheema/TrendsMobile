# Phone and accessory returns

The Supabase preview supports partial accessory quantities as well as exact-phone returns. Open **Sell / POS → Sales & Returns** and choose the original submitted invoice. This history now includes POS invoices too. A return must remain in the original shop and be linked to the original customer and invoice lines.

## Receive the items

1. Open **Return phones or accessories** on the original invoice. Each row shows sold, already-returned and remaining quantities, plus the original unit price.
2. Select each exact phone once, or enter an accessory quantity against the correct row. The same accessory may have separate rows at different prices; choose the row the customer bought. Accessory quantities support up to three decimal places.
3. Select an active receiving warehouse in that shop. Use the appropriate inspection/quarantine location if the goods are damaged; returning goods does not certify their condition or PTA status.
4. Record the received condition/accessories and the reason. **Create draft return** saves a draft without posting stock or paying money.
5. Review the credit note. Its customer, invoice, product identity and unit price are protected. You can edit the receiving warehouse, date, notes and accessory quantity, or remove an unwanted line. Phone quantities stay at one. No unrelated items can be added in this edit form.
6. **Submit** after checking the physical items. Submission restores stock at the original invoice-line cost and issues the return credit. It rechecks quantity limits against all submitted returns, so an older draft cannot over-return stock after another return has posted.

The phone keeps its original IMEIs, PTA and warranty snapshot. Its status returns to Available with inspection Pending; inspect it and review pricing before resale. The original seller's historical name is retained, while the return records the staff/login who posts it. Refunds separately record the employee handling the payment.

## Settle the return credit

Stock return, credit application and cash refund are separate actions:

- **Original invoice still unpaid:** a cashier/manager uses **Apply credit to original invoice**. This reduces the invoice debt and uses the return credit without moving stock or cash again.
- **Credit remains after that debt is cleared:** choose **Record refund**. A Payment Entry opens with the original customer, return-credit reference, Pay direction and remaining credit amount. Check the handler, actual payment method and amount; record a bank/card/wallet reference where required. Save and submit it.
- **Original invoice already fully paid:** record the refund directly from the posted return credit. Partial refunds are supported. More than the remaining credit is rejected.

Example: an invoice is PKR 1,000, the customer paid PKR 700, and returned items are worth PKR 500. Apply PKR 300 of credit to the unpaid balance; refund the remaining PKR 200. The customer has then paid PKR 500 for the goods they kept. Credit application creates a balanced receivable adjustment, without another cash receipt or payment.

The system blocks a refund while the original invoice still has an unpaid balance. A return never pays cash automatically. Cash refunds during an open shift reduce that till's expected cash; bank/card refunds do not affect the physical cash count. The selected refund method must reflect what was actually paid.

## Receipts, reports and history

Print the return credit note in 58 mm, 80 mm or A4 format. It shows returned quantities and amounts, remaining credit, actual refunds, and any credit applied to the original invoice. The original sale receipt also identifies applied return credit. Sales/receivable/stock/general-ledger reports use the posted credit and movements; refund payments appear in payment/staff/till records.

Original unit prices and costs are used, even if current selling prices or weighted stock costs have changed. Fractional return amounts preserve cumulative rounding so the final return does not lose a paisa. Each new return line stores its original invoice-line link. Existing return history without this link is read without rewriting it; price/cost/quantity ambiguity requires review.

Creating the draft and applying credit use the existing request-key protection against duplicate submissions. Concurrent returns are checked again in the database. Failed submissions roll back the entire operation. Posted returns and credit applications require accounting/stock review for corrections; there is no in-place cancellation or automatic amendment for this flow.

If an original invoice's total does not match its captured line amounts because of taxes, discounts or other adjustments, the standard return flow requests a reviewed credit calculation. Native invoice tax/discount and correction parity is still pending. This flow handles physical stock items, not service-fee credits or supplier returns.

The original Frappe CRM has not been switched. Actual shop setup, physical handover/printer/LAN checks, independent backup copying and final-server recovery/cutover acceptance remain separate tasks.
