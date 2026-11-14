# Edge-case matrix

PASS = executed and behaved correctly. FIXED = failed, fixed, now passes. NOT TESTED / BLOCKED = not executed, with the reason. "Existing" = covered by a test that was already in the repository (`offline/shop/tests_*.py`) and passed in the final run; the audit did not repeat it.

## Input values

| Case | Where tried | Result |
|------|-------------|--------|
| Empty / missing values | checkout, order API, payments, adjustments, returns | PASS |
| Null, wrong type (list, object, bool for a number) | order API (existing `tests_edge_cases.py`), checkout payments | PASS |
| Negative amounts and quantities | sale lines, payments, discounts, delivery fee, order quantity, stock adjustment | PASS (refused) |
| Zero amounts | adjustment of 0, payment of 0, quantity 0 | PASS (refused) |
| Very large values | amounts above 9,999,999,999,999; quantity above 50 online / 99 on the website | PASS (refused) |
| More than two decimals in money | payment 10.005 | PASS (refused) |
| Exact half paisa, 0.01, 0.05, 0.10, 0.99 | invoices | PASS |
| Repeating decimals | 10 over three lines; 150,000.01 over three phones | PASS |
| Infinity, NaN, 1e400 in JSON | order API | PASS (existing) |
| NUL character | search, forms, sale | FIXED (B6) |
| SQL and script fragments, `%`, `_`, backslash | search and filters | PASS |
| Urdu and Arabic-Indic digits, emoji, 5,000-character text | search and filters | PASS |
| Arabic-Indic digits as an IMEI | bulk purchase | PASS (refused, not normalised) |
| IMEI with spaces, dashes, leading zero, one digit short, wrong check digit | bulk purchase | PASS (refused) |
| Page number that is text, negative, zero or huge | record lists | PASS |
| Shop id that is not a UUID, does not exist, or is an injection string | POS, dashboard | PASS (404) |

## Repetition, retries and races

| Case | Result |
|------|--------|
| Same checkout request twice (sequential) | PASS: one sale, one payment |
| Same checkout request three times at once | PASS |
| Same request key, different contents | PASS (refused) |
| Same website order 6 times at once | PASS: one order, one reservation |
| Same website order reference with different items | PASS: the first order is returned |
| 8 customers order the last phone at once (x3 rounds) | PASS: one 201, seven 409 |
| Counter sale and website order for the same phone at once (x4) | PASS: exactly one wins |
| Two counters (four requests) sell one phone at once | PASS: one sale |
| 7 counters sell the last 3 accessories at once | PASS: three sales; shelf at 0 units and 0 value |
| 6 payments of 400 against a 1,000 bill at once | PASS: two accepted, 200 left |
| Dispatch an order 5 times at once; deliver 5 times at once | PASS: one invoice, one payment |
| Refund the same return 4 times at once | PASS: one refund |
| Transfer and sale of the same phone at once (x3) | PASS: exactly one wins |
| Repeated confirm, dispatch, deliver, receive-returned (sequential) | PASS: no second posting |
| Second year-end close, overlapping close | FIXED (B4) |
| Second depreciation run for the same months | PASS: nothing posted |
| Duplicate daily closing; second closing of a till shift | PASS (refused) |
| Network timeout / browser retry of a sale | PASS by design: the screen re-sends the same request key (`static/pos.js`), proven by the repeat tests above. A real network cut was NOT TESTED |
| Website retries an order whose first answer was lost | PASS (existing website test; the ERP returns the same order) |

## States and transitions

