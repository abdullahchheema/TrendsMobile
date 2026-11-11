# Covison ERP

Pakistani mobile-shop ERP by [Covison](https://covison.com): **Django transactions on self-hosted Supabase PostgreSQL, Auth and Storage**.

| Access | Address |
| --- | --- |
| Main application on this computer | http://localhost:8080/desk/mobile-shop |

Use the Covison owner account. Its private login instructions are in `supabase/private/SERVER-LOGIN.txt`.

## Start and inspect

Run from this project directory with Docker running:

```sh
sh supabase/start.sh
python3 supabase/control.py ps
```

## Features

Two kinds of user: the **Owner** (sees and controls every shop) and one **Shop Accountant** login per shop (runs that shop only and can never see another shop). One two-panel **POS** (New Sale) with IMEI scanning, split payments, credit, receipts and WhatsApp sharing; returns, refunds and exchanges from the sale page; IMEI-tracked stock per shop (no warehouses); bulk purchasing with typed brand/model/storage/colour; **Owner-only stock transfers** with Pending, Dispatched, Received, Discrepancy and Cancelled statuses; simple **Accounts & Finance** (Money In, Money Out, Expenses, Customer Dues, Supplier Dues, Cash and Bank, one Transaction History); separate Owner and Shop dashboards; reports; and the Owner's advanced accounting (chart of accounts, adjustments, month lock, year-end, fixed assets, prepaids/accruals, loans).

## Page navigation (no full reloads)

Clicking a link or saving a form swaps the page content in place ([Turbo](https://turbo.hotwired.dev), vendored in `offline/static/vendor/`); the menu, header and search stay put. The server pages, forms and business rules are unchanged. For anyone adding a screen:

- A form that saves must redirect (as now); a form shown again with errors is answered with status 422 automatically (`shop/turbo.py`).
- Page scripts that attach listeners to `document` or `window` must remove them with `covison.page.leave(fn)`; one-off setup that must run on every page uses `covison.page.ready(fn)` (`static/page.js`).
- Downloads (CSV and the like) need `data-turbo="false"`; pages that are not built on `base.html` (login, receipts, slips) carry `<meta name="turbo-visit-control" content="reload">`.

## Guides

- **[Fresh deployment on Supabase + Vercel](DEPLOYMENT.md)**
- [Server setup and deployment](supabase/README.md)
- [Shop setup](offline/SHOP_SETUP_GUIDE.md)
- [Daily counter entry](offline/EASY_RETAIL_GUIDE.md)
- [Sales discounts and tax](offline/INVOICE_PRICING_GUIDE.md)
- [Owner journals and corrections](offline/JOURNAL_GUIDE.md)
- [Reports](supabase/REPORTS.md)
- [Backup and recovery](supabase/OPERATIONS.md)
- [Client handover and onsite acceptance](supabase/CLIENT_HANDOVER.md)

The repository contains no sample data: create the shops, their logins, products and opening balances through the screens. Actual four-shop/staff setup, approved tax treatment, shop equipment/LAN, HTTPS, independent backups and client acceptance remain required before production handover. The current application image is Linux ARM64.
