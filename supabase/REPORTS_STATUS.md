# Reports release status

Completed at UTC 2026-10-31T20:39:34.035593+00:00. The main CRM now serves 17 reports at <http://localhost:8080/reports/>.

The release restores the original report labels, staff shortcuts and report controls, exposes Stock Ledger, and adds ledger-based Profit and Loss, Balance Sheet and Trial Balance pages. Earlier-month transactions are accessible through explicit date presets; no historical data was manufactured or repriced.

Validation: **302 application tests passed**; **79 browser screens checked**, including mobile layouts and absence of page/JavaScript errors. Final-image interaction checks passed for report search, numeric sorting, filtered totals, CSV exports, printing, financial reconciliation and account drilldowns. Existing POS interactions and 58/80 mm/A4 printer layouts also passed simulated browser checks; this does not certify physical shop hardware.

A consistent main-server backup was taken before activation. The previous image tag is retained. All three existing main/alias addresses passed health checks, authenticated HTTP navigation passed, migration control totals reconciled, and all 19 application business-table fingerprints stayed unchanged. The original Frappe applications remain stopped.

Evidence: [release record](artifacts/reports-release.json), [application tests](artifacts/report-features-tests.log), [browser review](artifacts/device-review/review.json), [final interactions](artifacts/device-review/interactions.json), [financial checks](artifacts/device-review/financial-checks.json).

See [Reports guide](REPORTS.md) for permissions, dates, statement totals, exports and supported scope. This release does not establish complete parity with every native ERPNext report option.