| Case | Result |
|------|--------|
| Sell a phone that is Sold, Reserved, Holding, In transit, Written off, Unavailable, Returned to supplier | PASS (refused) |
| Sell a phone under another model's product; quantity 2 or 0.5 of a phone; the same phone twice on one invoice | PASS (refused) |
| Sell another shop's phone (three ways) | PASS (refused) |
| A phone the page listed, moved to another shop before checkout | FIXED (B8) |
| Order a phone that is unpublished, sold, held, in Holding, in transit, written off or damaged | PASS (409) |
| Dispatch before confirmation; confirm a prepaid order before payment; deliver after a failed delivery; receive back before a failed delivery is recorded | PASS (refused) |
| Cancel a transfer after part of it arrived; receive it twice; dispatch twice; put a phone in two transfers | PASS (refused) |
| Write off a phone that is reserved, sold or already written off | PASS (refused) |
| Write off a damaged returned phone or one in Holding | FIXED (B5) |
| Return the same phone twice; return more units than sold; return after a partial return | PASS |
| Refund more than is owed back; refund while the customer still owes | PASS (refused / applied to the debt) |
| Supplier return of a phone from another supplier's bill; without a reason; twice; by another shop | PASS (refused) |
| Post into a locked period (5 kinds); post after the daily closing | PASS (refused) |
| Open a second till shift for the same till; close without a count; close with an unexplained difference | PASS (refused) |
| Sale above the customer's credit limit | FIXED (B3: now refused with the reason) |
| Asset sold above cost | FIXED (B1) |
| Disable a login, a shop person or an online login during a session | PASS (access ends on the next request) |
| Role change during a session | NOT APPLICABLE: there are no roles to change (see the register, R53) |

## Dates and time

| Case | Result |
|------|--------|
| Future posting date | PASS (refused; existing tests) |
| The day a commission rate changes | PASS (the new rate applies from that day) |
| The lock date and the day after | PASS |
| Free-delivery amount: exactly at it, one paisa below | PASS |
| Month end for depreciation and prepaid (whole months only) | PASS |
| Instalment due dates on the 29th-31st | PASS (existing `tests_credit.py`: 31 Jan -> 28 Feb -> 31 Mar) |
| Year boundary | PASS for year-end close; a sale across midnight 31 Dec was NOT TESTED |
| Time zone | The business runs on `Asia/Karachi`; dates are local dates. Pakistan has no daylight saving: NOT APPLICABLE |

## Failures of other systems

| Case | Result |
|------|--------|
| Database error half way through a posting | PASS (everything rolled back) |
| Payment method that does not exist half way through a sale | PASS (sale rolled back) |
| ERP unreachable from the website | PASS (existing website test: "try again" message, no order) |
| Website unreachable when the ERP asks it to refresh | PASS (existing test; logged, nothing fails) |
| Supabase Auth unreachable | PASS (existing test: 10-minute grace for a verified session) |
| Supabase Storage unreachable during a photo delete | PASS by reading (the record is removed, the stray file is harmless) |
| Courier webhook replay or out of order | NOT APPLICABLE (no webhooks) |
| Serverless cold start | measured on the live site (up to 1.4 s) |

## Data volume

| Case | Result |
|------|--------|
| Bulk purchase of 6 phones across 3 models in one form | PASS |
| 30-line invoice | PASS |
| 4,700 documents / 11,750 ledger rows | pages measured (`PERFORMANCE_AUDIT.md`); General Ledger 1.78 MB (P3) |
| Pagination: page 3, page beyond the end | PASS |
| Several thousand phones in one purchase | NOT TESTED (limit is 100 per row) |

## Website

| Case | Result |
|------|--------|
| Cart: price changed, product removed, made-up price in the key, quantity above stock, duplicate lines | PASS |
| Checkout: invalid phone, landline, missing address or province, unknown province, unknown method, method switched off, honeypot, 0 / 41 lines, tampered total, fee, discount, "Paid" flag | PASS |
| Tracking: wrong phone, unknown number, changed check code, guessing | PASS |
| Urdu: right-to-left, no horizontal scroll at 375 px, no console errors | PASS |
| Unknown product address | 200 with `noindex` (W1) |
| Multiple browser tabs, cart after reload | NOT TESTED (the cart lives in the browser's local storage; there is no automated test of that store). The server re-prices whatever the browser holds, so a stale cart cannot place a wrong order |
