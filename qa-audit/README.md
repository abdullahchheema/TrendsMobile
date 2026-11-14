# QA, accounting and security audit (13-14 Nov 2026)

Independent audit of the ERP (`offline/`), its Online Store, and the customer website (`Online-Ecom/mobileshop-ecom/`). Start with **`FINAL_AUDIT_REPORT.md`**.

| File | What it is |
|------|------------|
| `FINAL_AUDIT_REPORT.md` | Verdict, what was tested, results, what was fixed, what is open, decisions needed, deployment steps |
| `ARCHITECTURE_MAP.md` | How the parts connect: components, users, data model, the order path, every posting, environment variables, where the brief and the code differ |
| `BUSINESS_RULE_REGISTER.md` | 53 rules as implemented, each with its code location and test, plus 11 decisions for the owner |
| `TEST_PLAN.md` | Tools, safety rules, phases, invariants, and how to run everything again |
| `TEST_DATA_DESIGN.md` | The fixtures and the 90-day dataset (all invented; created through the real screens' services) |
| `ACCOUNTING_AUDIT.md` | Double entry, trial balance, P&L, cash, dues, inventory, online settlement, tax, commission, assets, period lock |
| `CALCULATION_VERIFICATION.md` | The independent oracle and the 162 comparisons |
| `SECURITY_AUDIT.md` | Access matrix, attempted changes, API, Supabase, website, live headers |
| `PERFORMANCE_AUDIT.md` | Live read-only timings, staging page costs, concurrency up to 32 threads |
| `EDGE_CASE_MATRIX.md` | Inputs, retries, races, states, dates, outside failures, volume |
| `BUG_REPORT.md` | Every confirmed defect with reproduction, cause, fix and regression test |
| `test-results/` | `calculations.csv`, `access-matrix.csv`, `live-erp-probe.json`, `staging-performance.json`, `website-local-checks.json` |
| `scripts/` | `live_readonly_probe.py` (safe for production), `staging_performance.py` (refuses anything but a local database), `website_local_checks.py` (refuses anything but localhost), `pg_settings.example.py` |

Test data is not stored as files: every test builds its own records (see `TEST_DATA_DESIGN.md`), so there is no `test-data/` folder to keep in step.

The automated tests are in the ERP repository with the rest of its tests: `offline/shop/tests_audit_*.py`, plus the read-only command `manage.py audit_invariants`.

Nothing in this folder contains a secret, a password, a key or real customer information.
