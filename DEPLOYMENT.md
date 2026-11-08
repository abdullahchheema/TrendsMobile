# Fresh deployment: Supabase (hosted) + Vercel

This deploys the Covison ERP (the Django app in `offline/`) with:

- **Supabase** — PostgreSQL database, sign-in (Auth) and private file storage
- **Vercel** — runs the Django app; every push to `main` on GitHub redeploys it

Nothing in this repository points at any previous Supabase project, Vercel project or GitHub account. Every secret lives in
the Vercel and Supabase dashboards, never in the code. **Do not paste keys or passwords into chat, commits or issues**; if one is
exposed, rotate it in the dashboard.

> **Plan limits that matter**
> - Vercel's free *Hobby* plan is for personal, non-commercial use. A shop's ERP needs a **Pro** plan.
> - Supabase's free plan pauses idle projects and has no automatic backups. Use **Pro** (daily backups) for live trading.
> - A single request to Vercel can carry at most 4.5 MB, so PTA-evidence uploads are limited to that size (the app's own limit is 10 MB).

---

## Part 1 — Supabase project

1. In Supabase create a **New project**. Pick the region closest to the shop (for Pakistan: *South Asia (Mumbai)*) and set a strong
   database password (this is the *postgres* password; you will not need it again).
2. Open **SQL Editor**, paste all of [`supabase/hosted/setup.sql`](supabase/hosted/setup.sql), and **first replace**
   `CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD` with a new password using only letters and digits (30+ characters). Run it. This creates the
   restricted `covison_app` database user, its schema and the two private storage buckets. Keep that password: it is `DB_PASSWORD` below.
3. **Authentication → Sign In / Providers**: turn **off** "Allow new users to sign up". The owner creates every account.
4. **Project Settings → API**: copy the **Project URL**, and from the *Legacy API keys* tab the **anon** key and **service_role** key.
   (The code sends these as the `apikey` header, which is what the self-hosted setup it was tested with did. If you prefer the new
   `sb_publishable_…`/`sb_secret_…` keys, try them only after the legacy keys work.)
5. **Project Settings → Database → Connection string**: choose **Transaction pooler** and note the host, which looks like
   `aws-0-<region>.pooler.supabase.com`, and the project reference (the letters in your project URL).
6. Optional hardening: **Project Settings → Data API** → turn the Data API off. The app talks to the database directly and does not use it.

Build the two connection strings (replace the UPPERCASE parts; the user is `covison_app.` followed by the project reference):

```
Vercel (transaction pooler, port 6543):
postgresql://covison_app.PROJECTREF:DB_PASSWORD@aws-0-REGION.pooler.supabase.com:6543/postgres

One-time setup from your computer (session pooler, port 5432):
postgresql://covison_app.PROJECTREF:DB_PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres
```

## Part 2 — Create the tables and the owner (once, from your computer)

You need Python 3.11 or newer. In PowerShell, from the project folder:

```powershell
cd offline
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt

$env:DJANGO_SECRET_KEY = "temporary-setup-value"
$env:DATABASE_URL = "postgresql://covison_app.PROJECTREF:DB_PASSWORD@aws-0-REGION.pooler.supabase.com:5432/postgres"
$env:SUPABASE_URL = "https://PROJECTREF.supabase.co"
$env:SUPABASE_PUBLISHABLE_KEY = "<anon key>"
$env:SUPABASE_SECRET_KEY = "<service_role key>"
$env:COVISON_CRM_OWNER_EMAIL = "owner@yourcompany.com"
$env:COVISON_CRM_OWNER_PASSWORD = "<a strong password you will sign in with>"

python manage.py migrate
python manage.py createcachetable
python manage.py bootstrap_supabase_owner
```

`migrate` creates the ERP tables, `createcachetable` creates the small table used to rate-limit sign-in attempts, and
`bootstrap_supabase_owner` creates the owner login in Supabase Auth and links it. Close the PowerShell window afterwards so the
variables disappear. Re-running any of these commands is safe.

## Part 3 — Vercel

1. In Vercel choose **Add New → Project** and import the GitHub repository `abdullahchheema/TrendsMobile`.
2. Set **Root Directory** to `offline` (important — the app lives there). Leave the framework as *Other* and clear any build command.
3. Add these **Environment Variables** (Production, and Preview if you use previews):

   | Name | Value |
   | --- | --- |
   | `DJANGO_SECRET_KEY` | a new long random string (50+ characters) |
   | `COVISON_HTTPS` | `1` |
   | `DATABASE_URL` | the **transaction pooler** string (port **6543**) from Part 1 |
   | `SUPABASE_URL` | `https://PROJECTREF.supabase.co` |
   | `SUPABASE_PUBLISHABLE_KEY` | the anon key |
   | `SUPABASE_SECRET_KEY` | the service_role key |
   | `DJANGO_ALLOWED_HOSTS` | *(optional)* your custom domain, e.g. `erp.yourcompany.com` |
   | `COVISON_TRUSTED_ORIGINS` | *(optional)* `https://erp.yourcompany.com` |

   The `*.vercel.app` address of each deployment is allowed automatically. A list is also in
   [`offline/.env.example`](offline/.env.example). Do **not** add the owner email/password to Vercel; they are only for Part 2.
4. Click **Deploy**. The function runs in the Mumbai region (`bom1`, set in `offline/vercel.json`).

## Part 4 — Check it works

1. Open `https://<your-project>.vercel.app/health/` — it should answer `{"status": "ok", ...}`.
2. Open the site, sign in with the owner email and password from Part 2, then **change the password** (Settings → Change password).
3. Go to **Settings → Four-shop setup** and create the shops, warehouses, tills and staff logins. Create products and stock through
   **Buy Stock**. The system contains no sample data.
4. Upload one PTA evidence file on a device and open it again to confirm Supabase Storage works.

## Part 5 — Before real trading

- Turn on **Pro** plans (see the note at the top) and confirm Supabase backups are running; test a restore once.
- Add your custom domain in Vercel, then add it to `DJANGO_ALLOWED_HOSTS` and `COVISON_TRUSTED_ORIGINS` and redeploy.
- Approve the tax treatment with the accountant and configure it under the tax settings before posting invoices.
- Create one login per staff member with the narrowest role (cashier, salesperson, purchaser, manager).

## Troubleshooting

| Symptom | Likely cause and fix |
| --- | --- |
| `DisallowedHost` / 400 on the site | the address is missing from `DJANGO_ALLOWED_HOSTS` (custom domains only) |
| `CSRF verification failed` on login | add `https://your-domain` to `COVISON_TRUSTED_ORIGINS`, redeploy |
| 500 on every page, log says *relation does not exist* | Part 2 `migrate` has not run against this database, or `search_path` was not set (re-run `setup.sql`) |
| `password authentication failed` | wrong `DB_PASSWORD`, or the user is missing the `.PROJECTREF` suffix |
| `prepared statement … does not exist` | the app is not reading `DATABASE_URL` (it must be set in Vercel exactly as named); with it set the app turns prepared statements off for the pooler |
| Sign-in says the request was rejected | wrong `SUPABASE_URL` or keys, or the owner was never created (run `bootstrap_supabase_owner`) |
| Sign-in always bounces back to the login page | `COVISON_HTTPS=1` is set but the site is opened over plain HTTP, or Supabase is unreachable |
| Static files / styling missing | confirm the Root Directory is `offline` so the `static/` folder is deployed |

## Updating later

Push to `main` and Vercel redeploys. When a release adds database migrations, run `python manage.py migrate` from your computer
(Part 2 environment) **before** or right after the deploy.

## Local development without Supabase

Set only `DJANGO_SECRET_KEY` and `DATABASE_URL` (or the `DATABASE_*` variables) and leave `SUPABASE_URL` empty: the app then uses plain
Django sign-in (run `python manage.py createcachetable` once if you use `DATABASE_URL`). The folder `supabase/` also contains an optional self-hosted Docker stack for running everything on a shop server.
