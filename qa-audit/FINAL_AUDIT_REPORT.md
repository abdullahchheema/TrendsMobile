# Final audit report

ERP repository at `63b99a4` plus the changes listed in section 6 (uncommitted). Website repository at `7b06333` (unchanged). Audit run 13-14 November 2026.

## 1. Executive summary

**Verdict: the software is fit for real transactions, with conditions.** No critical defect was found and none is open. The books balance and reconcile after every workflow tested, under concurrency and on a 4,700-document dataset; one phone can never be sold or reserved twice; one shop cannot see or change another shop's data; the website cannot set its own prices.

**Not certified:** this is not a certification of the accounting *policies* (eleven need the owner's or accountant's decision, section 13) or of tax compliance; and the production database and live website could only be checked from outside (section 12).

| | |
|---|---|
| Defects found and fixed | 11: 1 high, 7 medium, 3 low (section 6) |
| Defects open | 6 performance and hardening items, none affecting money or data (section 7) |
| Critical defects | 0 found, 0 open |
| Automated tests | 890 existing + **105 new** = **995**, all passing on the final clean run after the owner's decisions (section 4) |
| Calculations verified against an independent oracle | 162 of 162, difference 0.00 |
| Invariants | 21 new + 18 existing books checks + online check: clean after every scenario, after load, and on staging |
| Concurrency | up to 32 simultaneous requests: 0 server errors, 0 double sales, 0 double reservations, books clean (measured before D4/D5/D8; the race tests were re-run after them) |
| Access control | 111 pages x 5 identities, 48 cross-shop listings, 57 attempted unauthorised changes: all refused |

Before go-live: deploy the fixes and the three decisions below (or at least set `COVISON_HTTPS=1`, B7), and run the three Supabase permission queries in `SECURITY_AUDIT.md` section 5.

### Owner's decisions of 14 Nov 2026, implemented after the audit

| # | Decision | What changed |
|---|----------|--------------|
| D4 | Online revenue is recorded **at delivery** | Dispatch posts nothing: the phones stay in their shop's stock, Reserved, and the accessories stay held (also on the website). Delivery posts the whole sale: revenue, cost of goods, the stock leaving the shop, the debt to the shop, and the cash (COD) or the advance (prepaid). A failed delivery reverses nothing because nothing was sold: the parcel goes back on the shelf of the shop that sent it. A prepaid order that comes back keeps the delivery charge it paid for (D6, unchanged) and owes the customer the rest |
| D5 | Prepaid money is booked **as an advance** | "Confirm payment received" books Dr the Online Store's account / Cr the customer at once; delivery applies it; a cancelled paid order keeps it as money owed back (the order history says so). A "Paid" flag sent by the website is now ignored: only the online team confirms payments |
| D8 | A payment from an account that does not hold the money is **refused** | One check inside every payment out (purchases at the Buy screens, bulk and accessory purchases, supplier bills, exchange differences, refunds), refused whole, under the same shop lock that serialises postings |

Orders already dispatched before deployment (they were invoiced at dispatch) keep the old path for delivery and failed delivery; orders already marked paid before deployment have no advance and are received at delivery as before. Both are detected automatically.

## 2. System architecture findings

