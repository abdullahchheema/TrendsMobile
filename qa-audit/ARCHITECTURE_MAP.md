# Architecture map

What the system actually is, read from the source (ERP repo at commit `63b99a4`, website repo at commit `7b06333`), the migrations and the deployment files. Where the description in the audit brief differs from the code, the code is what is written here and the difference is listed at the end.

## 1. Components

| # | Component | Where | Runs on | Talks to |
|---|-----------|-------|---------|----------|
| 1 | **ERP** (Django 5.2, Python 3.12, one app `shop`, server-rendered pages with Turbo; no separate frontend build) | `offline/` | Vercel Python function (`offline/vercel.json`, region `bom1`), every path routed to `config/wsgi.py` | Supabase Postgres (through the pooler), Supabase Auth, Supabase Storage |
| 2 | **Online Store** | inside the ERP: `online_orders.py`, `online_views.py`, `online_store.py`, `online_accounts.py`, `online_access.py` | same function | same database; it is a `Branch` row with `is_online = true` and its own set of books |
| 3 | **Website** (Next.js 16, React 19, English and Urdu) | `Online-Ecom/mobileshop-ecom/` (its own git repository, ignored by the ERP repository) | separate Vercel project | reads 4 Supabase views with the public key; calls the ERP order API from its server with a shared secret |
| 4 | **Database** | Supabase Postgres | Supabase | ERP tables live in schema `covison_app` (not exposed by the Data API); 4 views are re-exposed in schema `api` for `anon` (`supabase/hosted/web-views.sql`) |
| 5 | **Auth** | Supabase Auth (`shop/supabase_auth.py`) | Supabase | the ERP keeps the provider session server-side and re-verifies it every 30 seconds |
| 6 | **Storage** | Supabase Storage | Supabase | `product-images` is public (the website needs plain URLs); `pta-evidence` and `device-images` are private, reached only with the server's secret key |

There are **no background jobs, queues, schedulers or webhooks** in either repository. Everything happens inside a request. Depreciation, prepaid release, accrual reversal and year-end close are buttons the owner presses. There is no courier integration: "dispatched", "delivered" and "delivery failed" are entered by the online team on the order page. There is no payment gateway: a prepaid order is marked paid by a person.

## 2. Entry points

| Purpose | Entry | Notes |
|---------|-------|-------|
| ERP web | `offline/config/wsgi.py` -> `config/urls.py` (111 named routes) | `manage.py runserver` locally; Vercel in production |
| ERP order API | `POST /api/online/orders/`, `GET /api/online/orders/<ref>/`, `GET /api/online/config/` | header `X-API-Key`, compared with `hmac.compare_digest`; CSRF exempt; an empty `ONLINE_API_KEY` switches the API off |
| ERP health | `GET /health/` | public, no business data |
| ERP maintenance | `manage.py verify_books`, `online_check`, `deployment_readiness`, `bootstrap_owner`, `generate_test_business`, `seed_online_store`, and (added by this audit) `audit_invariants` | all read-only except the bootstrap and seed commands |
| Website | `src/app/[lang]/(shop)/...` pages; server routes `src/app/api/{checkout,cart/lines,orders/track,store-config,revalidate,catalog-version}/route.ts` | `next dev` locally |

## 3. Users and how access is decided

| Identity | What it is in the data | Decided by |
|----------|------------------------|-----------|
| Owner | Django user with `is_superuser` | `access.staff()` returns `None` = no shop restriction |
| Shop login | a user linked to the shop's stand-in `Staff` row (one per shop, always manager + buy + sell + payments) | `access.branches(user)` = that one shop; `access.require(user, branch)` on every action; `permissions.SHOP_PERMS` (no accounting set-up, no audit log) |
| Online store login | a user with an enabled `OnlineStaff` row, no `Staff` row | `online_access.OnlineAccessMiddleware`: every request outside a fixed list of pages is refused with 403; record pages are allowed only for documents with `channel = 'Online'` and parties of the Online Store |
| Staff members | `Staff` rows with `crew = true`, no user | chosen on a sale or purchase; commission report only |
| Website customer | nobody: the website has no accounts | an order is tied to a phone number; tracking needs the order number and that phone |

