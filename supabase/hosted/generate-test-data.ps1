# Trends Mobile ERP - add a realistic 90-day test business to YOUR live database, then audit the books.
# Run on your own computer. It only ADDS records; it never deletes, resets or edits anything that exists.
#
#   powershell -ExecutionPolicy Bypass -File supabase\hosted\generate-test-data.ps1
#
# What it does, in order:
#   1. Audits your books as they are now (read-only) so you can see they were healthy before.
#   2. After you type ADD TEST DATA, adds about 90 days of activity to your first two shops: purchases, sales (cash, bank,
#      part-paid, credit), customer and supplier payments, returns, exchanges, expenses, transfers, adjustments, loans and assets.
#      Every customer and supplier it creates is named "TEST - ...", every note says "TEST DATA".
#   3. Audits the books again and prints the result.
# Nothing you type is saved. Run it a second time only if you want even more data (it will ask you to add -Again).

param([switch]$Again, [int]$Days = 90)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$app = Join-Path $root 'offline'
if (-not (Test-Path (Join-Path $app 'manage.py'))) { throw "Cannot find offline\manage.py. Run this from the project folder." }

function AskSecret([string]$text) {
    $s = Read-Host $text -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return ([Runtime.InteropServices.Marshal]::PtrToStringAuto($b)).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

$url = (Read-Host "Supabase Project URL (https://xxxx.supabase.co)").Trim().TrimEnd('/')
if ($url -notmatch '^https://([a-z0-9]+)\.supabase\.co$') { throw "That does not look like https://<letters>.supabase.co" }
$ref = $Matches[1]
$poolerHost = (Read-Host "Pooler host (aws-0-....pooler.supabase.com)").Trim()
if ($poolerHost -notmatch '^[a-z0-9.-]+\.pooler\.supabase\.com$') { throw "That does not look like xxxx.pooler.supabase.com" }
$dbPassword = AskSecret "Database password for covison_app (hidden)"
if ($dbPassword -notmatch '^[A-Za-z0-9]{12,}$') { throw "Use only letters and digits, 12 or more characters." }

$py = Join-Path $app '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) {
    $python = $null
    foreach ($v in '3.12', '3.11', '3.13') { try { & py "-$v" --version *> $null; if ($LASTEXITCODE -eq 0) { $python = @('py', "-$v"); break } } catch { } }
    if (-not $python) { throw "Python 3.11 or 3.12 was not found. Install Python 3.12 from python.org and run this again." }
    Push-Location $app; try { & $python[0] $python[1] -m venv .venv } finally { Pop-Location }
}
Push-Location $app
try {
    & $py -m pip install --quiet --disable-pip-version-check -r requirements.txt
    if ($LASTEXITCODE) { throw "Installing the requirements failed." }
    $env:DJANGO_SECRET_KEY = 'one-time-test-data-value'
    $env:DATABASE_URL = "postgresql://covison_app.$ref`:$dbPassword@$poolerHost`:5432/postgres"
    $env:SUPABASE_URL = $url

    Write-Host "`n1. Checking your books as they are now (read-only)..." -ForegroundColor Yellow
    & $py manage.py verify_books; if ($LASTEXITCODE) { throw "The check could not run. Check the connection details, and that the database update was applied." }

    Write-Host "`nThis will ADD about $Days days of test activity to your first two shops." -ForegroundColor Yellow
    Write-Host "Nothing is deleted or changed. Test customers/suppliers are named 'TEST - ...'. It cannot be undone automatically." -ForegroundColor Yellow
    $answer = Read-Host "Type ADD TEST DATA to continue (anything else stops here)"
    if ($answer -cne 'ADD TEST DATA') { Write-Host "Stopped. Nothing was added." -ForegroundColor Green; return }

    Write-Host "`n2. Adding test business (this takes a few minutes)..." -ForegroundColor Yellow
    $extra = @('--days', "$Days"); if ($Again) { $extra += '--again' }
    & $py manage.py generate_test_business @extra
    if ($LASTEXITCODE) { throw "The generator stopped. Whatever it already added is kept, because each event is saved on its own." }

    Write-Host "`n3. Final audit of the books..." -ForegroundColor Yellow
    & $py manage.py verify_books
    Write-Host "`nDONE. Open the app: sales, stock, dues, cash and bank now hold three months of activity." -ForegroundColor Green
}
finally {
    Pop-Location
    foreach ($n in 'DJANGO_SECRET_KEY','DATABASE_URL','SUPABASE_URL') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}