Full map: `ARCHITECTURE_MAP.md`. In short: one Django application on Vercel with Supabase Postgres, Auth and Storage; the Online Store is a second set of books inside it (a shop without stock that owes the supplying shops each item's cost); the Next.js website reads four public catalogue views and places orders through one keyed API, which re-prices everything. The ERP's tables are not exposed through Supabase's Data API at all; Row Level Security is not used and not needed for that reason. Access is decided in Python on every request.

Strengths: one ledger table is the single source of every money figure; every posting is one transaction with the shop row locked and a balance check; idempotency keys on every form; database constraints on IMEIs, order references, ledger signs and open shifts.

Structural risks (not defects): the shop row lock serialises each shop's postings (fine for a few counters, section 11); there are no background jobs, so periodic work depends on someone pressing a button; static files are served by the Python function (P1).

## 3. Modules tested

| Module | Depth | Result |
|--------|-------|--------|
| Sales / POS (single, multi, phones + accessories, discounts, tax, split, credit, instalments, credit limit) | full, through the real endpoint | PASS (B3, B8 fixed) |
| Purchases (single, bulk, accessories; cash, credit, part paid; IMEI rules) | full | PASS |
| Inventory and IMEI (states, transitions, uniqueness, valuation) | full | PASS (B5 fixed) |
| Returns and exchanges (full, partial, mixed invoice, refund, credit, exchange both ways) | full | PASS |
| Supplier returns | full | PASS (B11 fixed) |
| Transfers (pending, dispatched, received, discrepancy, cancel, resolve) | full | PASS |
| Stock adjustments | full | PASS |
| Tills, shifts, daily closing | full | PASS |
| Customers and suppliers (dues, advances, isolation) | full | PASS |
| Staff commission | full | PASS (B2 fixed) |
| Accounting: double entry, TB, P&L, BS, cash flow | full, independent recomputation | PASS |
| Fixed assets, prepaid, accruals, loans | full | PASS (B1 fixed) |
| Period lock, year-end | full | PASS (B4 fixed) |
| Online Store: catalogue, orders, reservation, confirmation, dispatch, delivery, failed delivery, returns, settlement | full, through the API and the order page | PASS |
| Website: pages, cart, checkout, tracking, revalidate, Urdu, mobile | local against the mock ERP | PASS (W1 noted) |
| Security and isolation | full on the server; live from outside | PASS (B6, B7 fixed) |
| Performance | live read-only; staging pages and load | measured (B9, B10 fixed; P1-P5 open) |
| Manual journals, internal fund transfers, warranty claims, tax rule set-up | existing tests only | PASS (existing) |
| Manager override PIN | existing tests only (the only remaining use is the credit limit) | PASS (existing) |

## 4. Tests executed and results

| Suite | Tests | Result |
|-------|------:|--------|
| Existing ERP suite, baseline before any change | 890 | 890 PASS |
| Audit: calculations | 26 | PASS |
| Audit: accounting and period controls | 11 | PASS |
| Audit: shop operations | 17 | PASS |
| Audit: Online Store and cross-system scenarios 1-2 | 20 | PASS |
| Audit: concurrency | 11 | PASS |
| Audit: security | 17 | PASS |
| Audit: query counts | 2 | PASS |
| New deployment-setting test | 1 | PASS |
| Final clean run before the owner's decisions (14 Nov) | 993 | 993 PASS |
| **Final clean run after D4, D5, D8** (new empty database, 14 Nov) | **995** | **995 PASS** |
| Website: vitest / eslint / tsc | 95 / - / - | PASS / clean / clean |
| Website: local end-to-end checks | 90 | 89 PASS, 1 noted (W1) |
| Invariants command on the staging database | 40 checks | 40 PASS |
| Live ERP read-only probe | 16 addresses | 16 as expected |

Existing tests changed by the audit fixes: one. `tests_pos.Checkout.test_a_phone_that_is_not_available_or_belongs_to_another_shop_is_refused` expected the HTML 404 that fix B8 replaces with a JSON 400 carrying the reason; it now checks the 400, the message, and that the other shop's phone was not sold.

Existing tests changed by the owner's decisions (they encoded the old policy, so they were moved to the new one; none was removed, and every assertion about the books was kept or made stricter):
* **D4/D5** (`tests_online_orders`, `tests_online_lifecycle`, `tests_online_money`, `tests_edge_cases`): about 30 tests now check that dispatch posts nothing, that the whole sale appears at delivery, that a refused parcel leaves no document at all, and that confirmed prepaid money sits in the Online Store's account as the customer's advance. A shared `ship()` helper dispatches and delivers.
* **D8** (ten modules): about 50 tests bought stock "paid in full" from a shop that had never had money put in. A shared `fund()` helper now records the owner's capital for exactly what each purchase pays. One assertion changed value: `tests_reports_f` expected a till balance of -40,000 (impossible now), and expects +60,000 with the owner's 100,000 counted.

Scenario coverage of the brief: 1 (normal online sale to settlement), 2 (failed delivery), 3 (shop day to daily closing), 4 (return to Holding and acceptance), 5 (transfer with discrepancy), 6 (counter vs website for one phone), 7 (full financial period): all executed, all PASS.

## 5. Critical and high-severity defects

None critical. One high: **B4**, an overlapping year-end close moved the same profit to retained earnings twice. Fixed. If a year-end close has ever been run on the live books, check for two overlapping "Year-end closing" journals (`BUG_REPORT.md`, B4).

## 6. Defects fixed

| ID | Severity | Fix | Files |
|----|----------|-----|-------|
| B4 | High | Refuse a year-end close that starts within an earlier close | `yearend.py` |
| B1 | Medium | Asset sold above cost: post the excess back to the asset account | `advanced.py` |
| B2 | Medium | A return uses its sale's commission rate | `staff_views.py` |
| B3 | Medium | Approval refusals at checkout answered as JSON 400 with the reason | `views.py` |
| B8 | Medium | A phone gone from the shop answered as JSON 400 with the reason | `views.py`, `tests_pos.py` |
| B5 | Medium | Damaged / Holding phones can be written off; cancelling restores their state; books check counts them | `services.py`, `stock_adjust.py`, `books_check.py` |
| B7 | Medium | Cookies always `Secure` on Vercel | `config/deploy.py`, `config/settings.py`, `tests_deploy_settings.py` |
| B9 | Medium | Customers/Suppliers lists: one query for the shops | `views.py` |
| B10 | Medium | Device Profit: one query for the shops | `reports.py` |
| B6 | Low | Requests carrying a NUL answered 400 | `requestctx.py` |
| B11 | Low | Books check accepts a supplier bill in credit | `books_check.py` |
| N1 | Note | Two code comments corrected to match the pack list | `online_orders.py`, `online_views.py` |

Each has a regression test that failed before the fix (`BUG_REPORT.md`). Total production-code change: 13 files, 67 lines added and 16 removed (plus two existing test files: `tests_pos.py` as explained in section 4, and one new test in `tests_deploy_settings.py`). Added: the 10 `tests_audit_*.py` modules, `manage.py audit_invariants`, and this folder.

No database migration is needed: no model or constraint changed.

## 7. Defects still open

| ID | Severity | What |
|----|----------|------|
| P1 | Medium | Static files served by the Python function, 60-second cache |
| P3 | Medium | General Ledger / Stock Ledger / Sales Register render whole periods (1.78 MB, 1.9 s for a year of staging data) |
| P5 | Low | Online Dashboard and Products & Photos run about 250 queries per view |
| P4 | Low | Lists and money screens total in Python |
| P2 | Low | No Content-Security-Policy |
| W1 | Low | Website: unknown product answers 200 + noindex |

Details and recommendations: `BUG_REPORT.md` and `PERFORMANCE_AUDIT.md`.

## 8. Accounting findings

`ACCOUNTING_AUDIT.md`. Every tested posting balances; the trial balance equals an independent aggregation of the ledger account by account; 14 balance-sheet figures of a three-month period equal figures computed from the inputs; P&L, balance sheet and cash flow agree with the ledger and with each other; customer and supplier sub-ledgers equal their control accounts; stock value equals the inventory account in every shop; the Online Store's debt to the shops is the same in both sets of books. Classification notes: stock losses go to the general shop expense account; several standard accounts are never used; commission is not accrued.

## 9. Financial reconciliation results

| Reconciliation | Result |
|----------------|--------|
| Debits = credits per document, per shop, overall | PASS |
| Trial balance vs raw ledger | PASS |
| P&L vs ledger vs report page | PASS |
| Cash and bank: ledger = balances screen = history | PASS |
| Customer receivables: dues screen = sub-ledger = control | PASS |
| Supplier payables: same | PASS |
| Inventory: stock movements = inventory account | PASS |
| Online payable: Online Store side = shops' side | PASS |
| Settlement: paid + remaining = owed | PASS |
| Commission vs oracle | PASS |
| Depreciation, prepaid, loan vs oracle | PASS |
| All of the above after 170 concurrent requests x 5 levels on staging | PASS |

## 10. Security findings

`SECURITY_AUDIT.md`. Isolation between shops, between shops and the Online Store, and for anonymous users holds on the server for reading and for writing. The order API re-prices everything, requires the key, and is idempotent and rate-limited in the database. The public views expose no private column. No secret reaches the browser. Fixed: B6 (NUL), B7 (cookie flag). Recorded risks: whoever holds the API key may mark orders paid and apply discounts (R38); the shop pack list shows delivery contact details by design; login lockout is per username.

## 11. Performance findings

`PERFORMANCE_AUDIT.md`. Live: about 450 ms per request even for a redirect (network + function start), up to 1.4 s cold. Staging: everyday pages 20-140 ms of server time; year-long reports 0.3-1.9 s; postings serialise per shop, about 11 per second at best, under 2 s at 8 concurrent postings in one shop, 10 s at the 99th percentile with 32. Fixed: two N+1 pages (B9, B10).

## 12. Production risks

1. **Live checks that were not possible.** The production database's permissions, the live website and any signed-in live page were not tested (no access, and the audit would not use production credentials or write to production). Three read-only SQL queries to close the database part are in `SECURITY_AUDIT.md` section 5.
2. **Cookie flag missing on the live ERP** until B7 is deployed or `COVISON_HTTPS=1` is set.
3. *(Resolved by decision D5.)*
4. *(Resolved by decision D8 for every payment out.)* Moves between a shop's own accounts (cash to bank) can still take an account below zero.
5. **Reports grow with history** (P3): the General Ledger will eventually exceed the function's time limit.
6. **The API key is the only protection for discounts** (R38); a "paid" flag from the API is now ignored. Keep it only in the two Vercel projects' server environments; rotate it if it is ever exposed (old tracking links stop working when it changes).
7. **No reservation expiry** (D3): an unconfirmed order holds its phone until someone acts.

## 13. Ambiguous business rules requiring owner decisions

From `BUSINESS_RULE_REGISTER.md`, with the options for each:

| # | Decision |
|---|----------|
| D1 | One rounding rule for half a paisa (half-even on invoices, half-up elsewhere) |
| D2 | Commission earned on sale or on payment; posted as a liability or report only |
| D3 | Auto-release of unconfirmed online orders after a time |
| D4 | ~~Online revenue at dispatch or at delivery~~ **Decided: at delivery** (implemented) |
| D5 | ~~Book prepaid money as a customer advance~~ **Decided: yes** (implemented) |
| D6 | Refund the delivery charge when a prepaid delivery fails |
| D7 | Online return received by a shop other than the supplier |
| D8 | ~~Refuse purchases paid from an account with no money~~ **Decided: refuse** (implemented). Open: the same for moves between own accounts |
| D9 | Depreciation in the month of purchase |
| D10 | Per-login discount and refund limits |
| D11 | Sales tax rules (for the tax adviser) |

## 14. Database migrations required

The audit fixes need none. **Decision D4 needs one: `0034_web_views_v5`**, which recreates the website's catalogue views so that accessories of a parcel on its way stay held. No table, column or constraint changes; it was applied, rolled back to `0033` and applied again on the staging database, and `makemigrations --check` reports nothing pending.

On the hosted database the website's copies of these views depend on them, so the order is (also written into `DEPLOYMENT.md`):
1. `supabase/hosted/web-views-drop.sql` in the Supabase SQL editor (the website cannot read its catalogue until step 3);
2. `supabase/hosted/update-database.ps1`;
3. `supabase/hosted/web-views.sql` in the SQL editor.

## 15. Deployment and rollback

The changes are uncommitted in the ERP working tree; the website is untouched. Nothing was deployed.

**Deploy**

1. Review the diff (`git diff` in the ERP repository) and the new tests.
2. Commit on a branch; let CI (`.github/workflows/tests.yml`) run the full suite, which includes the audit tests.
3. Apply migration `0034` in the order given in section 14, at a quiet moment (about a minute without a catalogue on the website).
4. Deploy to a Vercel preview first. On the preview: sign in, make a test sale with a credit-limit customer and confirm the screen shows the reason; check the login page's cookies carry `Secure`.
   Also on the preview: confirm a prepaid test order's payment (the money appears in the Online Store's account as an advance), dispatch it (nothing is posted), deliver it (the sale appears).
5. Promote to production.
6. After deployment run, from a machine that has the production database URL, the read-only check: `python manage.py audit_invariants` (it runs in a read-only transaction and exits with an error if anything is wrong). Then `python manage.py verify_books`.

**Rollback**

The audit fixes are code only. For D4/D5/D8, first roll the views back (`web-views-drop.sql`, `python manage.py migrate shop 0033`, `web-views.sql`), then redeploy the previous Vercel deployment. Orders dispatched under the new rule have no invoice; the old code would expect one at delivery, so deliver (or cancel) any parcel that is on its way before rolling back. Advances booked under D5 stay as credits on the customers' accounts and can be applied or refunded with the existing advance screens.

For the audit fixes alone: redeploying the previous Vercel deployment (or reverting the commit) restores the old behaviour completely; no data written by the new code needs undoing (a phone written off from Holding or "damaged" is simply a written-off phone to the old code). After a rollback the old defects return: for example a damaged returned phone again cannot be written off and `verify_books` again reports it as an error.

**Independently of deploying the code**, add `COVISON_HTTPS=1` to the ERP's Vercel environment (it is listed in `DEPLOYMENT.md`), then redeploy.
