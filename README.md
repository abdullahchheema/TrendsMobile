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

Simple one-save sales and purchases with optional IMEI capture, PTA / Non-PTA phone inventory, PKR purchasing and supplier bills, POS and split payments/change, staff responsibility, customer credit/advances, returns, exchanges, warranty/repairs, stock transfers and adjustments, tills/closing, reports, configurable sales discounts/tax and owner journal corrections.

## Guides

- [Server setup and deployment](supabase/README.md)
- [Shop/staff setup](offline/SHOP_SETUP_GUIDE.md)
- [Daily counter entry](offline/EASY_RETAIL_GUIDE.md)
- [Sales discounts and tax](offline/INVOICE_PRICING_GUIDE.md)
- [Owner journals and corrections](offline/JOURNAL_GUIDE.md)
- [Reports](supabase/REPORTS.md)
- [Backup and recovery](supabase/OPERATIONS.md)
- [Client handover and onsite acceptance](supabase/CLIENT_HANDOVER.md)

The repository contains no sample data: create the shops, staff, products and opening balances through the setup screens. Actual four-shop/staff setup, approved tax treatment, shop equipment/LAN, HTTPS, independent backups and client acceptance remain required before production handover. The current application image is Linux ARM64.
