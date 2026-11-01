# Owner accounting adjustments and corrections

Open **Accounting adjustments** in the sidebar using the owner login. These functions are deployed in the separate Supabase preview; no owner adjustments have been posted to its primary business data during testing.

## Create an adjustment

1. Choose **New adjustment**, select the shop and load its accounts.
2. Enter an open posting date, a supporting reference and the accounting reason.
3. Enter one row per account, with a positive debit or credit on each row. Add/remove rows as needed (2–30 completed rows). Amounts use two-decimal PKR and the debit total must equal the credit total.
4. Save the draft. Review the account names, amounts, reference, date and reason on its detail page, then submit.

The form supports accounting reclassification using permitted income, expense, equity and non-control current asset/liability accounts. Stock valuation, stock-received clearing, receivables/payables, cash/bank/wallet accounts, inter-shop clearing and configured output-tax accounts are excluded. Another shop’s mapped income/expense accounts are excluded. The server rechecks account roles and active status at posting.

Customer/supplier balances and advances, inventory/IMEIs, payments, till cash, sales-tax treatment and bank transfers use their existing transaction workflows. A journal neither moves a phone nor pays an invoice. Device profit/economics and staff net-sales reports retain their invoice basis; general ledger reports include owner accounting reclassifications separately.

## Correct a posted owner journal

1. Open its detail and choose **Prepare reversal**. The system prepares a new draft, preserving the original posted document and ledger.
2. Supply a new supporting reference and explain the reversal. Review the current open posting date. The original accounts/amounts are locked in reverse; submit the reversal.
3. If a corrected entry is required, return to the original and choose **Prepare replacement after reversal**. Review/edit the replacement rows, add its supporting reference/reason and save/submit it.
4. Check all three linked records and their general-ledger effect. Original + reversal cancel; the replacement supplies the corrected result.

Preparing a correction twice reuses its pending draft. Only one posted reversal and replacement are allowed for each original. A replacement can itself be corrected through another linked chain. Reversal documents are not directly reversed; prepare the corrected replacement from the original instead. A closed original accounting date stays closed: the new correction must post in an open period.

Submitted owner journals cannot be edited or cancelled in place. Imported/system journals, invoice history and return-credit allocation journals cannot be repurposed through this form. Unsupported posted invoice/payment amendments require their dedicated review/workflow; this journal feature does not implement arbitrary rewrites of those records.

Draft saves, correction preparation and posting are audited. Request keys protect retries. If an operation has an uncertain result, review the journal history/detail before starting a new adjustment. Owner-journal pages and actions are restricted to the owner; ordinary sales/payment staff keep access to their existing workflows.
