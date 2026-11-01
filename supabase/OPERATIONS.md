# Backup and recovery guide

These tools operate on the separate **Covison Supabase preview**. They do not back up or stop the original Frappe/MariaDB application. Use Python **3.11 or later**, Docker and the checked-in Covison Compose wrapper. Daily shop users only need a browser.

## Current automatic schedule

The current Mac has the `com.covison.erp.backup` user LaunchAgent installed. It checks at minutes 00 and 30 and on loading. The application decides when a backup is due using **Asia/Karachi**, independently of the Mac's timezone:

- One full backup each day after **02:00 Pakistan time**. The job usually starts at the next half-hour check; a missed run is caught up when the computer/session/Docker are available.
- A restore rehearsal on the first scheduled backup, then when the latest verification is at least seven days old. It restores into a temporary database and volumes, compares data, and removes the temporary resources.
- An optional verified second copy to an already-mounted destination. This is **not configured yet**; the client has not supplied a destination.
- No automatic deletion of old backups. Monitor capacity and agree retention before removing older copies. A minimum free-space check is a guard, not a guarantee that a large backup will fit.

This is a Mac **user-session** schedule. The Mac must be awake, the user logged in and Docker available. It does not enable Docker Desktop login startup, wake the Mac, configure a UPS, or guarantee operation before login. Install and test scheduling on the actual shared server before production use. Keep the project at its installed path; moving it requires updating the scheduler.

```sh
python3 supabase/operations.py status
python3 supabase/operations.py run
python3 supabase/operations.py scheduled
```

`run` forces a new backup; `scheduled` checks whether one is due and retries pending verification/copy work. Both return a nonzero exit code on failure. The latest status is `supabase/private/operations-state.json`; scheduler stdout/stderr are in `supabase/private/backup-scheduler*.log`. Nothing is emailed automatically. Check status each opening day: `status` should be `ok`, checksums valid, no overdue backup, and no maintenance recovery pending. Restore verification and the second copy have separate timestamps.

Configuration is `supabase/private/operations.json`: timezone, backup hour/minute, primary directory, optional mirror directory and verification interval. It contains no account passwords. Backup archives themselves contain credentials and personal/business data: keep access limited to the server administrator. POSIX folders/files are created with owner-only permissions; on Windows use equivalent NTFS access controls. Backups are not encrypted by this script. Use protected storage and disk encryption for removable copies.

## Consistent backup and service recovery

```sh
python3 supabase/backup.py
python3 supabase/backup.py --destination /existing/backup-folder
```

The backup briefly pauses currently running preview gateways, transaction/API/Auth/storage services and administrative services while the database stays running. Finish trading first when running it manually. A payment already submitted may show an uncertain result in the browser; use the existing same-sale retry workflow after reconnection.

The tool records the previously running container IDs, stops their writers, takes the database/file snapshot, checks captured table fingerprints stayed stable, resumes those containers and waits for health checks. It only publishes a complete backup after checks pass. Previously stopped services stay stopped. A lock prevents overlapping snapshots/restores. QA and optional project services must be stopped before backup. Do not run manual SQL or other direct database writers during maintenance.

An incomplete attempt remains in a `.incomplete-*` folder and is never treated as a complete backup. If startup was interrupted, the private maintenance journal is retained until the original services become healthy. The scheduled job and `start.sh` attempt recovery; it can also be run explicitly:

```sh
python3 supabase/operations.py recover-services
python3 supabase/control.py ps
```

If containers were deliberately replaced after a crash, inspect the journal locally and reconcile service IDs before retrying; do not blindly delete it. If the database or disk failed, repair/restore that first. A restart policy alone does not recover a lost disk or a corrupted database.

Each new format-2 backup contains:

| File | Contents |
| --- | --- |
| `database.dump` | Full PostgreSQL `postgres` database, including business, Auth and storage metadata |
| `database-roles.sql` | Cluster roles/settings, including protected credential hashes |
| `storage.tar.gz` | Private storage volume with Linux extended attributes and ACLs |
| `database-keys.tar.gz` | Database encryption-key configuration |
| `server.env` | Server secrets, passwords, signing keys and deployment settings |
| `snapshot.json` | Capture time, release metadata and data fingerprints |
| `manifest.json` | SHA-256 checksums of the required backup files |

The backup does not include Docker images, the operating system or application source. Keep a matching release/source bundle and cached images for the server's CPU architecture separately. Keep backups outside version control. Earlier backup folders are preserved but lack the new snapshot metadata; the current verification command expects format 2.

## Second copy

After the owner supplies and mounts an external-drive or separate-server folder:

```sh
python3 supabase/operations.py set-mirror --directory /mounted/drive/CovisonBackups
python3 supabase/operations.py scheduled
```

The destination must already exist. The copy is published only after checksums match. A missing drive causes a reported failure and a pending retry; it does not silently create its old mount directory on the local disk. The script cannot prove a folder is on an independent machine/disk: check this physically. A second directory on the server's same disk is not protection against losing that disk. Do not treat the copy as complete until its timestamp and checksums are confirmed. There is no automatic pruning.

## Rehearse recovery without changing live trading data

Choose the folder printed by `operations.py status`:

