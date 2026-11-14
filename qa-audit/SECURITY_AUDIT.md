# Security and data-isolation audit

Method: the source was read for every access decision; then every named page of the ERP (111) was requested as each kind of user with the record ids in the address pointing at someone else's records; then the same was tried with POSTs that would change something, comparing a snapshot of the data before and after. The matrix of answers is in `test-results/access-matrix.csv`. The live ERP was only observed from outside (no sign-in). Nothing destructive was attempted anywhere.

## Summary

| Area | Result |
|------|--------|
| Cross-shop access (read) | PASS: no page shows shop B's customers, suppliers, documents, phones or IMEIs to shop A |
| Cross-shop access (write) | PASS: 28 attempted changes to shop B's records by shop A, all refused, nothing changed; 20 attempts to use owner powers, all refused |
| Owner-only functions | PASS: refused for shop and online logins on the server |
| Online login vs shop money | PASS: every shop page answers 403; its finance pages show and post only the Online Store's books |
| Shop vs Online Store | PASS: every online page and action answers 403 to a shop |
| Not signed in | PASS: every private page redirects to the login; every POST is refused |
| Order API | PASS: key required and compared in constant time; wrong orders change nothing; rate limits in the database |
| Public catalogue | PASS: 4 read-only views; no cost, IMEI, shop, customer or supplier column |
| Secrets in the browser | PASS: the ERP key is read only by server files of the website |
| Session handling | PASS; one configuration gap on the live site (cookies not `Secure`, B7) |
| Injection, XSS, CSRF, path traversal | PASS; one unhandled input found and fixed (NUL character, B6) |

No critical or high security defect was found.

## 1. Authentication

| Check | Result | Evidence |
|-------|--------|----------|
| Sign-in is Supabase Auth; the ERP keeps the provider session on the server and asks the provider who the session belongs to at most every 30 seconds; an explicit rejection signs the user out; a provider outage is tolerated for 10 minutes for an already verified session | Read (`supabase_auth.py`); covered by existing `tests_supabase.py`, `tests_phase1.py` | PASS (existing tests), not re-tested against a real Supabase project: BLOCKED (no test project) |
| A login whose staff record or shop is switched off loses access on the next request | PASS | `sec.test_a_login_that_is_switched_off_loses_access_at_once` |
| An online login that is disabled loses access; a deactivated user is signed out | PASS | same |
| Sign-out is a POST and ends the session | PASS | `sec.test_signing_out_ends_the_session` |
| Repeated wrong passwords lock that username for 15 minutes (429), even for the right password | PASS | `sec.test_wrong_passwords_lock_the_login_for_a_while` |
| Password reset | NOT TESTED: there is no self-service reset; the owner resets a shop's password in Shop Accounts (Supabase admin call). Changing one's own password requires the old one (read) |
| Session lifetime | 12 hours by default (`COVISON_SESSION_HOURS`); cookies `HttpOnly`, `SameSite=Lax` | read |

Note on the lockout: the counter is keyed by username and the address Django sees. On Vercel that address is the proxy's, so in practice the lock is per username: someone who knows a shop's login name can lock it for 15 minutes by guessing. Low; accepted trade-off of any lockout.

## 2. Authorization matrix (server side)

GET of every named page; numbers are HTTP status codes. Full table: `test-results/access-matrix.csv`.

| Who | Asked for | Expected | Result |
|-----|-----------|----------|--------|
| Nobody | every private page | 302 to `/login/` | PASS (all); the three API addresses answer 401 / 405 |
| Shop A | pages carrying an id of shop B (sale, bill, customer, supplier, phone, payment plan, return, statement, receipt, device history, ...) | 403 or 404 | PASS (all) |
| Shop A | lists, searches, reports, dues, money screens, POS, dashboard, with and without `?branch=<shop B>` | never any of shop B's names, document codes, phone codes or IMEIs | PASS (48 addresses) |
| Shop A | owner-only pages (23) | 403 | PASS |
| Shop A | online pages (19), GET and POST | 403 | PASS |
| Online login | every shop page | 403 | PASS |
| Online login | invoice, receipt and return of an online order | 200 | PASS |
| Online login | the same pages for a shop's sale | 403 | PASS |
| Online login | its finance pages with `?branch=<shop B>` | only the Online Store's figures | PASS |
| Owner | everything | never 403 | PASS |

"Stock in All Shops" is the one screen where a shop sees other shops: model names and quantities only, by design. Checked: no costs, customers or suppliers.

