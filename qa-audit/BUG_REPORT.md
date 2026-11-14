# Bug report

Every defect the audit confirmed, in order of severity. A defect is listed as FIXED only when a test that failed before the change passes after it and the whole suite still passes (`FINAL_AUDIT_REPORT.md`, section 4).

**No critical defect was found**: nothing that loses or duplicates money, unbalances the books, lets one shop see or change another's data, sells or reserves one phone twice, or exposes a secret.

| ID | Severity | Module | One line | Status |
|----|----------|--------|----------|--------|
| B4 | High | Accounting: year-end close | An overlapping close moved the same profit to retained earnings twice | FIXED |
| B1 | Medium | Accounting: fixed assets | An asset could not be sold for more than it cost (server error) | FIXED |
| B2 | Medium | Staff commission | A return took commission back at the wrong rate after a rate change | FIXED |
| B3 | Medium | New Sale screen | A credit-limit refusal showed "The sale result is uncertain" | FIXED |
| B8 | Medium | New Sale screen | A phone no longer in the shop showed "The sale result is uncertain" | FIXED |
| B5 | Medium | Stock / online returns | A returned phone marked damaged could never be written off | FIXED |
| B7 | Medium | Deployment (live ERP) | Session and form cookies are not marked `Secure` on the live site | FIXED in code; live variable untouched |
| B9 | Medium | Performance | Customers and Suppliers lists: one database question per row | FIXED |
| B10 | Medium | Performance | Device Profit report: one database question per sold phone | FIXED |
| B6 | Low | All pages | A NUL character in a search or a form caused a server error | FIXED |
| B11 | Low | Books check | `verify_books` reported a correct supplier credit as an error | FIXED |
| P1 | Medium | Performance (live ERP) | Static files are served by the Python function with a one-minute cache | OPEN |
| P3 | Medium | Performance | General Ledger, Stock Ledger and Sales Register render a whole period as one page | OPEN |
| P5 | Low | Performance | Online Dashboard and Products & Photos re-check every product page on each view | OPEN |
| P4 | Low | Performance | Lists and money screens read all rows and total them in Python | OPEN |
| P2 | Low | Security hardening | No `Content-Security-Policy` header | OPEN |
| W1 | Low | Website | An unknown product address answers 200 (marked `noindex`) instead of 404 | OPEN |

Items that are not defects but need a decision are in `BUSINESS_RULE_REGISTER.md` (D1 to D11).

---

## B4 · Year-end close could count the same days twice · High

* **Module:** Accounting -> Year-end close (`offline/shop/yearend.py`, `close_year`).
* **Reproduce:** trade for a period; close the year from 1 Jan to 30 Jun; then close from 1 Jan to 31 Dec.
* **Expected:** the second close is refused, or moves only what happened after 30 Jun.
* **Actual:** accepted. The profit of January to June was moved to retained earnings again; the income and expense accounts were left with balances of the opposite sign for that amount.
* **Root cause:** the "already closed" check only looked for an earlier close ending on or after the *new end date*. The amounts to move are deliberately computed ignoring earlier closing entries (`financial_reports.pre_closing`), so an overlap counted the earlier months again.
* **Fix:** a close is refused when its start date is on or before the end of an earlier close of the same shop, with the message "This shop is already closed through <date>. Start the new period on the day after that."
* **Regression test:** `tests_audit_accounting.PeriodControls.test_year_end_close_moves_the_profit_once_and_locks_the_period` (also proves the following period closes correctly and that income and expense accounts are then zero).
* **Verified:** failed before (the overlapping close was accepted), passes after.
* **Existing data:** if an overlapping close was ever run on the live books, retained earnings are overstated by the overlap. `manage.py audit_invariants` does not detect this; check Settings -> Accounting adjustments for two "Year-end closing" entries with overlapping dates. None is expected on a system that has not been through a year end.

## B1 · An asset could not be sold for more than it cost · Medium

* **Module:** Accounting -> Fixed assets (`offline/shop/advanced.py`, `dispose_asset`).
* **Reproduce:** add an asset costing 100,000; post depreciation; Sell / dispose with proceeds of 150,000.
* **Expected:** cash +150,000; the asset and its accumulated depreciation leave the books; the excess over book value is a gain.
* **Actual:** a server error. The disposal journal tried to credit the asset account with `cost - proceeds` = -50,000; the database constraint `ledger_nonnegative` refused the row. Nothing was posted (the transaction rolled back), but the asset could not be disposed of at all.
* **Root cause:** the journal line assumed proceeds never exceed cost.
* **Fix:** when proceeds exceed cost the difference is debited back to the asset account, so the asset account nets to zero and the gain is proceeds less book value.
* **Regression tests:** `tests_audit_accounting.FullPeriod.test_an_asset_can_be_sold_for_more_than_it_cost`, `...test_an_asset_sold_below_book_value_is_a_loss`.
* **Verified:** failed before with `IntegrityError`, passes after; trial balance balanced, asset and accumulated depreciation zero, gain equal to the oracle's figure.

