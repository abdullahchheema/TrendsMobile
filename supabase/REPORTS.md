# Covison reports

Open **All reports** in the sidebar, or <http://localhost:8080/reports/>. The catalogue lists only reports permitted for the signed-in staff member. Owner access includes 17 reports.

| Area | Reports |
| --- | --- |
| Stock and devices | Phone Stock (Mobile Inventory), Stock Age & Prices (Mobile Stock Ageing), Device Profit, Device Costs & Margin (Mobile Device Economics), Stock Balance, Stock Ledger |
| Sales and purchases | Sales Register, Purchase Register |
| Staff | Staff Activity, Staff Summary |
| Financial statements | Profit and Loss Statement, Balance Sheet, Trial Balance |
| Accounts and payments | Customer Balances (Accounts Receivable), Supplier Balances (Accounts Payable), Payment Ledger, General Ledger |

## Dates and controls

Transaction reports initially show this month in **Pakistan time**. A new month can make older transactions disappear from the initial view. Choose **Last month** or **All history** (2000 through today), or enter explicit dates and press Refresh. Balance Sheet, customer/supplier balances and Stock Balance use an as-of date. Inventory uses current records; Stock Age & Prices initially includes only available/reserved phones needing price review. Its shortcut shows all available/reserved phones. Phone Stock has an all-statuses shortcut.

Select a shop and any report-specific filters. Search filters visible table columns, and clicking a column heading sorts the rows. Export CSV uses the same filtered rows and order. Print / Save PDF opens the browser print dialog; report pages use an A4 landscape layout. Thermal receipt layouts stay separate. Linked devices and documents open their source records only where the user has permission.

Table totals add explicitly identified money/quantity columns. Staff Activity has no combined money total: buying, sales and collections are different activities and adding them would double count. Sales and purchase returns are labelled separately. Historical unassigned staff activity remains visible rather than being dropped or assigned to a current staff member.

## Financial statement basis

All three financial statements read the existing posted ledger. They do not post journals, reprice devices or change opening balances.

- **Profit and Loss Statement:** period income less period expenses, including net returns, overheads and posted corrections. This is ledger profit on an accrual basis, not cash collected and not just per-device margin.
- **Balance Sheet:** assets, liabilities and equity through the chosen date. Unclosed income less expenses is shown once within equity as unclosed profit/loss. Posted closing journals already reduce unclosed earnings, so they are not counted twice. Stock is ledger valuation, not asking price.
- **Trial Balance:** account opening net balances, period debit/credit turnover and closing net balances. A nonzero debit/credit difference remains visible; the report never inserts a balancing amount.

Statement cards always show totals for the full selected shops/date period. Row search only narrows the table and CSV. This distinction is printed beside the cards. Profit and Loss and Balance Sheet have no misleading sum of mixed income/expense or asset/liability rows. Trial Balance totals each debit/credit column separately.

Account names drill into General Ledger with the account and period selected. Statement access follows the existing purchasing/management financial-report permissions and shop scope. Sales-only staff cannot access statement pages or CSV exports.

Original account root metadata is used when available; otherwise recognized account types or unambiguous configured posting/payment/tax mappings identify the section. If a used account cannot be classified, Profit and Loss and Balance Sheet stop with the account codes to review. Trial Balance and General Ledger remain available for investigation. These pages are ledger-based statements, not a replacement for every ERPNext report option, fiscal comparison, cash-flow classification or statutory filing workflow.
