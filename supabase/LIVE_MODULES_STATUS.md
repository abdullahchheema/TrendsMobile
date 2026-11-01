# Regular operating-shop modules

Completed at UTC 2026-11-01T20:42:03.011289+00:00. New Sale, Buy Stock and **Sales & Accounts** are normal operating features. Sales & Accounts defaults to the operating shop, supports shop/date filters, and reads posted ledger income, expenses, dues and advances. There is no separate demo-feature summary page; the previous `/training/` URL redirects to the standard workspace.

Selecting initial stock, variants, customers or suppliers does not automatically mark a new operating-shop transaction as demo. Source batch information is retained under Record history. Explicit sample-shop/transaction labels and previously generated history are preserved; existing generated records were not reclassified as verified actual business activity.

Validation: **356 application tests**, **136 controlled HTTP workflow checks**, **80 browser screens**, and final-image interactions passed. The normal workspace was verified without requiring a sample shop. Tests cover ledger-based balances, advances/credits, dates, permissions, unknown account classification errors, standard transaction mode, saved stock/payment effects and historical provenance.

A consistent backup preceded deployment. All 19 business-table fingerprints stayed unchanged, and all main/alias addresses and authenticated HTTP checks passed. There were no new schema migrations or historical transaction rewrites.

Open <http://localhost:8080/sales-accounts/>. See [Simple counter guide](../offline/EASY_RETAIL_GUIDE.md). Local verification artifacts remain private/ignored: `artifacts/live-workspace-release.json`, `artifacts/live-workspace-tests.log`, `artifacts/device-review/`, and `artifacts/controlled-demo/`.