Roles and per-role discount limits exist in the schema (`Role`, `Role.discount_max_pct`, `Role.refund_max`) but are **retired in code**: `permissions.staff_permissions` gives every shop person the same permissions and `overrides.limit_for` returns 100%. See the business-rule register, item R17.

Row Level Security is not used. The ERP connects as one database role (`covison_app`) and enforces access in Python. The Data API (`anon`, `authenticated`) cannot see schema `covison_app` at all; it can only `SELECT` four views in schema `api`.

## 4. Data model (schema `covison_app`, Django app `shop`)

```
Branch (shop or Online Store; lock_through) ─┬─ Warehouse ─ StockMovement (qty, value) ── Product
                                             ├─ Till                                      │
                                             ├─ Staff ── StaffRate (commission history)   ├─ PhoneModel
                                             ├─ ShopSettings (posting accounts JSON,      ├─ ProductImage
                                             │   payment methods JSON)                    └─ Device ── IMEI (number = primary key)
                                             ├─ Party (customer / supplier, per shop)
                                             └─ Document (17 kinds, Draft/Submitted/Cancelled)
                                                  ├─ Line (product, device, qty, rate, cost, pricing snapshot)
                                                  ├─ LedgerEntry (branch, account, party, debit, credit)   <- the only source of money figures
                                                  ├─ StockMovement
                                                  ├─ Installment
                                                  └─ Audit / RequestKey (idempotency)
Account (chart, root_type)      FixedAsset   Deferral   Loan   SalesTaxRule
OnlineOrder ─ OnlineOrderLine ─ OnlineAllocation (which shop, which exact phone)   OnlineOrderEvent   OnlineOrderAck
OnlineSettings   OnlineDeliveryRule   OnlinePaymentAccount   OnlineStaff   SupabaseIdentity
```

Constraints the database itself enforces (found in the migrations):

| Invariant | Constraint |
|-----------|-----------|
| One IMEI, one phone | `IMEI.number` is the primary key (15 characters) |
| At most two IMEIs per phone, slots 1 and 2 | `imei_device_slot` unique, `imei_slot_valid` check |
| A ledger row is never negative | `ledger_nonnegative` check |
| One order per website reference | `OnlineOrder.external_ref` unique |
| One daily closing per shop and day | `one_daily_closing` partial unique |
| One open shift per login, per till, per cashier | `one_open_shift`, `one_open_till`, `one_cashier_shift` partial unique |
| One party name per shop and kind | `party_branch_kind_name` |
| Asset, deferral, loan, tax-rate and commission-rate sanity | check constraints on each |
| Idempotency of a submitted form | `RequestKey.key` primary key |

Invariants that exist only in application code (no database constraint): a document's ledger rows balance; a phone has one status and one shop; a phone is on one unreturned sale; a phone is held by one open order; `Document.outstanding` stays between 0 and the total; stock never goes negative. These are protected by `select_for_update` locks in `services.py` and are what the audit's concurrency tests and `tests_audit_invariants.py` check.

## 5. The website's read path

```
Browser ──> Next.js server ──(public key, schema "api", SELECT only)──> api.web_families / web_listings / web_variants / web_images
                                  cached "catalogue" tag, about one minute            │ plain views over covison_app tables
ERP change ──> website POST /api/revalidate (same shared secret) ──> cache dropped    ▼
                                                                      price, stock total, specs, photo URL. Never cost, IMEI, shop or customer.
```

Columns were checked by test (`tests_audit_online.ApiRules.test_the_public_catalogue_views_show_nothing_private`). Three more views exist in the ERP schema (`web_variant_stock`, `web_stock`, `web_phone_units`); they name the shops, and `web-views.sql` deliberately drops them from schema `api`.

## 6. The order path (website checkout)

