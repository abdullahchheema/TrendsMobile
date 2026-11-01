# Covison ERP local Supabase server

This deployment uses one shared LAN server. Shop computers open the CRM in a browser; they do not each need a database or Docker installation. The server and LAN must remain available. Normal sign-in, trading and file access work without internet after the images and configuration have been installed.

Supabase provides PostgreSQL, Auth, private Storage, the Data API, Studio and Realtime. Django remains the transaction API for phone inventory, staff responsibility, POS, purchases, supplier bills, payments, advances, exchanges, returns, expenses, closing and reports.

## Current addresses

- Main CRM: **http://localhost:8080/desk/mobile-shop** or **http://10.202.44.209:8080/desk/mobile-shop**
- Previous LAN address: **http://10.202.44.209:8081/desk/mobile-shop** — an alias of the same main application/database
- Supabase Studio and API: **http://10.202.44.209:8765**
- Original Frappe CRM: backed up and stopped; preserved for guarded rollback

Studio and the CRM have separate initial logins. See **`private/SERVER-LOGIN.txt`** on the server. Studio is for the owner/administrator; counter staff use their own CRM accounts. Server keys and database passwords are in `server/.env` and are never sent to CRM browser pages.

The server binds only to the verified private LAN address, not all network interfaces. Keep that IP reserved on the router. These are HTTP preview addresses; configure HTTPS for operational handover. Connectivity from the shop computers and Windows startup have not been tested on those machines.

## Start and stop

Run from the project root with Docker running:

```sh
sh supabase/start.sh
```

Startup uses cached images with `--pull never` and `--no-build`. It starts the database, prepares the exposed API schema, starts Supabase, applies checked-in migrations, provisions the restricted application role, and starts Django and its local proxy.

```sh
python3 supabase/control.py ps
python3 supabase/control.py logs --tail 30 crm-web
python3 supabase/control.py stop
```

Stopping retains the data. Use the Covison wrapper so the security, storage and network overlays are included. Do not use upstream reset scripts or delete volumes during normal maintenance.

## Install on another shared server

The shared server needs Docker, Docker Compose and Python 3.11 or later. Initial key generation also needs Node 16 or later, or the upstream generator's Docker fallback. On Windows, use Git Bash or WSL for the upstream shell-based key generators. Daily users need only their browser.

For a fresh installation, generate new credentials using the actual server's private LAN IP:

```sh
python3 supabase/setup_local.py --host SERVER_LAN_IP
python3 supabase/configure_app.py
python3 supabase/control.py pull db auth rest storage imgproxy meta realtime studio api-gw crm-proxy
python3 supabase/control.py build crm-web
sh supabase/start.sh
python3 supabase/control.py exec crm-web python manage.py bootstrap_supabase_owner
```

Download/build images once while connected, or transport matching Docker images on a drive and load them with `docker load`. Image bundles must match the shared server's CPU architecture. Current setup scripts preserve an existing `.env`; they do not rotate keys or passwords on repeated runs.

An existing server is restored from its protected backup, including its original secrets and database encryption keys. A fresh `.env` is not a replacement for lost encryption keys.

## Data migration and access

The original Frappe/MariaDB CRM remains intact, backed up and stopped after the authorized local main-address promotion. `scripts/export_offline_snapshot.py` creates a read-only export; its checksum manifest is saved in `artifacts/source-manifest.json`. The snapshot is mounted read-only from `offline/imports/current-crm.json`.

```sh
python3 supabase/import_crm.py
python3 supabase/control.py exec crm-web python manage.py verify_migration
```

The importer accepts an empty business database, checks the snapshot checksum, preserves the raw source records and reconciles stock at warehouse level and financial totals. Reimporting the same snapshot creates no duplicates. It will not merge a newer export over a database that has already begun trading.

The verified copy contains 39 phones, 52 synthetic IMEIs, stock worth PKR 2,593,163, customer invoice outstanding of PKR 205,000 and supplier invoice outstanding of PKR 1,101,454. All 13 reports contain data. Historical cancelled USD documents retain their original currency; active trading documents use PKR.

