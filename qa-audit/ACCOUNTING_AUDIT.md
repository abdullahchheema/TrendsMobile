# Accounting audit

Scope: whether the software posts what it says it posts, whether the books reconcile, and whether the reports say what the ledger says. **Not in scope, and not certified here:** whether the accounting policies are the right ones for this business or satisfy Pakistani tax law. Those are listed at the end for the owner's accountant and tax adviser. A system can be arithmetically right and still use a policy that a professional would change.

Status words: PASS = executed and verified in this audit; NEEDS BUSINESS DECISION = the software behaves consistently but the policy is not confirmed; NOT TESTED = not executed.

## 1. How the books are built

* One ledger table (`LedgerEntry`: shop, account, optional party, debit, credit, date, document). Every balance, statement and dues list is read from it; nothing is stored twice except `Document.outstanding` (kept in step in the same transaction and reconciled by invariant F6/F7).
* Each shop has its own posting accounts (receivable, payable, inventory, stock received not billed, sales, cost of goods, expense, clearing, and one account per payment method), created with the shop. A business-wide standard chart (`COA-1000` to `COA-6950`) holds capital, drawings, loans, fixed assets, prepaid, accruals, tax payable and the expense categories.
* The Online Store is a shop without stock, with its own accounts.
* A posting is one database transaction: lock the shop, lock the document, check the date is open, write the rows, check debits equal credits, write the audit row.

## 2. Double entry · PASS

| Check | How | Result |
|-------|-----|--------|
| Every document's rows balance, in total and within each shop's books | invariant F1 after every scenario, after the load test (11,750 rows) and in the full-period dataset | PASS |
| An unbalanced posting is refused | a posting forced one paisa out of balance (`FailureHalfWay.test_an_unbalanced_posting_is_refused`) | PASS: "The transaction does not balance. Nothing was posted." |
| A failure half way leaves nothing | the second ledger insert of a sale made to fail; a sale with a second payment by a method the shop does not have; a purchase with an invalid payment | PASS: no document, ledger row, stock movement, customer or audit row remained; the phone stayed Available |
| No negative or two-sided rows | invariant F1b + database constraint `ledger_nonnegative` | PASS |

Transactions verified with their postings, dates, shop and source document: cash, bank and wallet sales; split payments; credit and part-paid sales; phone and accessory purchases (cash, credit, part paid); supplier payments; customer receipts and advances; expenses; refunds; sale returns (full, partial, with discount and tax); supplier returns; stock adjustments (loss and gain); transfers between shops (dispatch, receipt, discrepancy, write-off, return, cancel); owner capital; drawings; other income; loan received and repaid; fixed asset purchase, depreciation and disposal (gain and loss); prepaid release; accrual and reversal; online sale, delivery, failed delivery, return and settlement with the shop; cancellation reversals.

Not exercised by the audit's own tests (covered only by the existing suite): the manual journal entry screens (`tests_journals.py`), internal cash/bank transfers through their own screen (`tests_funds.py`; the audit used `money.move_money`, the same posting), warranty claims.

## 3. Chart of accounts · PASS with notes

| Check | Result |
|-------|--------|
| Every standard account has a section (Asset, Liability, Equity, Income, Expense) consistent with its type and its parent | PASS (enforced by `Account.clean`; read in full) |
| Capital and drawings are equity; loans are liabilities; tax payable is a liability; customer advances are a liability | PASS (`FullPeriod` asserts capital is not income, drawings are not expense, the loan is not income; an advance does not move the sales account) |
| An account with postings cannot change code or type or be disabled; posting accounts are locked once a shop has traded | PASS (code read; covered by existing `tests_chart.py`, `tests_setup.py`) |
| Accounts with a balance but no section | none (invariant via `books_check.unclassified_accounts`) |