The pack list (`/orders/online/`) shows a shop the delivery name, phone and address of orders it supplies, by design, and never a price or payment. A shop therefore does see online customers' contact details for its own parcels. If that is not wanted, it is a product decision (the previous version did not show them).

## 3. Attempts to change something

`tests_audit_security.WhoCanChangeWhat`: each request is sent, the answer must be a refusal, and a snapshot (counts of documents, ledger rows, stock movements and parties; every phone's status and shop; every document's status and balance; the online order's status; the shop's lock date) must be identical afterwards.

* Shop A against shop B: cancel, submit, check out, return, bill, allocate, apply credit and settle on B's sale; cancel B's bill; sell B's phone by naming shop B, by naming shop A with B's warehouse, and by naming shop A with A's warehouse; sell to B's customer; take a payment from B's customer or pay B's supplier (three routes each); rename B's customer and supplier; return B's sale; return B's stock to its supplier; set a payment plan on B's sale; change B's phone; accept B's phone from Holding; write off B's phone; post drawings in B; rename shop B. **All refused.**
* Shop A using owner powers: create or dispatch a transfer (from B to A and from A to B), lock a period, close a year, open a journal, add staff, change who a sale is credited to, create a login, create a shop, change posting accounts, create accounts / staff / warehouses / tills / products / models, add an asset or a loan, post a loan voucher. **All refused.**
* Online login: POS checkout, cancel a shop's sale, cancel an online invoice, post owner funds in a shop, take a payment from a shop's customer, transfer stock, write off stock, lock a period, post into shop B through its own finance pages, add a payment account customers would be told to pay into. **All refused or confined to the Online Store's books.**
* Forged cross-site POSTs (no CSRF token): refused (JSON 403 for the checkout; form posts are sent back with "page expired"); nothing changed; the user stayed signed in.

## 4. Order API

| Check | Result |
|-------|--------|
| No key, empty key, wrong key, key with a trailing space, key missing a character, key in another case | 401 each, nothing created |
| `ONLINE_API_KEY` empty on the server | the API is off (401) |
| Constant-time comparison | `hmac.compare_digest` (read) |
| GET on the orders address | 405 |
| Tampered price, fee, total, quantity, province fee, discount above the items, negative amounts, unknown payment method, missing name, short phone | 400 or 409, nothing reserved |
| Unpublished product; phone that is Sold, Reserved, in Holding, in transit, written off or damaged | 409 |
| Same reference twice, including with different contents, including six at once | one order |
| Rate limits (database-backed, shared by every copy of the website): 3 unconfirmed orders per phone, 6 per phone per hour, 12 per address per hour | PASS: the 4th order from one number is 429 |
| Tracking answer | status, courier, tracking number, total, dates. No name, address or phone |
| Malformed bodies of every shape | covered by the existing `tests_edge_cases.py` (not repeated) |

**Trust boundary (documented behaviour, recorded as a risk):** whoever holds the key can apply any discount up to the items' value. Since decision D5 (14 Nov 2026) a `payment_status: "Paid"` from the API is ignored: an order is paid only when the online team confirms it, which books the money. The current website sends neither field (verified locally). The protection for the discount is the secrecy of the key; if the website will never apply coupons itself, the ERP could ignore that field too.

The tracking address accepts any order number with only the key; the website adds the "number + phone" check with an HMAC built from the key. That is sound as long as the key stays secret; rotating the key invalidates old order numbers for tracking.

## 5. Supabase

| Check | Result | Evidence |
|-------|--------|----------|
| ERP tables are in schema `covison_app`, with all rights revoked from `public`, `anon`, `authenticated` and `service_role`; the Data API serves schema `api` only | `supabase/hosted/setup.sql` (read) | PASS on paper. **Live check BLOCKED**: verifying it means querying the production Data API; the audit did not, because the project address and public key are only in the private credentials folder |
| Exposed to `anon`: `SELECT` on `api.web_families`, `web_listings`, `web_variants`, `web_images` | `supabase/hosted/web-views.sql` (read) | PASS on paper |
| The per-shop views are dropped from `api` | same | PASS on paper |
| Columns of the four views contain nothing private | test on the real views in PostgreSQL | PASS |
| Writes through the public key | The views are joins and aggregates with `SELECT` granted only; there is no `INSERT`/`UPDATE`/`DELETE` grant | PASS on paper; live attempt BLOCKED (would be a write attempt against production) |
| Storage: `product-images` public (by design: only product photos); `pta-evidence` and `device-images` private, 10 MB limit | `setup.sql`, `product_media.py` (read) | PASS on paper |
| Photo upload accepts only JPEG, PNG or WebP by their real bytes, 5 MB, 12 per product, stored under a random name | `product_media.py` (read); existing `tests_online.py` | PASS |
| The service key is used only on the server | `supabase.py` (read); it is not in any template or static file (searched) | PASS |

