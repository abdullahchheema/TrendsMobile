# Sales discounts and tax

Available in the separate Supabase + Django preview.

## Enter a sale

1. Open **Point of Sale**, or create a draft under **Sales & Returns**.
2. Enter the phone/accessory quantities and unit selling rates as usual.
3. In **Invoice pricing**, choose a **PKR amount** or **Percentage** discount. It applies to the whole invoice before tax. A percentage can be 0–100; an amount cannot exceed the subtotal.
4. Leave **Sales tax** at **No tax added**, or select an owner-configured, client-approved rule. The system supplies no default tax rules or statutory rates.
5. Check the subtotal, discount, tax and total. POS previews the calculation; the server validates and posts it. Ordinary invoices show the saved calculation on their detail page before submission.
6. Record payment against the final total. **Cash handed** can exceed the net cash payment; the difference is change. An unpaid balance remains on the customer’s account.

A salesperson cannot use a header discount to bypass a phone’s minimum selling price. The minimum is checked against that phone’s allocated revenue after discount and before tax. A manager can approve a lower price by posting the sale under their own login.

If a sale result is uncertain after a connection failure, keep the page open and **Retry same sale**. The items, discount, tax-rule version and payments remain frozen together. A changed tax rule requires reloading/reviewing the price and saving the draft again; it cannot silently change a pending sale.

## Owner: configure a client-approved tax rule

1. Obtain the client’s approved invoice treatment, rate and label. No actual client tax instructions have been supplied yet, so the primary preview starts with **zero configured rules**.
2. Close the shop’s POS shifts.
3. Open **Shop setup → Sales tax rules**. Create a separate active **Tax** or **Current Liability** account if needed. It cannot also be a shop’s stock, sales, party or payment account.
4. Choose **Add rule** for the relevant shop. Enter its invoice label, exclusive percentage, output-tax account and the client’s instruction/reference. Rates allow up to four decimal places; posted money uses two.
5. Save and review the rule. A change affects future sales; submitted invoices and returns retain the original amounts, label, rate and output account. Disable a rule to stop selecting it for new sales rather than deleting its history.

Rules apply one exclusive rate to the entire discounted invoice. There is no automatic determination of taxable goods, used-phone margin taxation, inclusive prices, mixed item rates, withholding, purchase/input tax or government e-invoicing. Arrange a separate implementation and review if the client needs those treatments. An unset tax rule means only that the software adds no tax; it is not a legal classification of the sale.

## Returns and reports

Return physical items from the **original invoice** using the existing return form. Do not enter a fresh negative-price sale. Each credit reverses the original line’s allocated discount, net revenue, tax and stock cost. Partial accessory returns use cumulative rounding, so returning the entire quantity restores the original paisa exactly, even after prices or tax settings have changed.

Apply a return credit to any unpaid amount on the original invoice first, then record any remaining cash/bank refund separately. A credit alone does not remove cash from a till.

- Receipts (58 mm, 80 mm and A4) and invoice detail show subtotal, discount, net sales before tax, tax and total.
- **Sales Register** separates those amounts from the customer’s outstanding balance. **Staff Summary**, **Device Profit** and **Mobile Device Economics** use captured revenue after discount and before tax for new sales.
- **General Ledger** credits net sales and the separate tax liability; receivables and payments use the customer’s full total. Discounts and sales tax do not alter stock purchase cost.

Imported posted history remains read-only. Historical adjustments without a reconciled pricing snapshot still require a reviewed credit calculation. No sample tax rules or priced sales were added to the primary preview; synthetic rates were used only in disposable QA databases.