```sh
python3 supabase/verify_backup.py --backup supabase/backups/BACKUP_FOLDER
```

It verifies required files/checksums, restores into a UUID-named disposable PostgreSQL database and file volume, and compares all captured table fingerprints and every restored file's bytes/attributes. A separate disposable file fixture checks nonempty content and Linux xattrs even if the shop currently has no attachments. It reads the key archive without replacing active encryption keys. A failed restore is reported and cleanup is attempted for every temporary resource. It never restores over the live business database.

The result is `supabase/artifacts/backup-verification.json`. Verifying the scheduled latest backup also updates the private restore-check timestamp. This tests logical database and file recovery on the current installation. It does **not** prove a fresh operating system can boot the entire stack using restored keys, every cluster role or a different CPU architecture.

## Power failure or normal server reboot

1. Restore power to the server and LAN; verify the reserved LAN IP is still assigned.
2. Start Docker/the Docker service. Existing containers use `unless-stopped`; deliberately stopped containers require explicit startup.
3. From the matching project directory, run `sh supabase/start.sh`. It uses cached images (`--pull never`, `--no-build`), checks for an interrupted backup, applies checked-in migrations and starts services without resetting volumes.
4. Check `python3 supabase/control.py ps`, then run the read-only client check below. Sign in, review recent invoices for interrupted sales and confirm one device/stock/payment before reopening all tills.
5. Inspect backup status and restore/copy timestamps. A missed backup is retried by the schedule.

```sh
python3 supabase/client_check.py --url http://SERVER_LAN_IP:8081
python3 supabase/operations.py status
```

Cached-image startup and normal service resumption have been exercised on the current Mac. A physical power-loss/reboot test, Docker startup configuration and UPS/runtime settings on the actual shop server are still pending.

## Complete machine/disk failure

Use a replacement server or isolated recovery environment; preserve the failed machine and any newer files. A qualified administrator should perform this procedure before the first production handover:

1. Select the newest complete, checksum-valid backup from the independent copy. Record its capture time and which later transactions may need reconciliation. Keep an untouched copy.
2. Install the matching application release, PostgreSQL/Supabase versions and images for the replacement CPU. Restore the saved environment and original database encryption-key volume **before initializing/starting the restored database**. Preserve the signing and encryption secrets; do not generate replacement keys with `setup_local.py`.
3. On the isolated replacement, establish the required PostgreSQL roles/settings from the saved environment and role archive. Restore the full database and storage archive with GNU tar's `--xattrs --xattrs-include=* --acls --numeric-owner` into the correct Linux volumes. The current rehearsal validates restoration into an empty disposable database; replacing the bootstrap `postgres` database needs an administrator-controlled restore with services stopped. Do not run that operation on the active shop server.
4. Verify captured database fingerprints, file bytes/attributes, Auth sign-in, storage downloads, account mappings, shop permissions, stock/ledger balances and an approved test transaction. Confirm the reserved LAN address or update it deliberately for the replacement.
5. Reinstall the backup schedule for that OS, prove the second copy works, test a full reboot and have the owner accept the recovery before reconnecting the shop PCs. Prevent the old and replacement servers from both accepting trading writes.

No command here automatically overwrites the active database. A fresh-machine rehearsal using the saved keys is required on the final deployment before promising a recovery time. Daily backups alone can lose transactions since the last capture; agree the acceptable recovery window with the owner.

## Schedule on the final server

**macOS:** `python3 supabase/operations.py install` loads the current user's LaunchAgent. It uses absolute arguments and no shell. To remove this project's schedule, use `launchctl bootout gui/USER_ID/com.covison.erp.backup`, then remove its plist from that user's `~/Library/LaunchAgents`. Do not remove another project's job. User-session/Docker limitations above apply.

**Linux:** install Python 3.11+, OS timezone data and Docker for the service account. Configure Docker to start at boot. Use the account's scheduler every 30 minutes and at boot, passing the absolute project/Python paths and a PATH containing Docker. Example crontab entries for a deployment placed at `/srv/covison`:

```cron
PATH=/usr/local/bin:/usr/bin:/bin
0,30 * * * * /usr/bin/python3 /srv/covison/supabase/operations.py scheduled >> /srv/covison/supabase/private/backup-scheduler.log 2>&1
@reboot /usr/bin/python3 /srv/covison/supabase/operations.py scheduled >> /srv/covison/supabase/private/backup-scheduler.log 2>&1
```

Use the actual Python 3.11+ executable, confirm Docker access, restrict log permissions and verify a real scheduled run. These entries are examples and were not installed on a Linux server. If trading must start automatically at boot, configure the server's service manager to run the cached startup script after Docker/network readiness; a backup schedule alone is not a trading-service startup manager.

**Windows:** use Python 3.11+ with the `tzdata` package available (bring its wheel for offline installation), Docker Desktop/WSL configured for the correct account, and Task Scheduler to run the absolute Python executable with `operations.py scheduled` every 30 minutes and at login/startup. Set the project working directory, run missed tasks promptly, prevent parallel instances, and allow access to the Docker engine, private files and mounted second-copy drive. The script uses a Windows file lock, but Windows scheduling/NTFS permissions and Docker startup have not been exercised here. Complete an actual reboot/backup/restore test on that PC before handover.
