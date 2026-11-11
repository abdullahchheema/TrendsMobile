# Stress test and realistic-data report

Run on a local copy of your system that mirrors your live starting point (owner, two shops with their logins, 2 purchases, 2 sales).
Your **live Supabase database was not touched**: it cannot be reached from the test machine and no credentials were requested.
To put the same test business into your live database, run `supabase\hosted\generate-test-data.ps1` on your own computer (it only adds, never deletes).

## A. Overall summary

| | |
|---|---|
| Modules exercised | Purchases (single, bulk, accessories), IMEI stock, POS/sales (cash, bank, part, credit), customer and supplier payments, dues, refunds, sales returns, purchase returns, exchanges, owner-only transfers, stock adjustments, expenses, owner funds/drawings, cash↔bank moves, fixed assets, depreciation, prepaid, accrual, loan, cancellations, all reports and screens |
| Business events run through the generator | ≈ 1,900 (419 sales, 529 phones bought, 109 customer payments, 40 supplier payments, 207 expenses, 11 returns, 4 exchanges, 5 transfers, 13 adjustments …) |
| New adversarial tests written | 27 (all pass) |
| Full automated suite | 646 tests: 638 pass, the 8 that error are the Postgres-only concurrency tests (they cannot run on the SQLite test machine) |
| Pages opened and checked (owner + each shop login) | 258, **0 server errors, 0 cross-shop leaks** (checked by record ID, not by name) |
| Whole-books consistency checks | **17 of 17 pass** after 90 days of activity |
| Bugs found | 5 · fixed 4 · by design / reported as risk 1 |

## B. Data generated (additive, permanent, not cleaned up)

| Item | Count |
|---|---|
| Documents posted | 1,569 (432 sales invoices, 99 purchase bills + 99 receipts, 687 payment entries, 212 vouchers, 19 journals, 7 stock adjustments, 4 transfers, 3 exchanges) |
| Phones (devices) | 534 — Trends Mobile 245, Capital Mobiles 289 |
| IMEIs | 708 (174 dual-IMEI phones) |
| Brands | iPhone 136, Samsung 110, Xiaomi 41, Tecno 44, Redmi 36, Vivo 31, Oppo 30, Google Pixel 30, Infinix 28, OnePlus 14, Realme 14, Huawei 13, Motorola 7 |
| Customers / suppliers | 151 customers, 36 suppliers (all named "TEST · …") |
| Accessory products | 10 (chargers, cables, covers, glass, earphones, earbuds, power banks, stands, memory cards) |
| Ledger rows | 4,008 (debits = credits = PKR 351,267,308.50) |
| Phone states at the end | Sold 522, Available 6, Holding 3, Written off 2, In transit 1 |
| Transfers | 3 received, 1 with a missing-phone discrepancy (that phone is still shown In transit) |

Your original records were compared row by row afterwards: the 2 shops, 3 users, 12 documents, 28 ledger lines, 2 phones, 4 parties and 79 accounts are **unchanged** (only each user's "last login" time moved, because the page check logged in).

## C. Financial consistency

* Every document balances; every shop balances; the trial balance balances (all shops and per shop).
* Phone status = stock movement = location, for all 534 phones. No negative stock. Stock value = inventory account.
* Customer and supplier balances equal the open amounts on their invoices (less advances, plus refunds handed back).
* Sales invoice totals equal the ledger; invoice lines add up; sold/available phones agree with sales.
* Cancelled documents net to zero. Dashboard, profit and loss and ledger agree; all-shops = sum of the shops.
* Cash, bank and wallet balances equal the Transaction History. Customer and supplier dues lists equal the ledger.

| | Trends Mobile | Capital Mobiles |
|---|---|---|
| Sales invoices | 191 | 230 |
| Customers owe (receivable) | 1,502,350 | 1,980,650 |
| Owed to suppliers (payable) | 1,821,000 | 3,027,600 |
| Inventory at cost | 2,408,823 | 2,134,869 |
| Cash | 1,587,250 | 5,191,700 |
| Bank | 1,262,450 | 2,492,400 |
| Easypaisa / JazzCash / Card | 1,233,150 / 2,000,350 / 1,725,350 | 1,376,600 / 1,233,900 / 742,050 |

Totals: sales PKR 54,337,200 (3,483,000 still unpaid), sales returns 1,490,000, purchase bills 51,935,700 (4,848,600 still unpaid).

## D. Bugs

| # | Title | Module | Severity | Status |
|---|---|---|---|---|
| 1 | A cash refund was recorded "on account" and never closed the return credit. The same credit could be refunded again, and a refund larger than the credit was accepted. | Money / Returns | **High** (money could be paid out twice) | **Fixed**: a refund now settles the party's open return credits oldest-first, is refused if larger than what is owed, is refused while the original invoice is unpaid, and uses up a returned advance. Tested. |
| 2 | A sale that had ever received a payment could never be cancelled, even after every payment was properly reversed. | Sales | Medium | **Fixed**: payments that were cancelled and reversed no longer block. Tested. |
| 3 | Selling another shop's phone, or a phone in transit, crashed with a technical error instead of a clear message. | Sales | Low | **Fixed**: now a clear message. Tested. |
| 4 | The books audit mis-signed refunds handed back (false alarm in the checker, not in the app). | Audit tool | Low | Fixed. |
| 5 | A phone can be bought with a blank IMEI (the field says "recommended"). | Purchases | Medium (business rule) | **Not changed — your decision.** See risks. |

Behaviours confirmed correct (the app rejects them cleanly and leaves nothing behind): duplicate IMEI in the same or the other shop, malformed/short/non-numeric IMEI, zero/negative/sub-paisa/enormous prices, selling a sold phone, selling a written-off or in-transit phone, returning the same phone twice, payment on a fully paid invoice (kept as a customer advance, not lost), a shop login creating or opening transfers, a shop login opening the other shop's customers, suppliers, phones, statements, documents, ledger or transfers, forged IDs on cancel/accept, double submit, double cancel, 500-character and Urdu text.

## E. Remaining risks

1. **Live database not tested here.** Run `generate-test-data.ps1` yourself; it first audits your books (read-only), asks you to type `ADD TEST DATA`, adds the activity, audits again.
2. **True simultaneous use** (two people saving at the same moment) can only be tested on PostgreSQL. 8 concurrency tests exist and are skipped on the test machine; they should be run in CI against Postgres.
3. **Blank IMEI** is allowed on purchase. If every phone must have an IMEI, make the field required (small change).
4. **Generated story sells fast** (522 of 534 phones sold) and starts with plenty of cash, so stock-out and low-cash situations are under-tested. Dashboards on this data will look "busy" but are not a forecast.
5. One return was left as an unposted draft (the app refused to post it because the phone had already been returned). Harmless, but a draft can exist without posting.
6. Performance was not load-tested beyond ~1,500 documents; pages opened in under 3 seconds each.

## F. Readiness assessment

Money, stock and ledgers stayed consistent through 90 days of mixed activity in two shops, and the isolation between shops held on every page and forged-ID attempt tried. Before relying on it with real money: run the live script, run the Postgres concurrency tests, and decide the blank-IMEI rule. With those done, I consider it ready for supervised daily use, not yet for unattended use.
