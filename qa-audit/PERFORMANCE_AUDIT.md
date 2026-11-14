# Performance audit

All figures are measured; nothing is estimated. Raw data: `test-results/live-erp-probe.json` and `test-results/staging-performance.json`.

## 1. Live ERP, read-only (13 Nov 2026, 23:34 PKT)

* **Environment:** the production ERP at the address documented in the repository (Vercel, function region `bom1`; requests entered Vercel's edge in `sin1`). Measured from one connection in Pakistan.
* **Method:** `scripts/live_readonly_probe.py`: 18 addresses x 12 requests, one at a time, 0.4 s apart. Unauthenticated GETs only: public pages, private pages (which must redirect) and the order API without a key (which must refuse). The first request of each address is reported apart because it may include a cold start.
* **Not measured, and why:** signed-in pages (dashboard, POS, reports, inventory) because the audit had no live login and does not sign in to production; Core Web Vitals and real-browser timing (BLOCKED for the same reason, and the website's live address is not in the repository).

| Address | Answer | First request | Median | p95 | Max | Size |
|---------|--------|--------------:|-------:|----:|----:|-----:|
| `/health/` (one database query) | 200 | 1,398 ms | 551 ms | 1,403 ms | 1,403 ms | 184 B |
| `/login/` | 200 | 913 ms | 621 ms | 867 ms | 867 ms | 1.5 KB |
| `/` | 302 | 784 ms | 523 ms | 671 ms | 671 ms | 0 |
| 7 private pages (redirect to login) | 302 | 424 - 517 ms | 439 - 475 ms | 473 - 707 ms | | 0 |
| `/api/online/config/`, order status (no key) | 401 | 438 - 469 ms | 451 - 460 ms | 503 - 509 ms | | 40 B |
| `/static/pos.js` | 200 | 471 ms | 365 ms | 420 ms | 420 ms | 23.6 KB |
| `/admin/`, unknown photo | 404 | 429 - 441 ms | 452 - 458 ms | 518 - 595 ms | | 179 B |

Error rate: 0 of 192 requests to the 16 application addresses (the 24 to `/.env` and `/.git/config` were refused by Vercel's firewall, which is correct).

**Reading the numbers.** Even a redirect that does no database work takes about 450 ms, so roughly 400+ ms of every request is fixed cost: the trip to Vercel and the Python function start-up. The database-backed `/health/` adds about 100 ms on a warm instance and up to 1.4 s on a cold one. A signed-in page also re-verifies the Supabase session every 30 seconds (one more network call). Static files go through the same Python function (finding P1), cached for only 60 seconds by browsers, so a first page view pays that cost about ten times over.

## 2. Staging: page cost with a realistic dataset

* **Environment:** isolated PostgreSQL 17 on the test machine (`covison_staging`), the application and database on one machine, no network. These are the server's own times; they show which pages are expensive and how they grow, not what a user in a shop sees.
* **Dataset:** the application's own 90-day business generator run three times on two shops: 4,677 documents, 11,752 ledger rows, 3,962 stock movements, 1,439 phones, 1,872 IMEIs, 421 customers and suppliers, 55 products, 6,447 audit rows. About 8,400 business events, 0 generator crashes.
* **Method:** `scripts/staging_performance.py`: each page 7 times as the owner, a shop login or the online login; the first is discarded; SQL statements counted with Django's query capture.

Final figures (after fixes B9 and B10):

| Page | Who | SQL | Median | p95 | Size |
|------|-----|----:|-------:|----:|-----:|
| Dashboard (all shops) | owner | 39 | 141 ms | 176 ms | 23 KB |
| New Sale | shop | 41 | 71 ms | 77 ms | 33 KB |
| New Sale (all shops) | owner | 63 | 98 ms | 106 ms | 49 KB |
| Phone inventory | shop | 8 | 25 ms | 30 ms | 11 KB |
| Sales history (and page 3, and search) | shop | 8 | 83 - 91 ms | 109 - 122 ms | 20 KB |
| Payments list | shop | 8 | 116 ms | 156 ms | 19 KB |
| Customers | shop | **8** (was 175) | **33 ms** (was 240) | 42 ms | 27 KB |
| IMEI search, exact / partial | shop | 8 / 10 | 19 / 29 ms | 20 / 32 ms | |
| Sale detail / receipt / phone history | shop | 20 / 14 / 12 | 39 / 26 / 36 ms | | |
| Transaction history | shop | 11 | 341 ms | 376 ms | 30 KB |
| Cash and bank | shop | 14 | 325 ms | 463 ms | 19 KB |
| Customer dues / supplier dues | shop | 12 | 78 / 30 ms | | |
| Returns: find a sale | shop | 49 | 75 ms | 108 ms | 22 KB |
| Profit and loss / trial balance / balance sheet (1 year) | owner | 14 - 15 | 39 - 49 ms | | |
| **General ledger (1 year)** | owner | 12 | **1,921 ms** | 2,109 ms | **1.78 MB** |
| **Stock ledger** | owner | 12 | **734 ms** | 812 ms | 474 KB |
| Sales register (1 year) | owner | 11 | 318 ms | 356 ms | 262 KB |
| Mobile inventory report | owner | 12 | 364 ms | 371 ms | 219 KB |
| Device profit (1 year) | owner | **12** (was 665) | **399 ms** (was 1,574) | 418 ms | 287 KB |
| Branch comparison | owner | 18 | 54 ms | 88 ms | 18 KB |
| Audit log | owner | 7 | 58 ms | 78 ms | 28 KB |
| Online dashboard | online | **260** | 339 ms | 349 ms | 19 KB |
| Products & photos | online | **238** | 313 ms | 335 ms | 22 KB |
| Online orders / stock / new order | online | 17 / 25 / 29 | 27 / 45 / 45 ms | | |
| Live badge poll (every screen, repeatedly) | shop | 15 | 21 ms | 24 ms | |

## 3. Staging: concurrency

For each level the shops were stocked with 80 identical new phones. Then 170 requests were released together on that many threads, each with its own session and database connection: 50 website orders (more than half the stock can satisfy once the counter sales are counted, so some must lose), 40 counter sales naming exact phones, 40 IMEI searches, 40 customer payments.

| Threads | Requests | Wall time | Requests/s | Server errors | Online orders 201 | Counter sales 200 / 400 | Phones sold / held / free | Counter sale median / p95 | Payment median / p95 | Online order median / p95 |
|--------:|---------:|----------:|-----------:|--------------:|------------------:|------------------------:|--------------------------:|-----------------------:|---------------------:|-----------------------:|
| 1 | 170 | 25.9 s | 6.6 | 0 | 50 | 23 / 17 | 23 / 50 / 7 | 194 / 318 ms | 100 / 214 ms | 126 / 347 ms |
| 4 | 170 | 14.6 s | 11.6 | 0 | 50 | 22 / 18 | 22 / 50 / 8 | 501 / 635 ms | 242 / 489 ms | 357 / 494 ms |
| 8 | 170 | 14.9 s | 11.4 | 0 | 50 | 20 / 20 | 20 / 47 / 13 | 1,351 / 1,749 ms | 607 / 1,392 ms | 487 / 925 ms |
| 16 | 170 | 18.8 s | 9.0 | 0 | 50 | 20 / 20 | 20 / 49 / 11 | 2,896 / 6,204 ms | 1,104 / 5,218 ms | 820 / 2,672 ms |
| 32 | 170 | 16.7 s | 10.2 | 0 | 50 | 34 / 6 | 34 / 15 / 31 | 4,567 / 10,068 ms | 2,249 / 8,596 ms | 2,187 / 4,766 ms |

The sold / held / free columns count the 80 phones bought for that level (they always add to 80). Website orders may also take identical phones left free by an earlier level, which is why 50 orders can succeed while fewer than 50 of the new phones are held. What matters is checked after the run:

* **0 server errors, 0 crashes, 0 deadlocks** at every level.
* **No phone sold twice, none both sold and held, no accessory oversold**; after the load the 21 audit invariants, the 18 books checks and the online check found **0 problems** across the whole staging database.
* A counter sale refused with 400 is a phone the website had already reserved (or the other way round): the intended outcome of the race.

**Where the time goes.** A sale or a payment locks its shop row (`select_for_update` on `Branch`) for the whole posting so that the books of one shop are written one transaction at a time. That is what keeps them correct; it also means a shop's postings queue behind each other. Throughput peaks at about 11 postings per second on this machine; latency then grows with the queue. For the business's scale (a few counters per shop) four or eight concurrent postings in one shop is already more than realistic, and stays under two seconds.

## 4. Infrastructure observations (Django on Vercel with Supabase)

| Topic | Finding |
|-------|---------|
| Connection reuse | With the transaction pooler (port 6543) the app keeps its connection for 300 s, prepared statements off, server-side cursors off: correct for Supabase's pooler (`config/deploy.py`, tested) |
| Serverless time limit | The General Ledger already takes about 2 s of server time on one year of a modest dataset; with several years of history it will approach the function's time limit (P3) |
| Cold starts | First request up to 1.4 s on the live site |
| Background work | None exists, so nothing can silently fail in the background. The flip side: depreciation, prepaid release and order follow-up depend on someone pressing a button |
| Login throttling | Counters live in the database cache on Vercel so every instance shares them (correct) |
| Session check | One extra call to Supabase at most every 30 s per user |

## 5. Recommendations, in order of value

1. Serve `/static/` from Vercel's CDN with long-lived hashed files (P1). Every page view benefits.
2. Page or default to one month the General Ledger, Stock Ledger and Sales Register (P3).
3. Stop running `sync_all()` on every view of the Online Dashboard and Products & Photos (P5).
4. Total and filter Transaction History, Cash and Bank and the record lists in SQL (P4).
5. Re-measure the live ERP signed in (a test login on a preview deployment), with a real browser for Core Web Vitals.