Application tables are in the private `covison_app` schema. Anonymous and authenticated Supabase browser roles have no access to it. The Data API exposes only the dedicated `api` and `graphql_public` schemas. Money and stock changes pass through Django's atomic services rather than direct browser table writes.

Staff logins are created through **Staff login accounts**. Supabase identities are explicitly linked to local staff users; a valid Supabase account alone grants no CRM access. Staff/shop permissions remain in the transaction API. Supabase access and refresh tokens stay in server-side sessions. Public signup, phone/SMS login and external OAuth providers are disabled. Offline password login does not need email delivery; email-based invitations/recovery require a separately configured mail service.

PTA evidence is in a private Supabase Storage bucket. Downloads pass through authenticated, shop-scoped Django routes. Storage uses a Docker named volume because Docker Desktop bind mounts did not support the file backend's required extended attributes.

## Backups and verification

```sh
python3 supabase/backup.py
python3 supabase/verify_server.py
python3 supabase/verify_app.py
python3 supabase/control.py run --rm --no-deps crm-qa
```

Backups include the full PostgreSQL database, cluster roles, file storage with extended attributes, database encryption keys, server secrets, captured data fingerprints and SHA-256 checksums. They briefly pause preview writers and restore their prior running state, publishing only a complete checked snapshot. Keep backups private and copy them off the server. Self-hosting does not provide a managed cloud backup service.

The [operations guide](OPERATIONS.md) covers the installed Mac daily schedule after 02:00 Pakistan time, weekly restore verification, interrupted-backup recovery, a second-copy destination and final-server scheduling. The second copy is awaiting a destination; Mac scheduling requires this user session and Docker. Run `python3 supabase/operations.py status` to inspect backup freshness and checksums.

`verify_backup.py --backup BACKUP_FOLDER` checks restoration into a disposable database and storage volume and removes those temporary resources. It compares against that backup's captured fingerprints rather than fixed demo totals, and checks file bytes and extended attributes. It never restores over the live database. Full replacement-server boot/recovery with saved encryption keys still needs a rehearsal on the actual server.

Verified artifacts:

- `artifacts/server-verification.json` — Auth, API keys, RLS, storage and offline isolation.
- `artifacts/application-http-verification.json` — real Supabase owner sign-in and all original navigation targets.
- `artifacts/transaction-tests.log` — isolated transaction, concurrency, permission and provider tests.
- `artifacts/migration-verification.json` — imported stock, ledger and balance reconciliation.
- `artifacts/backup-verification.json` — restored data and file metadata.

Supabase's optional pooler, Edge Runtime and analytics are not started. The selected application uses Django for transaction logic. Core backend services have no internet egress; the gateways connect the LAN to the isolated service network.

## Handover status

The [four-shop setup tools](artifacts/SHOP_SETUP_STATUS.md) are deployed at `/setup/` for the owner. They include atomic empty-shop creation, posting/payment-account configuration, staff roles, default warehouses and setup checks. See the [owner guide](../offline/SHOP_SETUP_GUIDE.md). Actual shop/staff entries are awaiting the client's details and the decision about the existing imported shop; no placeholders were created. The latest suite passed 263 automated application tests and 132 isolated HTTP checks, with 74 screens reviewed in headless Chrome.

[Inter-shop transfers](artifacts/TRANSFER_STATUS.md) are implemented for the owner, preserving exact device identity, location and stock value with balanced inventory/clearing postings in each shop. See the [transfer guide](../offline/TRANSFER_GUIDE.md) for setup, physical handover, reporting and correction. Ordinary trading does not require a clearing mapping; cross-shop transfers require it for both shops. No actual additional shops/accounts were created.

The [Step 3 device checks](artifacts/DEVICE_ACCEPTANCE_STATUS.md) are deployed at `/device-check/`: scanner diagnostics, 58/80 mm and A4 test sheets, server-aware POS submission and safe same-request retries. See the [onsite acceptance guide](../offline/DEVICE_ACCEPTANCE.md). Browser simulations and this Mac passed; physical printers/scanners and connectivity from all four premises still need verification. No sales can be posted while the shared server is unreachable.