## B2 · A return took commission back at the wrong rate · Medium

* **Module:** Staff & Commission (`offline/shop/staff_views.py`, `staff_detail`).
* **Reproduce:** a seller on 2% sells a phone for 60,000 (earns 1,200.00). Ten days later the rate becomes 5%. The phone is then returned.
* **Expected:** the return takes back 1,200.00; the seller ends with nothing for that sale. The page itself says a sale earns "the rate in force on the day of the sale; a return takes its commission back".
* **Actual:** the return took back 3,000.00 (5% of 60,000). The seller's commission for a fully undone sale was **-1,800.00**.
* **Root cause:** the rate for a return document was looked up on the return's own date.
* **Fix:** a return uses the rate of the day of its original sale.
* **Regression test:** `tests_audit_calculations.Commission.test_a_return_takes_back_exactly_what_the_sale_earned_even_after_a_rate_change`.
* **Verified:** -180000 paisa before, 0 after. Commission is a report only, so no stored figure needed correcting.

## B3 and B8 · The New Sale screen said "result uncertain" for a sale that was simply refused · Medium

* **Module:** New Sale (`offline/shop/views.py`, `pos_checkout`; `offline/static/pos.js`).
* **Reproduce (B3):** give a customer a credit limit of 500; sell them goods on credit that take their balance above it.
* **Reproduce (B8):** open New Sale in shop A; the owner transfers a listed phone to shop B; complete the sale of that phone in the still-open page.
* **Expected:** the screen shows the reason ("... above their credit limit of PKR 500.00. A manager can approve it ...", or "no longer in this shop").
* **Actual:** the server answered with an HTML error page (403 for B3, 404 for B8). The screen only understands JSON, so it showed "The sale result is uncertain. Keep this page open ... retry this same sale" and stayed locked on a retry that could never succeed. Nothing was posted in either case.
* **Root cause:** `pos_checkout` turned validation errors into a JSON answer but let `PermissionDenied` and `Http404` through to Django's HTML error pages.
* **Fix:** both are answered as JSON with status 400 and the reason. Who may use a shop at all is still decided before the sale starts and still answers 403.
* **Regression tests:** `tests_audit_calculations.CustomerDues.test_credit_limit_is_enforced_by_the_server`; `tests_audit_shop.CounterDay.test_a_phone_in_any_state_other_than_available_cannot_be_sold`; `tests_audit_shop.Transfers.test_scenario_5_a_transfer_with_a_missing_phone`; access still refused: `tests_audit_security.WhoCanChangeWhat.*`.
* **Verified:** 403 / 404 before, 400 with the message after; no document, ledger row or stock movement in either case.

## B5 · A returned phone marked damaged could never be written off · Medium