Notes:
* `Accumulated depreciation` (COA-1590) is classed as an Asset with a credit balance (a contra-asset). The balance sheet therefore shows fixed assets net, which is correct; it is simply not labelled "contra".
* Stock losses are posted to the **shop's own "Shop / repair expenses" account**, not to `COA-5200 Stock losses and write-offs` (which only transfer write-offs use). The profit is right; the split between "repairs" and "losses" in the expense report is not. NEEDS BUSINESS DECISION (cosmetic classification).
* The standard chart has `COA-1200 Mobile phone inventory`, `COA-1300 Customer receivables`, `COA-2100 Supplier payables`, `COA-4100 Mobile phone sales`, `COA-5100 Cost of goods sold` which are never posted to (each shop uses its own accounts of the same nature). Unused, harmless, potentially confusing.
* Delivery income and goods income of the Online Store go to one sales account; the Online Dashboard separates them by reading the invoice lines.

## 4. Trial balance · PASS

Full-period dataset (`TEST_DATA_DESIGN.md`): the audit aggregated the raw ledger rows itself (`oracle.trial_balance`) and compared, account by account, with the application's Trial Balance report.

* total debits = total credits: PASS
* every account's closing balance in the report = the audit's own aggregation: PASS
* every balance = the figure worked out from the inputs (cash, bank, receivables, payables, inventory, tax payable, loan, capital, drawings, fixed asset, accumulated depreciation, prepaid, accrued, stock loss): PASS, 14 of 14 (see `test-results/calculations.csv`)

A balanced trial balance is not proof by itself; the 14 independent balances and the subsidiary reconciliations below are.

## 5. Profit and loss · PASS

Independently: income = phone sales + accessory sales net of discount and of the return + other income; cost of goods = phone costs + accessories at moving average, less the return; expenses = rent + salaries + loan interest + depreciation + prepaid released + stock loss (the accrual and its reversal cancel). Compared with: the raw ledger by section, the Profit and Loss report and its summary, and the amount printed on the report page. All equal to the paisa. The statement uses posting dates within the period and ignores year-end closing entries; drafts never reach the ledger (invariant F3); a cancelled document and its reversal net to zero (books check).

## 6. Balance sheet and cash flow · PASS

Assets less liabilities less equity (including unclosed profit) = 0. Total assets = cash + bank + receivables + inventory + fixed asset net of depreciation + prepaid, each independently computed. The cash flow statement's "unexplained difference" is 0 and its closing cash equals the ledger.

## 7. Cash and bank · PASS

| Check | Result |
|-------|--------|
| Each payment method's balance (ledger) = the Cash and Bank screen = received less paid in Transaction History | PASS |
| A split payment lands in exactly the accounts named, for the amounts named | PASS |
| A payment moved its amount through a cash or bank account exactly once (invariant F5) | PASS |
| Till: expected cash = opening float + cash received - cash paid; a difference must be explained | PASS (5,000 + 60,000 - 1,500 = 63,500; counted 63,000; difference -500 recorded) |
| Daily closing: every cash and bank account counted; the difference is booked to Cash short and over and the cash account then equals the count; one closing per day; nothing posts on a closed day | PASS |
| Money cannot leave an account that does not hold it | PASS for every payment out since decision D8 (14 Nov 2026): purchases at the Buy screens, supplier bills, exchange differences, expenses, drawings, refunds, settlements. Still possible: moving money between a shop's own accounts can take one below zero (follow-up) |

## 8. Customer receivables · PASS

Credit sale increases the receivable; a part payment leaves the exact remainder; full payment clears it; payments go to the oldest invoice first; money beyond the invoices is an advance (a liability, not income); a return first reduces what the customer owes and can never be refunded while they still owe; a refund can never exceed what is owed back; instalments add up to the balance and overdue follows the dates. The dues screen total = the customers' ledger balances = the control account (invariant F6). A customer belongs to one shop (invariant S1; cross-shop access refused, `SECURITY_AUDIT.md`).

## 9. Supplier payables · PASS

Credit purchase increases the payable; part payment leaves the remainder; paying one supplier never touches another; a supplier return reduces the right bill and, if the bill was already paid, leaves a credit the supplier owes (refund or keep). The dues screen = subsidiary = control account (invariant F7). Supplier ageing by bill date is shown by the Accounts Payable report (opened, not recalculated: NOT TESTED beyond opening without error).

## 10. Inventory valuation · PASS

