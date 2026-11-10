# Accounting guide (for the owner and accountant)

You do not need accounting knowledge to run daily sales. This guide covers the parts the owner or accountant uses.

## Who can do what
There are only two kinds of user.

- **Owner**: sees every shop, can create shops and their logins, and is the only one who can move stock between shops and use the
  advanced accounting items under Settings (chart of accounts, accounting adjustments, lock a month, year-end close, fixed assets,
  prepaids and accruals, loans, activity log).
- **Shop Accountant**: the one login of a shop. Sells, buys, takes and pays money, records expenses, handles returns and exchanges,
  and sees that shop's reports. A shop can never see another shop's customers, suppliers, stock, money or reports.

## Everyday money (Accounts & Finance menu)
- **Money In**: customer payments, refunds from suppliers, other income, money the owner puts in.
- **Money Out**: supplier payments, expenses, customer refunds, money the owner takes.
- **Expenses**: rent, electricity, salaries, repairs and so on, by category. Buying phones is a purchase, not an expense.
- **Customer Dues / Supplier Dues**: who owes what. Open a name and record a payment; it is applied to the oldest bills first and the
  remaining balance updates by itself. A payment is never counted as new sales.
- **Cash and Bank**: a separate balance for Cash, Bank, Easypaisa and JazzCash. Moving money between your own accounts (for example
  a deposit) is not income or expense.
- **Transaction History**: every movement in one list with filters. A cancelled entry stays visible next to its reversal so the totals
  always match the balances.

## Setting up the books
1. **Chart of accounts** (Settings > Chart of accounts): press **Install standard chart** once. It adds the usual mobile-shop accounts
   (cash, bank, JazzCash, Easypaisa, inventory, receivables, payables, owner's capital, drawings, loans, rent, salaries, electricity...).
   Existing accounts are never changed.
2. **Start with owner capital.** Cash and bank money must exist before it can be spent. Record the owner's starting cash as
   *Owner capital introduced* in **Expenses & owner entries**. Each shop's cash, bank and wallet balance is checked on every payment.

## Day to day
- **Expenses & owner entries** records rent, salaries, utilities, owner drawings, loans received and loan repayments, each paid from a
  cash, bank or wallet account. For services bought **on credit**, use a Supplier Bill so the supplier balance is tracked.
- **Stock adjustments** write off damaged or lost stock (managers only).
- **Return to supplier** (button on a supplier bill) sends phones or accessories back and reduces what you owe the supplier. If the bill was
  already paid, the supplier owes you a credit.
- **Daily closing** now books any counted cash difference to *Cash short and over*, so the ledger matches the cash really counted.

## Looking at the books (Reports menu)
- **Cash book / Bank & wallet book / Account ledger**: every posting with a running balance. Export to CSV or print.
- **Customer / supplier statements**: open a customer or supplier list and press *Statement*. Shows opening balance, every invoice and payment
  with a running balance, and how old the money owed is (0-30, 31-60, 61-90, 90+ days).
- **Profit and Loss, Balance Sheet, Cash Flow Statement**. The cash flow statement shows where cash came from and went and proves itself
  against the real change in cash and bank; any "unexplained difference" means an account needs attention.
- **Trial Balance, Unadjusted and Adjusted Trial Balance**: before and after the accountant's adjusting journals.

## Closing a period
- **Accounting periods**: lock everything on or before a date so nothing can be posted, changed or cancelled there. Only the owner can
  reopen (a reason is required and recorded).
- **Year-end close**: preview, then close. Income and expenses move into *Retained earnings*, the profit and loss for that year still reads
  correctly, and the year is locked.

## Audit log
**Audit log** (Settings) shows who did what and when: cancellations, overrides, role changes, period locks, returns and more. It cannot be
edited. Branch managers see their own shop; the owner sees everything.

## Fixed assets, prepaids, accruals and loans (Accounts menu)

All of these need the standard chart of accounts installed (Chart of accounts > Install).

- **Fixed assets.** Add an asset with its cost, scrap value and useful life. Pay for it from cash/bank, or mark it "already owned" to take it in against owner equity. Each month, open Fixed assets and press **Post depreciation**: it posts straight-line (or reducing-balance) depreciation for every whole month up to the date you pick, once only. To sell or scrap an asset, bring depreciation up to date, then use **Sell / dispose**; the gain or loss is posted for you (Other income / Other operating expenses).
- **Prepaid expenses.** Pay a larger bill now (for example a quarter of rent). It sits in Prepaid expenses and **Release prepaids** moves one month at a time into the expense account.
- **Accruals.** Record an expense you have used but not yet paid (electricity). It is booked now against Accrued expenses and reverses automatically on the date you choose; then enter the real payment as a normal expense.
- **Loans.** Record money borrowed (cash/bank goes up, the loan account owes it). Each repayment is split into principal and interest; the screen suggests one month of interest.

Journal entries posted by these screens cannot be cancelled; correct a mistake with a reversing entry. Locked periods are respected.