* **Module:** Online orders -> phone came back; Stock adjustments (`offline/shop/services.py` `_adjust` and `cancel`, `stock_adjust.py`, `books_check.py`).
* **Reproduce:** dispatch an online order; record a failed delivery; receive the phone back with the decision "Damaged". Then try to write the phone off.
* **Expected:** the screen says the phone's value "stays on the books until the owner writes it off", so the owner can write it off.
* **Actual:** the phone (status Unavailable, still in the shop's stock value) was not offered on the Stock adjustments screen, and the service refused any phone that was not on sale. Its cost stayed in inventory for good. `manage.py verify_books` also reported the phone as an inconsistency. The same applied to a returned phone still in Holding that fails inspection: it had to be put on sale (and on the website) before it could be written off.
* **Root cause:** write-off was limited to status `Available`; the books check counted only Available, Reserved and Holding phones as stock.
* **Fix:** a phone the shop holds but does not sell (Holding, or Unavailable with a stock location) can be written off; cancelling such a write-off puts it back to what it was, not on sale; the books check counts a damaged phone that is still in a location as stock. Reserved, in-transit and sold phones are still refused.
* **Regression tests:** `tests_audit_online.FailedDelivery.test_a_damaged_returned_phone_can_be_written_off`, `...test_the_write_off_screen_offers_phones_that_are_in_stock_but_not_for_sale`, `tests_audit_shop.Adjustments.test_a_cancelled_write_off_of_a_phone_in_holding_goes_back_to_holding`, `...test_stock_held_for_an_order_or_a_transfer_cannot_be_adjusted_away`.
* **Verified:** failed before (books check error; phone absent from the screen), passes after; inventory falls by the phone's original cost.

## B7 · Cookies on the live ERP are not marked Secure · Medium (configuration)

* **Module:** Deployment (`offline/config/settings.py`, `config/deploy.py`; Vercel environment).
* **Observed:** on the live login page the `csrftoken` cookie carries `SameSite=Lax` and no `Secure` attribute. The session cookie is governed by the same switch.
* **Expected:** `DEPLOYMENT.md` lists `COVISON_HTTPS=1` among the Vercel variables; with it the cookies are `Secure`.
* **Root cause:** the variable is evidently not set on the live project. HSTS (sent by Vercel) and Vercel's own redirect to HTTPS mean a normal browser never sends these cookies over plain HTTP, so this is a missing layer rather than an open hole.
* **Fix (code):** on Vercel the cookies are now always `Secure`, whether or not the variable was remembered (`deploy.secure_cookies`). It takes effect at the next deployment. The production variable was not changed by the audit.
* **Regression test:** `tests_deploy_settings.HostsAndModes.test_cookies_are_https_only_on_vercel_even_when_the_https_switch_was_forgotten`.
* **To do by the owner:** either deploy this change, or add `COVISON_HTTPS=1` to the ERP's Vercel environment and redeploy.

## B9 and B10 · One database question per row · Medium (performance)

* **Module:** Customers / Suppliers lists (`views.collection`); Device Profit report (`reports.run`).
* **Measured (staging: 421 parties, 4,700 documents):** Customers list 175 SQL statements, 240 ms; Device Profit for one year 665 statements, 1.57 s.
* **Root cause:** each row printed its shop's name, fetched separately.
* **Fix:** the shop is fetched together with the rows.
* **After:** Customers 8 statements, 33 ms; Device Profit 12 statements, 0.40 s.
* **Regression tests:** `tests_audit_performance.QueryCounts.*` (the number of statements must not change when rows are added).

## B6 · A NUL character caused a server error · Low

* **Module:** every page that searches or saves free text.
* **Reproduce:** search for a text containing a NUL (`?q=%00`), or send a sale whose customer name contains one.
* **Expected:** a clear refusal.
* **Actual:** HTTP 500 (`PostgreSQL text fields cannot contain NUL (0x00) bytes`). On New Sale the screen then reported an uncertain result. Nothing was saved.
* **Root cause:** only the order API checked for NUL; pages that read `request.GET` or a JSON body directly did not.
* **Fix:** one check in `requestctx.RequestContextMiddleware`: a request whose address, form body or JSON body carries a NUL is answered 400 with a plain message (JSON for JSON requests). File uploads are not read by the check.
* **Regression test:** `tests_audit_security.WhoCanChangeWhat.test_search_and_filters_survive_hostile_input`.

## B11 · The books check reported a correct supplier credit as an error · Low

* **Module:** `manage.py verify_books` (`books_check.document_totals_vs_ledger`).
* **Reproduce:** buy on credit, pay part, return the stock to the supplier and leave the amount as credit with the supplier. Run the check.
* **Expected:** clean. The supplier owes the shop; that is what the bill's negative balance means.
* **Actual:** "BILL-...: outstanding -166500.00 outside 0..953500.00" (found on the staging dataset the application's own generator produced).
* **Fix:** for a supplier bill the allowed range is from minus its total to its total. A sale is still never below zero.
* **Regression test:** `tests_audit_shop.ReturnsAndExchanges.test_stock_goes_back_to_the_supplier_against_its_own_bill` (books check clean while the bill is in credit).

---

## Open items (not fixed in this audit)

| ID | What | Evidence | Why not fixed here | Recommendation |
|----|------|----------|--------------------|----------------|
| P1 | Every CSS and JS file of the live ERP is answered by the Python function, `Cache-Control: max-age=60`. About 0.36 s per file from the test location; a page uses about ten. | `test-results/live-erp-probe.json` | A deployment and hosting change, outside "smallest reliable fix"; must be tried on a preview deployment | Run `collectstatic` at build time and let Vercel serve `/static/` (a `@vercel/static` build and a route), or keep WhiteNoise but with hashed file names and a long cache |
| P3 | General Ledger for a year: 1.78 MB page, 1.9 s server time on 11,750 ledger rows. Stock Ledger 0.47 MB / 0.73 s; Sales Register 0.26 MB / 0.32 s. Linear in history. | `test-results/staging-performance.json` | Changing a report's shape is a product decision | Page the rows (as record lists already do) or default these three reports to the current month; keep CSV for the full period |
| P5 | Online Dashboard 260 statements, Products & Photos 238, with 55 products: both call `online_store.sync_all()` on every view, which re-checks every product page and may write. | same | A refactor of when "live" is recalculated | Recalculate only where photos, specifications or products change (those paths already call `refresh_live`), or at most once a minute |
| P4 | Record lists, Transaction History (0.34 s) and Cash and Bank (0.33 s) load every row of the shop and filter or add them in Python. | same | Works today; grows with history | Filter and total in SQL; page before building rows |
| P2 | No `Content-Security-Policy` header on the live ERP. | live probe | Needs testing against inline scripts in the templates | Add a policy in report-only mode first |
| W1 | `/p/<unknown>` on the website answers HTTP 200 with a "not found" page marked `noindex` (the page is streamed, so the status is sent before the product is looked up). | `test-results/website-local-checks.json` | Search engines are kept out by `noindex`; changing it means moving the lookup out of the streamed part | Look the product up before streaming (in `generateMetadata` or the page shell) and call `notFound()` there |