To close the three BLOCKED rows, run these in the Supabase SQL editor (read-only) and compare with `setup.sql`:

```sql
select table_schema, table_name, grantee, privilege_type from information_schema.role_table_grants where grantee in ('anon','authenticated') order by 1,2,3,4;
select nspname, has_schema_privilege('anon', nspname, 'USAGE') from pg_namespace where nspname in ('covison_app','api','public');
select id, public from storage.buckets;
```

Expected: `anon` has `SELECT` on four `api.web_*` views and nothing in `covison_app`; `USAGE` true for `api` only (and `public`, which holds no ERP table); `product-images` public, the other two private.

## 6. Website

| Check | Result |
|-------|--------|
| `ERP_API_KEY` is read only in server files (`erp-orders.ts`, `erp-config.ts`, `order-ref.ts`, `api/revalidate/route.ts`, `env.ts`); no `"use client"` file reads it | PASS (search) |
| Rendered pages and the settings feed contain neither the key nor its variable name | PASS (36 page checks) |
| A production server refuses to start if a secret-looking variable is prefixed `NEXT_PUBLIC_` | PASS (read; existing `env.test.ts`) |
| Checkout rebuilds prices and delivery on the server; client totals are only compared | PASS (tampering checks, `test-results/website-local-checks.json`) |
| Tracking: wrong phone and unknown order give the identical answer; guessing is cut off at 20 tries per address per 10 minutes | PASS |
| Cache refresh hook needs the key | PASS |
| Honeypot field, input length limits, order-rate limit (8 per address per 10 minutes) | PASS |

The website's own limiter counts per server instance (in memory), so on a serverless host it only slows an attacker down; the ERP's database-backed limits are the real control. Stated in the code; confirmed.

## 7. General

| Class | How checked | Result |
|-------|-------------|--------|
| SQL injection | All queries go through the ORM; the only raw SQL is the view definitions (constants) and an advisory lock with a bound parameter. Hostile strings sent to five search and filter pages | PASS |
| Cross-site scripting | Django templates escape by default; a script tag sent as a search term is not echoed unescaped | PASS |
| CSRF | Enforced on every POST except the three key-protected API addresses | PASS |
| Direct object references | Section 2 and 3 | PASS |
| File upload | Section 5 | PASS |
| Path traversal on the local photo route | four traversal paths answer 404; the route is off on the live system | PASS |
| Error detail | `DEBUG=False`; errors are friendly pages; the health address shows no business data | PASS |
| NUL character | Server error before the fix | FIXED (B6) |
| CSV export | Values starting with `= + - @` are prefixed so a spreadsheet does not run them (read) | PASS |
| Dependencies | ERP: Django 5.2.18, psycopg 3.2.13, gunicorn 23.0.0, whitenoise 6.11.0, all pinned. Website: Next.js 16.4.3, React 19.3.0, pinned. No vulnerability scan was run against an advisory database | NOT TESTED (needs network access to an advisory service; run `pip-audit` and `npm audit` in CI) |

## 8. Live ERP, seen from outside (2026-11-13)

| Check | Result |
|-------|--------|
| HTTP redirects to HTTPS (308) | PASS |
| `Strict-Transport-Security: max-age=63072000; includeSubDomains; preload` | PASS |
| `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: same-origin` | PASS |
| Private pages without a session | 302 to the login (9 of 9) |
| Order API without a key | 401 |
| `/admin/` | 404 (Django admin is not installed) |
| Pages are `no-store` | PASS |
| `Content-Security-Policy` | absent (P2) |
| Cookie flags | `SameSite=Lax`; **`Secure` missing** (B7) |
| `/.env`, `/.git/config` | 403 from Vercel's firewall, which then challenged this address. Not exposed |

## 9. Audit trail

Every posted or cancelled sale, purchase, payment, voucher, adjustment and return has an audit row with user, action, shop and address (invariant S8, checked after every scenario). Order steps are kept in `OnlineOrderEvent`. Lock and reopen, staff changes on a posted document, transfers, settings changes and overrides are audited (read and spot-checked in tests). Audit rows cannot be edited through any screen; the log is owner-only.
