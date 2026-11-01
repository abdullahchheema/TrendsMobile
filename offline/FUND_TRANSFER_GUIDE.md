# Cash, bank and wallet transfers

Use **Cash & bank transfers** to record a completed movement between two payment accounts in the same shop: a bank deposit from cash, a bank withdrawal into a till, or a transfer between bank/card/wallet accounts such as JazzCash and Easypaisa.

This records the accounts; it does not instruct a bank, wallet service or cash drawer to move money. Use the actual transaction reference and amount. Customer collections, supplier payments, expenses and stock transfers retain their own workflows.

## Record the movement

1. Open **Cash & bank transfers → New transfer**. Select the shop and load its configured accounts when needed.
2. Choose the source and destination payment accounts, date, amount, responsible staff member and actual transaction reference. Both accounts must be active, configured for that shop and different underlying accounts. Two labels pointing to the same bank account cannot be transferred between.
3. Select the cash custody:
   - **My open POS till:** cash enters or leaves your own counted shift. The handler must be its cashier and the date must match the shift. A cash deposit cannot exceed the cash available in that till.
   - **Shop cash outside all tills:** a manager records treasury cash that is genuinely outside every POS till and explicitly confirms that custody. It does not change any cashier's count.
   - **Bank/wallet only:** no physical cash or POS till movement. The system classifies bank-to-bank transfers this way.
4. Add the reason/completed-movement details and save the draft. Review its route, accounts, reference, amount, handler and custody.
5. Submit to record both ledger sides together. The amount is credited to the source account and debited to the destination. No customer/supplier balance, sales income, inventory or extra cash receipt is created.

Payment/collection staff and managers can use this workflow in their permitted shop. Only managers can confirm cash outside tills. Opening a draft or saving it does not post any ledger movement.

The amount is the principal transferred between the two accounts. Record a bank/wallet fee separately through the expense workflow. Cash-to-cash custody between drawers and transfers between different shops are outside this account-transfer flow. Use the appropriate owner-reviewed workflow for those cases.

## Till example

A till has counted opening cash of PKR 1,000. Depositing PKR 500 into the bank records:

| Record | Effect |
| --- | --- |
| Cash ledger account | Credit 500 |
| Bank ledger account | Debit 500 |
| Expected cash in that till | 500 |
| Customer invoice/stock/sales income | No change |

Withdrawing PKR 500 from the bank back into that till brings its expected cash back to PKR 1,000. Bank-only transfers and confirmed outside-till cash movements do not change this count.

Till closing shows internal cash/bank movements separately from customer bank/card receipts. The cash received/paid columns include actual custody movements so the expected cash remains opening + cash in − cash out. Historical closed counts are not rewritten.

## Correct a completed transfer

Completed transfers cannot be cancelled in place. After physically arranging the opposite movement, open the original record and choose **Prepare reverse transfer**. This creates a linked draft with the original amount and accounts reversed. Supply the new actual transaction reference, current date, handler and correct cash custody before saving and submitting it.

Preparing a reverse draft does not move funds. Repeated preparation reopens the same pending draft; only one posted reverse is allowed for each original movement. If the original till is already closed, a new till movement uses the current open shift and preserves the old counted snapshot. Original imported transfer history is read-only and needs a reviewed new correction rather than this automatic reverse-draft action.

## Reports and safeguards

- **Transfer history:** source/destination, date, shop, amount, handler and status.
- **General Ledger / daily closing:** both account entries and resulting balances.
- **Staff Activity / Staff Summary:** internal movements are recorded separately under **Internal Transfers Handled**, without increasing Money Collected, Money Paid / Refunded or Net Sales.
- **Payment Ledger / receivables / payables:** this party-payment report excludes internal movements; customer and supplier balances are unchanged.

The posting service revalidates roles, accounts, date, cashier, references and till funds. If account mappings or the current till shift changed since a draft was saved, edit and save it again to review the accounts and cash custody. Failed validation rolls back both sides. Request keys protect repeated saves/submissions, and concurrent deposits cannot both withdraw the same till cash.

The feature is deployed in the separate Supabase preview. Existing business records and the original Frappe deployment remain unchanged. Native invoice tax/discount options, journals/amendments and final shop/server acceptance still require work before cutover.
