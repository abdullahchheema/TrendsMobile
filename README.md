# Covison ERP

Pakistani mobile-shop CRM with **Django transactions and self-hosted Supabase PostgreSQL, Auth and Storage**. This is now the selected main application on the current computer.

| Access | Address |
| --- | --- |
| Main CRM on this computer | http://localhost:8080/desk/mobile-shop |
| Main CRM on the current LAN | http://10.202.44.209:8080/desk/mobile-shop |
| Previous LAN address | http://10.202.44.209:8081/desk/mobile-shop — same application/database |

Use the existing Covison owner account. Its private login instructions remain in `supabase/private/SERVER-LOGIN.txt`; the switch does not reset passwords or create operator accounts.

## Start and inspect

Run from this project directory with Docker running:

```sh
sh supabase/start.sh
python3 supabase/promote_main.py status
python3 supabase/control.py ps
```

The protected environment selects the main routing overlay and a versioned application image. Use these Supabase startup instructions for the selected CRM. Original Frappe containers, database and files are retained for guarded rollback; its application and workers are stopped with automatic restart disabled.

## Features

Per-device IMEI/condition/variant inventory, PKR purchasing and supplier bills, POS and split payments/change, staff responsibility, customer credit/advances, returns, exchanges, warranty/repairs, stock transfers, tills/closing, reports, configurable sales discounts/tax and owner journal corrections.

Existing business records were preserved during the main-address switch. Original tax templates are retained as source evidence; client-approved rates have not been activated automatically.

## Operating guides

- [Main CRM, preserved original and guarded rollback](supabase/MAIN_CRM.md)
- [Server setup and deployment](supabase/README.md)
- [Shop/staff setup](offline/SHOP_SETUP_GUIDE.md)
- [Sales discounts and tax](offline/INVOICE_PRICING_GUIDE.md)
- [Owner journals and corrections](offline/JOURNAL_GUIDE.md)
- [Backup and recovery](supabase/OPERATIONS.md)
- [Client handover and onsite acceptance](supabase/CLIENT_HANDOVER.md)
- [Recorded main-address switch](supabase/artifacts/main-crm-promotion.json)

The current dataset still contains demonstration records and one imported shop. Actual four-shop/staff setup, the demo-data boundary, approved tax treatment, legacy-shift review, shop equipment/LAN, HTTPS, independent backups and client acceptance remain required before production shop handover. The current application image is Linux ARM64.

The repository also retains [the original upstream Frappe Docker README](docs/FRAPPE_DOCKER_UPSTREAM_README.md) and Frappe source/deployment files for the archived original. Those generic startup examples are not the selected main-CRM startup procedure.

Report navigation, dates, financial statements and exports: [Reports guide](supabase/REPORTS.md).