[POS cash handed/change](artifacts/CASH_STATUS.md) is implemented with split and partial payments, exact-paisa previews, server-side validation, receipt evidence and net-only till/ledger postings. See the [cashier guide](../offline/POS_CASH_GUIDE.md).

[Accessory and partial-quantity returns](artifacts/RETURN_STATUS.md) are implemented with original-line prices/costs, quantity limits, credit application to unpaid invoices and separately recorded refunds. Sales & Returns now includes POS invoices. See the [return guide](../offline/RETURN_GUIDE.md). New invoices capture discounts and tax for exact partial reversals. Uncaptured historical adjustments and ambiguous legacy history require review.

[Same-shop cash/bank/wallet transfers](artifacts/FUND_TRANSFER_STATUS.md) are implemented with two-sided ledger postings, explicit till custody, staff reporting and linked reverse drafts. See the [transfer guide](../offline/FUND_TRANSFER_GUIDE.md). This records completed movements and does not initiate bank/wallet payments.

POS opening cash and per-till physical closing are implemented in the preview. See [the till guide](../offline/TILL_GUIDE.md) for setup, cashier workflow, bank-payment exclusion, differences and legacy shift reviews. Closed-shift payment cancellation is blocked to preserve the counted snapshot. Till opening/closing records do not create automatic ledger adjustments.

[Sales discounts and configurable tax](artifacts/PRICING_STATUS.md) are deployed in sales drafts and POS. Receipts and reports distinguish discounted revenue, tax and customer totals; partial returns reverse the original captured amounts. The owner can add client-approved rules under **Shop setup → Sales tax rules**. No primary rules were created, so sales default to no tax added. See the [invoice pricing guide](../offline/INVOICE_PRICING_GUIDE.md). This covers one exclusive invoice-wide rate; other tax treatments and purchase taxes remain pending.

[Owner accounting adjustments](artifacts/JOURNAL_STATUS.md) are deployed at `/journals/`, with balanced PKR rows, linked reversal/replacement corrections, owner permissions and closed-date protection. Control accounts retain their dedicated stock/payment/party/tax workflows. See the [journal guide](../offline/JOURNAL_GUIDE.md).

The [six-step rollout status](artifacts/ROLLOUT_STATUS.md) and [client handover worksheet](CLIENT_HANDOVER.md) track the remaining work. The read-only `manage.py deployment_readiness` command found one shop, no linked operator logins, 90 submitted demo documents and one legacy open shift; actual configuration and onsite acceptance are required. A fresh backup restored 33 captured tables without changing primary records. An independent backup destination and final-machine recovery remain pending.

Supabase + Django is now the selected main CRM on this computer. The original address serves the replacement; the Frappe application and workers are preserved, backed up and stopped. See [the main CRM guide](MAIN_CRM.md) and [switch record](artifacts/main-crm-promotion.json). This local application switch does not certify the four-shop client deployment. The [Step 1 screen and feature review](artifacts/SCREEN_PARITY_REVIEW.md) covers 57 headless Chrome screens, fixes and remaining gaps. Original form layouts are not pixel-identical. Full native invoice/accounting and setup parity remains pending. Imported posted history is read-only in this version. Review these findings, actual shop names and staff permissions before switching the shops to this deployment. Physical printers and actual shop computers still need acceptance testing. Final cutover needs a fresh export with trading paused so the two systems do not receive separate live transactions.

The pinned official server release is `self-hosted/v0.8.3`; see `UPSTREAM.txt` and `UPSTREAM-LICENSE`. Key generation uses the reviewed upstream scripts and enables their asymmetric Auth settings. Covison service configuration is in `compose.covison.yaml`.

References: [Supabase self-hosting](https://supabase.com/docs/guides/self-hosting/docker), [securing the Data API](https://supabase.com/docs/guides/api/securing-your-api), [local runtime storage](https://supabase.com/docs/guides/local-development/docker-and-native-runtimes).
