# Test data design

## Principles

1. **Nothing real.** Every name, phone number and reference is invented and marked as such (`Audit Shop A`, `Audit Supplier`, `TM-AUDIT0001-ABCDEF`, IMEIs from an unassigned-looking test prefix `35693803......` with a correct check digit). No real customer, supplier or order appears in any test or result file.
2. **Created the way the business creates it.** Shops through the owner's shop set-up (`setup.create_shop`), logins through Shop Accounts, the chart through "install standard chart", stock through real purchases (receipt -> supplier bill -> payment), sales through the POS checkout endpoint, online orders through the order API with the shared key, order steps through the online order page as the online login. No test writes a ledger row, a stock movement or a phone status by hand to *set up* a scenario. (A few tests flip a phone's status directly to prove that the sale path refuses it; they say so.)
3. **Isolated and repeatable.** Each test runs in its own database transaction and is rolled back (the concurrency tests use real commits and are flushed afterwards). Tests do not depend on each other or on the order they run in.
4. **Amounts chosen to break things.** Prices and costs include paisa (`60000.75`, `133.33`, `0.01`, `0.99`), amounts whose shares do not divide evenly (`150000.01` over three phones, a discount of `0.01` over three equal lines, 5 of 30 units returned), an exact half paisa (`1000.50` at 5%), and dates either side of every boundary (the day a commission rate changes, the lock date and the day after, the free-delivery amount and one paisa below it).

## The fixture (`tests_audit_base.AuditMixin.build`)

| Record | Value |
|--------|-------|
| Owner | `audit-owner` (superuser) |
| Shops | `AUDA` "Audit Shop A" (Cash, Bank Transfer, Easypaisa), `AUDB` "Audit Shop B" (Cash, Bank Transfer); same company |
| Logins | `audit-shop-a`, `audit-shop-b`, online login `audit-online` |
| Chart | the application's standard chart (`COA-1000` ... `COA-6950`) plus each shop's own posting accounts |
| Product | "Galaxy A99 128GB Black" (serialized); accessories are created by their first purchase |
| Online Store | created on first use, as in production |

## The full-period dataset (scenario 7, `tests_audit_accounting.PeriodBase.trade`)

Dates are relative to the day the test runs (`D-90` = 90 days ago), so the dataset never goes stale.

| Day | Event | Amount (PKR) |
|-----|-------|--------------|
| D-90 | Owner capital, cash | 2,000,000.00 |
| D-89 | Cash moved to bank | 800,000.00 |
| D-85 | Phone 1 bought, paid in cash | 80,000.00 |
| D-84 | Phone 2 bought on credit, part paid in cash | 60,000.75 / 10,000.25 paid |
| D-83 | Phone 3 bought on credit, unpaid | 45,000.00 |
| D-80 | Insurance prepaid for 6 months, from bank | 60,000.00 |
| D-75 | Display counter bought from bank; 24 months, scrap value 12,000 | 120,000.00 |
| D-70 | Loan received into bank, 12% a year | 300,000.00 |
| D-60 | 100 covers at 100.00, cash | 10,000.00 |
| D-40 | 50 covers at 133.33, cash | 6,666.50 |
| D-30 | Phone 1 sold for cash | 100,000.00 |
| D-29 | Phone 2 sold on credit, part paid by bank | 75,000.00 / 25,000.00 paid |
| today | 30 covers at 250.00, 10% discount, 17% tax, cash | 7,897.50 |
| today | 5 of those covers returned, refunded in cash | 1,316.25 |
| D-28 | Rent, cash | 50,000.00 |
| D-27 | Salaries, bank | 30,000.00 |
| D-26 | Owner drawings, cash | 20,000.00 |
| D-25 | Other income (scrap sold), cash | 5,000.00 |
| D-20 | Loan repayment: principal + one month of interest, bank | 50,000.00 + 3,000.00 |
| D-15 | Electricity accrued; reversed on D-5 | 8,000.00 |
| D-10 | 2 covers written off (damaged) | at average cost |
| today | Depreciation and prepaid release posted up to today | by the oracle |

Expected figures (cash, bank, receivable, payable, inventory, tax payable, loan, capital, drawings, asset, accumulated depreciation, prepaid balance, income, cost of goods, expenses, profit) are computed in the test from these inputs with the oracle and compared with the ledger, the reports and the pages.

## Staging dataset (performance)

Built by the application's own generator (`stress_data.Story`, the same one behind `manage.py generate_test_business`) run three times over 90 days on two shops, then about sixty phone models published to the website. Sizes are recorded in `test-results/staging-performance.json` (`before` / `after`).

## What is deliberately not in the data

No real IMEIs of real devices, no real bank or wallet numbers (payment-account tests use `PK00FAKE`), no real addresses, no production export.