| Check | Result |
|-------|--------|
| In every shop, the sum of stock movement values = the inventory account (invariant I12) | PASS in every scenario and after the load test |
| Phones are carried at their own purchase cost from receipt to sale, through transfers and returns | PASS (a returned phone comes back at 80,000.00, not at its 100,000.00 selling price) |
| Accessories at moving average; the last units take the remaining value | PASS (100 at 100.00 + 50 at 133.33 -> 111.11; oracle and ledger agree after a sale, a return and a write-off) |
| Stock in transit between shops is in neither shop's inventory and sits in the clearing accounts, which cancel when it arrives or is resolved | PASS |
| The Online Store holds no inventory | PASS (invariant O10) |

## 11. Online Store and shop settlement · PASS

Since decisions D4 and D5 (14 Nov 2026): **dispatch posts nothing** (the phone stays in the shop's inventory, Reserved); **at delivery**, for a phone costing 80,000 sold online for 100,000 with 250 delivery: income 100,250 (goods 100,000 + delivery 250); cost of goods 80,000; owed to the shop 80,000; shop's inventory down 80,000 and claim on the Online Store 80,000; cash 100,250 collected. A prepaid order's money is booked when the payment is confirmed, as the customer's advance (a credit on the receivable, not income), and applied to the sale at delivery; settlement of 30,000 then 50,000 clears both sides; 0.01 more is refused; the shop's supplier bill is untouched. Two shops supplying one order are each owed their own cost. The amount owed is the same in both sets of books in every scenario (invariant O9). Refused COD parcel: nothing was ever posted; the phone goes back to the shelf of the shop that sent it at its original cost. Refused prepaid parcel: the delivery charge is kept (invoiced alone, paid from the advance), the rest of the advance is owed back. Orders invoiced at dispatch before the change keep the old reversal path.

## 12. Sales tax · PASS (arithmetic and posting only)

Tax-exclusive, on the discounted amount, half-even to the paisa, shared over lines exactly; posted to the tax account frozen on the invoice; a return reverses its share. Verified at 0% and 17% on paisa prices, fractional quantities and thirty lines. NOT TESTED: any tax report or return (none exists), input tax on purchases (not modelled), different rates on different lines (not supported).

## 13. Commission · PASS after fix B2

Recomputed independently across three dated rates, on the rate-change day itself, with discount and tax, for credit sales and for a return after a rate change. It is a report: nothing is posted, no liability exists in the books.

## 14. Fixed assets, prepaid, accruals, loans · PASS after fix B1

Straight line and reducing balance match the oracle; depreciation stops at the depreciable amount (10,000 less 1,000 scrap over 6 months, run 12 months later = 9,000.00); a second run posts nothing; disposal needs depreciation up to date; gain and loss on disposal are right in both directions. Prepaid released for whole months only; the accrual is reversed in full on its date. Loan principal is a liability; interest is an expense; a repayment above the outstanding principal is refused.

## 15. Period lock and year-end · PASS after fix B4

On or before the lock date the server refuses: a back-dated sale, a voucher, a customer payment, a loan repayment and the cancellation of an existing sale (5 of 5), leaving nothing behind; the first open day works. Only the owner can lock; reopening needs a reason and is audited; a lock in the future is refused; shop and online logins get 403. Year-end moves exactly the period's profit to retained earnings, once; an overlapping close is refused; the next period closes on its own and leaves income and expense accounts at zero.

## 16. Items for the accountant and the tax adviser (NEEDS BUSINESS DECISION)

1. *(Decided 14 Nov 2026: D4 revenue at delivery, D5 prepaid money as an advance, D8 no payment from an empty account. Implemented; see the final report.)*
2. **Half-even rounding** on invoices (D1).
3. **Stock losses in the general shop expense account** (section 3).
4. **Commission is not accrued** (D2).
5. **A full month of depreciation in the month of purchase** (D9).
6. **Moves between a shop's own accounts** (cash to bank) can still take an account below zero; D8 was decided for payments out only.
7. **Tax**: rate, base, rounding, invoice requirements and reporting (D11).
8. **Courier costs** are not captured per order; delivery income is shown gross.
9. **No opening balances workflow**: a shop starts at zero and the owner records capital. Taking on existing stock, receivables and payables at go-live would need journals (owner only) or purchases dated before go-live.