```
1 Browser   POST /api/checkout {orderId, items:[{key, qty}], expectedTotal, payment method, name, phone, address, province}
2 Website   re-reads the variants from the catalogue, re-prices the cart, takes delivery pricing from the ERP's /api/online/config/,
            builds ref = TM-<orderId>-<HMAC(ERP_API_KEY, orderId:last 10 digits of the phone)>
3 Website   POST <ERP>/api/online/orders/  X-API-Key   {external_ref, customer, payment_method, delivery_fee, expected_total, items[{product_id, condition, pta_status, device_type, price, quantity}], client_ip}
4 ERP       online_orders.create(strict=True), one transaction:
              same external_ref again            -> the same order (200)
              order-rate limits per phone and per address (database-backed)
              product must be published; a phone variant must exist at exactly that price; accessory price is the ERP's
              locks the product rows (one order at a time per product), re-checks the reference
              delivery fee must equal the ERP's own figure for that province; expected_total must equal the ERP's total
              reserves the exact phones (status Available -> Reserved) or counts the accessories
5 ERP       201 {order, external_ref, status, subtotal, delivery_fee, total, created}; 409 price/stock changed; 400 invalid; 429 too many; 401 bad key
6 Website   drops the catalogue cache; shows the order number
```

What the ERP **trusts** from the caller holding the key: an order-level `discount` (documented; the current website does not send one). Since 14 Nov 2026 a `payment_status: "Paid"` from the API is ignored: a prepaid order is paid only when the online team confirms it, which books the money.

## 7. The online order lifecycle and its postings

```
Unconfirmed ──confirm (COD: phone call; prepaid: only after "payment confirmed")──> Confirmed ──dispatch(courier, tracking)──> Dispatched ──deliver──> Delivered
     │ cancel (reason)                                                                  │ cancel                                  │ delivery failed ──> receive returned ──> Returned
     └──────────────────────────────────────> Cancelled <───────────────────────────────┘                                  customer return after delivery (Returns & Exchanges) ──> order shows Returned
```

Since the owner's decisions of 14 Nov 2026 (D4: revenue at delivery; D5: prepaid money is an advance):

