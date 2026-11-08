# Covison ERP - first-time setup for hosted Supabase. Run this ONCE on your own computer.
#
#   1. Open PowerShell in the project folder (the one that contains "offline" and "supabase").
#   2. Run:   powershell -ExecutionPolicy Bypass -File supabase\hosted\first-time-setup.ps1
#   3. Answer the questions. Nothing you type is saved in the project or sent to GitHub.
#
# It creates the database tables, the sign-in owner account, and writes a private file with the values to paste into Vercel.

$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$app = Join-Path $root 'offline'
if (-not (Test-Path (Join-Path $app 'manage.py'))) { throw "Cannot find offline\manage.py. Run this from the project folder." }

function Ask([string]$text) { $v = Read-Host $text; return $v.Trim() }
function AskSecret([string]$text) {
    $s = Read-Host $text -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return ([Runtime.InteropServices.Marshal]::PtrToStringAuto($b)).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

Write-Host ""
Write-Host "Step 1 of 3 - answers from your Supabase dashboard" -ForegroundColor Cyan
Write-Host "Project URL: Project Settings > API. It looks like https://abcdefghijklmnop.supabase.co"
$url = (Ask "Project URL").TrimEnd('/')
if ($url -notmatch '^https://([a-z0-9]+)\.supabase\.co$') { throw "That does not look like https://<letters>.supabase.co" }
$ref = $Matches[1]

Write-Host ""
Write-Host "Pooler host: click the green 'Connect' button at the top of Supabase > 'Transaction pooler'."
Write-Host "The host looks like aws-0-ap-south-1.pooler.supabase.com (copy only that part, no postgresql:// and no :6543)."
$poolerHost = Ask "Pooler host"
if ($poolerHost -notmatch '^[a-z0-9.-]+\.pooler\.supabase\.com$') { throw "That does not look like xxxx.pooler.supabase.com" }

Write-Host ""
Write-Host "The password you put in setup.sql for the covison_app user (letters and digits only)."
$dbPassword = AskSecret "Database password (typing is hidden)"
if ($dbPassword -notmatch '^[A-Za-z0-9]{12,}$') { throw "Use only letters and digits, 12 or more characters." }

Write-Host ""
Write-Host "Keys: Project Settings > API Keys. Use the 'Legacy' tab: anon key and service_role key."
$anon = AskSecret "anon key (typing is hidden)"
$service = AskSecret "service_role key (typing is hidden)"
if ($anon.Length -lt 30 -or $service.Length -lt 30) { throw "A key looks too short. Copy the whole key." }

Write-Host ""
Write-Host "Step 2 of 3 - the owner login you will use to sign in to the ERP" -ForegroundColor Cyan
$ownerEmail = Ask "Owner email"
if ($ownerEmail -notmatch '^[^@\s]+@[^@\s]+\.[^@\s]+$') { throw "That is not a valid email address." }
$ownerPassword = AskSecret "Owner password, 12+ characters (typing is hidden)"
if ($ownerPassword.Length -lt 12) { throw "Use at least 12 characters." }

# Find a Python that has ready-made database drivers (3.12 or 3.11 are safest).
$python = $null
foreach ($v in '3.12', '3.11', '3.13') {
    try { & py "-$v" --version *> $null; if ($LASTEXITCODE -eq 0) { $python = @('py', "-$v"); break } } catch { }
}
if (-not $python) { throw "Python 3.11 or 3.12 was not found. Install Python 3.12 from python.org and run this again." }

Write-Host ""
Write-Host "Step 3 of 3 - creating tables and the owner (takes a few minutes the first time)" -ForegroundColor Cyan
Push-Location $app
try {
    if (-not (Test-Path '.venv')) { & $python[0] $python[1] -m venv .venv; if ($LASTEXITCODE) { throw "Could not create the Python environment." } }
    $py = Join-Path $app '.venv\Scripts\python.exe'
    & $py -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE) { throw "Installing the requirements failed." }

    # These variables exist only inside this PowerShell window.
    $env:DJANGO_SECRET_KEY = 'one-time-setup-value'
    $env:DATABASE_URL = "postgresql://covison_app.$ref`:$dbPassword@$poolerHost`:5432/postgres"
    $env:SUPABASE_URL = $url
    $env:SUPABASE_PUBLISHABLE_KEY = $anon
    $env:SUPABASE_SECRET_KEY = $service
    $env:COVISON_CRM_OWNER_EMAIL = $ownerEmail
    $env:COVISON_CRM_OWNER_PASSWORD = $ownerPassword

    Write-Host "Creating tables..." -ForegroundColor Yellow
    & $py manage.py migrate --noinput; if ($LASTEXITCODE) { throw "migrate failed. Check the database password and pooler host." }
    Write-Host "Creating the sign-in rate-limit table..." -ForegroundColor Yellow
    & $py manage.py createcachetable; if ($LASTEXITCODE) { throw "createcachetable failed." }
    Write-Host "Creating the owner account..." -ForegroundColor Yellow
    & $py manage.py bootstrap_supabase_owner; if ($LASTEXITCODE) { throw "Creating the owner failed. Check the Project URL and the service_role key." }
}
finally {
    Pop-Location
    foreach ($n in 'DJANGO_SECRET_KEY','DATABASE_URL','SUPABASE_URL','SUPABASE_PUBLISHABLE_KEY','SUPABASE_SECRET_KEY','COVISON_CRM_OWNER_EMAIL','COVISON_CRM_OWNER_PASSWORD') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}

# Values for Vercel go in a private file that git ignores (supabase\private\).
$private = Join-Path $root 'supabase\private'
New-Item -ItemType Directory -Force $private | Out-Null
$bytes = New-Object byte[] 48; [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$secret = [Convert]::ToBase64String($bytes) -replace '[^A-Za-z0-9]', 'x'
$file = Join-Path $private 'vercel-env.txt'
@"
Paste these into Vercel > your project > Settings > Environment Variables (one row each: Name, then Value).
KEEP THIS FILE PRIVATE. Delete it when you are done.

DJANGO_SECRET_KEY=$secret
COVISON_HTTPS=1
DATABASE_URL=postgresql://covison_app.$ref`:$dbPassword@$poolerHost`:6543/postgres
SUPABASE_URL=$url
SUPABASE_PUBLISHABLE_KEY=$anon
SUPABASE_SECRET_KEY=$service
"@ | Set-Content -Encoding UTF8 $file

Write-Host ""
Write-Host "DONE. Your database and owner login are ready." -ForegroundColor Green
Write-Host "Next: open this file, then follow 'Part 3 - Vercel' in DEPLOYMENT.md:"
Write-Host "  $file"
Start-Process notepad.exe $file
