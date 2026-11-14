# Test plan

## What was tested, with what, and where

| Layer | Tool | Where it lives | Runs against |
|-------|------|----------------|--------------|
| Calculations (independent oracle) | Python `fractions` in whole paisa, no application imports | `offline/shop/tests_audit_oracle.py` | nothing: pure arithmetic |
| ERP workflows, books, access, races | Django test runner (the project's existing tool), real PostgreSQL | `offline/shop/tests_audit_*.py` | throwaway PostgreSQL 17 cluster on `127.0.0.1:55432` |
| System invariants | plain queries on the raw tables | `offline/shop/tests_audit_invariants.py`, `manage.py audit_invariants` | any database, read-only |
| Existing ERP suite (regression) | Django test runner | `offline/shop/tests*.py` (890 tests before the audit) | same throwaway cluster |
| Website units | vitest, eslint, tsc (existing) | `Online-Ecom/mobileshop-ecom/src/**/*.test.ts` | no network |
| Website end to end | local `next dev` + the repository's mock ERP, driven by HTTP requests and the browser | `qa-audit/scripts/website_local_checks.py` | `localhost:3111` + `localhost:4010` |
| Staging performance and load | in-process requests with SQL capture, threads with their own connections | `qa-audit/scripts/staging_performance.py` | isolated database `covison_staging` in the same throwaway cluster |
| Live ERP (read-only) | sequential GETs, no sign-in, no key | `qa-audit/scripts/live_readonly_probe.py` | the live ERP address documented in the repository |

No new dependency was added to either project.

## Environment safety (what was and was not touched)

* Production database: **never connected to**. `supabase/private/*` (production credentials) was not opened. The test settings file removes `DATABASE_URL`, `SUPABASE_*` and `WEBSITE_REVALIDATE_URL` from the environment before Django loads, so a test cannot reach Supabase or the live website even by accident.
* Production ERP: 216 read-only GET requests (18 addresses x 12), unauthenticated. No sign-in, no API key, no POST. Two of the addresses (`/.env`, `/.git/config`, expected to be "not found") were treated as an attack by Vercel's firewall, which answered 403 and began challenging this machine's address; probing stopped there and those two addresses were removed from the script.
* Production website: **not tested** (BLOCKED): its address is not written anywhere in either repository outside the private credentials folder.
* No real order, payment, notification, courier booking or email was created anywhere. The system has no notification or courier integration to trigger.
* No deployment was made and no environment variable was changed.
* Git: both repositories were clean at the start; nothing was committed or pushed.

## Phases and their tests

| Phase of the brief | Audit tests | Count |
|--------------------|-------------|-------|
| 3. Independent oracle | `tests_audit_oracle.py` (used by every other module) | - |
| 4. Accounting | `tests_audit_accounting.FullPeriod`, `PeriodControls`, `FailureHalfWay` | 11 |
| 5. Calculations | `tests_audit_calculations.Invoices`, `SplitPayments`, `CustomerDues`, `SupplierDues`, `Commission` | 26 |
| 6. Physical shop | `tests_audit_shop.Purchases`, `CounterDay`, `ReturnsAndExchanges`, `Transfers`, `Adjustments` | 17 |
| 7, 9. Online Store and cross-system scenarios | `tests_audit_online.NormalSale`, `FailedDelivery`, `CustomerReturn`, `ApiRules` | 19 |
| 10, 13. Concurrency | `tests_audit_concurrency.Races` | 10 |
| 11. Security | `tests_audit_security.WhoCanOpenWhat`, `WhoCanChangeWhat` | 17 |
| 14. Invariants | 21 invariants in `tests_audit_invariants.py`, run after every scenario and after the load test | 21 checks |
| 12. Performance | `staging_performance.py`, `live_readonly_probe.py` | measured |
| 8. Website | existing 95 vitest tests + `website_local_checks.py` | see `test-results/` |

Exact counts of the final run are in `FINAL_AUDIT_REPORT.md`.

## How every calculation is checked (phase 5)

1. Inputs are written in the test.
2. `tests_audit_oracle` works out the expected figures (it shares no code with the application).
3. The transaction is posted through the real entry point (for sales: `POST /api/pos/checkout`, the endpoint the New Sale screen calls).
4. Four things are read back and each must equal the oracle to the paisa: the API response, the saved documents and line snapshots, the ledger rows, and the amounts printed on the receipt or report page.
5. Each comparison is appended to `test-results/calculations.csv`.

## Invariants (phase 14)

Financial: F1 journals balance per document and per shop · F1b no negative or two-sided rows · F2 posted documents have ledger rows · F3 drafts have none · F5 a payment moved exactly its amount through cash or bank · F6/F7 customer and supplier control accounts equal the open amounts on their documents · F11 no invoice paid beyond its total, no credit used beyond its value · F13 nothing posted into a locked period.

Inventory: I1 IMEIs unique in any spelling · I2-I6 a phone's status, shop, location and stock movements agree and it is counted once · a phone is on one unreturned sale · I9 no negative stock, an empty shelf has no value · I12 stock value equals the inventory account in every shop · I8 adjustments have a reason and an audit row.

Online Store: O10 it owns no stock and no inventory value · O3/O4 each phone held by at most one open order (New or on its way), every hold is a real `Reserved` phone in the named shop, every `Reserved` phone is held by something · O1/O2/O12 one order per reference, invoiced once and only at delivery · O6 a paid prepaid order has its money booked once, as an advance · O7 undelivered COD has received no money · O9 the debt to the shops is the same in both sets of books.

Security (data ownership): S1 no document links a shop to another shop's customer or supplier · S8 every posted or cancelled transaction has an audit row.

The application's own `books_check.py` (18 checks) and `online_check.py` are run alongside; the audit invariants are a second, independent implementation of the same ideas plus the ones those did not cover.

## How to run everything again

```bash
# 1. a throwaway PostgreSQL (binaries of PostgreSQL 16 or 17 on PATH)
initdb -D pgdata -U covison -A trust -E UTF8 --locale=C
pg_ctl -D pgdata -o "-p 55432 -c max_connections=200" -l pg.log start
psql -h 127.0.0.1 -p 55432 -U covison -d postgres -c "create database covison"

# 2. settings: copy qa-audit/scripts/pg_settings.example.py to a folder outside the repository as pg_settings.py

# 3. the audit suite (about 15 minutes), writing the result tables
cd offline
AUDIT_WRITE_RESULTS=1 PYTHONPATH=<folder with pg_settings.py> python manage.py test shop.tests_audit_calculations shop.tests_audit_accounting shop.tests_audit_shop shop.tests_audit_online shop.tests_audit_concurrency shop.tests_audit_security --parallel 1 --settings=pg_settings --noinput

# 4. everything (about 45 minutes)
PYTHONPATH=<folder> python manage.py test shop --parallel 1 --settings=pg_settings --noinput

# 5. invariants against any database the settings point at (read-only)
PYTHONPATH=<folder> python manage.py audit_invariants --settings=pg_settings
```

In CI (`.github/workflows/tests.yml`) nothing needs to change: the audit tests are ordinary tests of the `shop` app and run with `python manage.py test shop --parallel 1`.