| Step | Stock | Online Store books | Supplying shop's books |
|------|-------|--------------------|------------------------|
| Order placed / confirmed | exact phone `Reserved`; accessories counted as held | nothing | nothing |
| Payment confirmed (prepaid) | none | Dr Bank or wallet / Cr Receivable (the customer's advance) | nothing |
| Dispatch | phone stays `Reserved` and in the shop's stock; accessories stay held | nothing | nothing |
| Deliver | phone `Sold`; stock movement out of the shop's warehouse at cost | Dr Receivable (total) / Cr Sales (goods + delivery, less discount); Dr Cost of goods / Cr Inter-shop clearing (cost). COD: Dr Cash (chosen account) / Cr Receivable. Prepaid: the advance is applied (journal within Receivable) | Dr Inter-shop clearing / Cr Inventory (cost) |
| Delivery failed | none (nothing moves until the parcel is physically back) | none | none |
| Parcel received back | the reservation ends: each phone back on the shelf of the shop that sent it, `Holding` / `Available` / `Unavailable` by the inspection result, at the cost it always had | COD: nothing. Prepaid: an invoice for the delivery charge alone, paid from the advance; the rest of the advance is owed back | nothing |
| Pay a shop | none | Dr Inter-shop clearing / Cr Cash or bank | Dr Cash or bank / Cr Inter-shop clearing |

Orders dispatched before the change were invoiced at dispatch; they keep the old path for delivery and for a failed delivery (reversal by sales return), detected by the presence of their invoice.

The Online Store has a warehouse row and an inventory account but nothing is ever posted to either (checked by invariant `online_store_owns_no_stock`).

## 8. Shop transaction flows

| Operation | Service | Ledger (shop's books) | Stock |
|-----------|---------|-----------------------|-------|
| Purchase receipt | `services._receive` | Dr Inventory / Cr Stock received not billed | + phones (`Available`) or + accessory quantity at cost |
| Supplier bill | `services._bill` | Dr Stock received not billed / Cr Payable (supplier) | none |
| Supplier payment | `services._payment` | Dr Payable / Cr Cash or bank | none |
| Sale | `services._sale` + `pricing.calculate` | Dr Receivable (total) / Cr Sales (net) / Cr Tax (tax); Dr Cost of goods / Cr Inventory | phone `Sold`; accessories out at moving average |
| Customer payment | `services._payment` | Dr Cash or bank / Cr Receivable | none |
| Sale return | `returns.post` | mirror of the sale for the returned lines | phone `Holding` at original cost; accessories back at the cost they left at |
| Refund | `money.record_party_cash_back` | Dr Receivable / Cr Cash or bank | none |
| Supplier return | `purchase_returns.create` | Dr Payable / Cr Inventory (+/- cost difference to Cost of goods) | phone `Returned to supplier`, IMEI released |
| Stock adjustment | `services._adjust` | loss: Dr Shop expense / Cr Inventory; gain: Dr Inventory / Cr Cost of goods | phone `Written off`; accessories +/- |
| Transfer (owner) | `transfers.py` | dispatch: Dr Clearing / Cr Inventory in the sender; receipt: Dr Inventory / Cr Clearing in the receiver | `Reserved` -> `In transit` -> `Available` in the other shop |
| Voucher (expense, capital, drawings, income, loan) | `services._voucher` | against a cash or bank account | none |
| Exchange / trade-in | `exchange.post` + `services.settle_exchange` | sale + purchase of the old phone, offset, difference paid or received | old phone into `Holding` |
| Daily closing | `services.submit` (`Mobile Daily Closing`) | counted differences to Cash short and over | none; closes the day for further posting |
| Depreciation, prepaid release, accrual, year-end | `advanced.py`, `yearend.py` | journal entries | none |

Every posting goes through `services.submit` (or a function with the same pattern): one database transaction, shop row locked first, document locked, `open_date` (not in the future, not locked, not after a daily closing), postings, `balanced()` check, audit row. Idempotency for screens is `services.once(request_key, ...)`.

## 9. Environment variables (names only)

| ERP (Vercel) | Website (Vercel) |
|--------------|------------------|
| `DJANGO_SECRET_KEY`, `DATABASE_URL`, `DATABASE_SSLMODE`, `DATABASE_CONN_MAX_AGE`, `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY`, `SUPABASE_SECRET_KEY`, `ONLINE_API_KEY`, `WEBSITE_REVALIDATE_URL`, `DJANGO_ALLOWED_HOSTS`, `COVISON_TRUSTED_ORIGINS`, `COVISON_HTTPS`, `COVISON_SESSION_HOURS`, `COVISON_LOGIN_MAX_FAILURES` | `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (public by design), `ERP_API_URL`, `ERP_API_KEY` (server only), `NEXT_PUBLIC_APP_URL` |

`ONLINE_API_KEY` (ERP) must equal `ERP_API_KEY` (website). The website refuses to start in production if a secret-looking name is prefixed `NEXT_PUBLIC_` (`src/lib/env.ts`), and no file marked `"use client"` reads `ERP_API_KEY` (checked by search).

## 10. Where the brief and the code differ

| The brief says | The code does |
|----------------|---------------|
| Returned phones always enter Holding | True for customer returns. A failed delivery received back lets the online team choose Holding, straight back to Available ("approved"), or Unavailable ("damaged") |
| Courier marks delivery; webhooks | No courier integration and no webhooks. A person presses the buttons |
| Stock reservation expiry | There is none. An unconfirmed order holds its phone until someone confirms or cancels it; spam is limited by order-rate limits instead |
| Manager overrides for discounts and refunds | Retired: every login is a manager and the discount limit is 100%. Only the customer credit limit still needs a manager PIN |
| Staff commission liability in the books | Commission is a report only; nothing is posted |
| Shop login is an "accountant" with restricted finance | The shop login sees its own shop's profit, cash and bank and can post expenses, capital and drawings; loans, journals, assets, period lock and year-end are owner-only |
| Supabase Row Level Security | Not used; the ERP schema is simply not exposed. The website's views are the only thing `anon` can read |
