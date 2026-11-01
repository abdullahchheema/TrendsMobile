# Main Covison CRM

The selected application is **Django + self-hosted Supabase**. The local promotion command checks the source, backs up both systems and changes the original main address to this application. Actual activation is recorded in [main-crm-promotion.json](artifacts/main-crm-promotion.json).

## Open and start

- On this computer: **http://localhost:8080/desk/mobile-shop**
- On the current private LAN: **http://10.202.44.209:8080/desk/mobile-shop**
- The previous LAN address at port **8081** remains an alias of the same application/database.

Use the existing Covison owner account. Credentials remain in `supabase/private/SERVER-LOGIN.txt`; migration does not reset the password or create staff accounts. The former Frappe login is preserved with the original installation.

From the project root, start the selected stack with:

```sh
sh supabase/start.sh
python3 supabase/promote_main.py status
python3 supabase/control.py ps
```

The protected server environment records `COVISON_MAIN_CRM_ENABLED=1` and a pinned `COVISON_MAIN_APP_IMAGE` after promotion. `control.py` then includes `compose.main.yaml`, exposing loopback and the configured private IPv4 address at port 8080 while retaining the 8081 alias. A generic Frappe startup command is not the selected startup path.

## Simple counter entry

Use **New Sale** and **Buy Stock** for one-save retail entry. Phone records use PTA / Non-PTA status and stock references. **Sales & Accounts** shows the selected operating shop’s posted sales, credit balances and profit. See [Simple counter guide](../offline/EASY_RETAIL_GUIDE.md).

## Customer entry

Customer fields accept typed names with optional suggestions. New names are saved with the draft or POS transaction; existing names reuse their accounts. See [Customer entry guide](../offline/CUSTOMER_ENTRY_GUIDE.md).

## Reports

Open **All reports** in the sidebar or <http://localhost:8080/reports/>. The catalogue includes the 13 original mobile-shop reports, Stock Ledger and three financial statements. Original shop-facing labels and staff shortcuts are restored. Filters, date presets, row search, sorting, scoped source links, totals, CSV export and Print / Save PDF are described in [REPORTS.md](REPORTS.md).

## Data and original installation

Promotion compares every previously imported core source row and the eight inventory/accounting control totals. Business fields must match. A company `modified` timestamp change is retained as a separate source revision; the original source row stays intact. It retains the target business records and adds omitted original tax-template evidence as raw source records with a checksum-tracked import record. Source template evidence does not enable new sales-tax profiles or reprice invoices.

The original Frappe application, scheduler, workers and websocket service are stopped and their automatic restart is disabled. Its database, site files, images and containers are retained, along with a complete database/files/site-configuration backup in the protected promotion directory. The older standalone Django prototype is also stopped. Avoid independently starting archived applications: all new work belongs in the selected main CRM.

The promotion stores before/after fingerprints for application business tables, original container restart settings, source checksum, backup checksums and the release image tag. No posted records are deleted or rewritten by the address switch.

## Rollback

Inspect the status and last completed promotion record first. The guarded command is:

```sh
python3 supabase/promote_main.py rollback
```

Rollback compares the current main business fingerprints with the pre-switch values. If anything changed, it refuses to switch back automatically: stop new trading and reconcile those changes before a reviewed rollback. It does not overwrite the new database or silently discard transactions.

If unchanged, rollback restores the previous Supabase routes/environment and original container restart policies, starts the formerly running original services and checks the original address. The preserved Supabase data and source evidence remain available. Both private environment files and backup archives contain sensitive credentials; keep their permissions and use a protected independent backup destination.

## Client deployment remains separate

Selecting the main application on this computer does not certify the final shop installation. The actual four-shop/staff setup, demo-data boundary, approved tax treatment, legacy-shift count, equipment/LAN testing, final server architecture/HTTPS, independent backups and client acceptance remain in [CLIENT_HANDOVER.md](CLIENT_HANDOVER.md). The current release image is Linux ARM64.

For future changes, build/test a separate candidate image, then use a new release tag and the guarded rollout process. Do not overwrite a retained release image tag as an update strategy.
