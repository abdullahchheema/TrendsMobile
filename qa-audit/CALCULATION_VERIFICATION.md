# Calculation verification

**Result: 162 of 162 comparisons PASS, difference 0.00 on every one.** The full table (calculation, input, independently expected, actual, difference, status) is `test-results/calculations.csv`, written by the final test run.

## How the expected figures were produced

`offline/shop/tests_audit_oracle.py` imports nothing from the application. It works in exact fractions and whole paisa (it refuses a float), and implements each formula from its definition:

| Calculation | Formula used by the oracle |
|-------------|---------------------------|
| Line amount | round(quantity x unit price) to the paisa, half to even |
| Percentage discount | round(subtotal x percent / 100), half to even; an amount discount is taken as given |
| Discount per line | the discount in paisa shared in proportion to line amounts: each line its floor, left-over paisa to the largest remainders, earlier line first on a tie |
| Tax | round((subtotal - discount) x rate / 100), half to even; shared over the discounted lines the same way |
| Invoice total | subtotal - discount + tax |
| Remaining receivable | total - sum of payments |
| Commission | for each sale, round(net amount x rate in force on the sale's date / 100); a return uses the rate of its original sale's date |
| Online price | round(base x (100 - percent off) / 100), half up |
| Delivery fee | 0 if items >= free-delivery amount; else the province's fee (case- and space-insensitive); else the default |
| Online order | subtotal = sum(price x qty); total = subtotal - discount + delivery; shop payable = sum(cost x qty); goods profit = subtotal - discount - cost |
| Accessory cost | moving average; units out at round(value / quantity) each; the last units take the remaining value |
| Straight line | min(cost - scrap, round((cost - scrap) x whole months / life, half up)); month of purchase counts |
| Reducing balance | each month min(round(book x rate / 1200, half up), book - scrap) |
| Prepaid release | min(amount, round(amount x whole months / months, half up)) |
| Loan interest | round(outstanding x rate / 1200, half up) |
| Instalments | the amount in paisa split evenly, the first instalments taking the left-over paisa |
| Trial balance, P&L | sum of the raw ledger rows by account; income = -(net of Income accounts), expenses = net of Expense accounts |

The rounding rule is the one the application states for each area; that two different rules exist is decision D1 in the business-rule register.

## What was compared

For each sale, four readings had to equal the oracle: the API response of the New Sale endpoint, the saved invoice and its line snapshots, the ledger rows, and the amounts printed on the receipt. For the period and accounting figures: the raw ledger, the application's reports and the report page.

## Summary by area

| Area | Cases | Examples of inputs | Result |
|------|------:|--------------------|--------|
| Invoice subtotal, discount, net, tax, total, discount spread, tax posted, due | 128 | 1 to 30 lines; prices 0.01, 0.05, 0.10, 0.99, 333.33, 55,999.99; quantities 0.5 and 1.125; discounts 0.01, 0.02, 10 over three equal lines, 1,000.01, 3.33%, 7.5%, 12.5%, 100%; tax 0% and 17%; the exact half paisa 1,000.50 x 5% = 50.02 | PASS |
| Split payments: remaining receivable | 7 | cash; bank; cash + bank; cash + credit; bank + credit (0.01 left); three methods; nothing paid | PASS |
| Customer and supplier dues | 2 | 1,000 - 300 - 200 = 500.00; 60,000.75 - 10,000.25 = 50,000.50 | PASS |
| Phone profit | 1 | sold 100,000, cost 80,000 | PASS |
| Commission | 3 | 2% -> 5% -> 1.5% over four sales including one on the change day = 10,050.02; 2,000 less 200 discount plus 17% tax at 2% = 36.00; full return after a rate change = 0.00 | PASS |
| Full period balances | 14 | cash 1,134,914.50; bank 862,000.00; receivables 50,000.00; payables 95,000.50; inventory 58,666.53; tax payable 956.25; loan 250,000.00; capital 2,000,000.00; drawings 20,000.00; asset 120,000.00; accumulated depreciation 13,500.00; prepaid 30,000.00; accrued 0.00; stock loss 222.22 | PASS |
| Trial balance, net profit, P&L report, year-end transfer | 4 | debits = credits = 4,470,040.57; net loss 83,875.72 (by the ledger and by the report) | PASS |
| Depreciation and disposal | 3 | straight line stops at 9,000.00; reducing balance 25,240.65; gain on selling at 150,000 = 65,000.00 | PASS |

The online figures (order total 100,250.00, shop payable 80,000.00 and 77,000.50, goods sales, goods profit 20,000.00, delivery kept 250.00, remaining payable after settlement 0.00, free delivery at the threshold, province fee 400.00, bulk-purchase cost share 50,000.00 / 50,000.00 / 50,000.01, returns of 2 of 6 discounted taxed units, exchange difference 55,000.00, stock value received by a shop 141,000.50) are asserted in `tests_audit_online.py` and `tests_audit_shop.py` the same way; they are assertions in those tests rather than rows in the CSV, so they show as passing tests in the final run, not as table rows.

## Notes on specific results

* **Half paisa:** 1,000.50 at 5% gives a discount of 50.02 (half to even), not 50.03. Consistent with the till screen's own preview (`static/pricing.js`), so the cashier and the books agree.
* **One paisa of tax:** 0.05 at 17% = 0.0085 -> 0.01. 0.01 at 17% = 0.0017 -> 0.00.
* **A 100% discount** produces ledger rows of 0.00. Harmless; the audit's own invariant allows them.
* **Residual paisa** always go to the earliest line on a tie, so the same invoice is always split the same way.
* **Thirty lines at 12.5% and 17%:** the discounts and the taxes on the thirty lines add up exactly to the invoice figures.
