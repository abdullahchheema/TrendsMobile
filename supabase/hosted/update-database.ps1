# Covison ERP - apply new database changes after an update. Run on your own computer whenever a release says
# "run the database update" (or after pulling new code that contains new migrations). Safe to run again.
#
#   powershell -ExecutionPolicy Bypass -File supabase\hosted\update-database.ps1
#
# It asks only for the database password (never saved). The project URL and pooler host are remembered.

$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$app = Join-Path $root 'offline'
if (-not (Test-Path (Join-Path $app 'manage.py'))) { throw "Cannot find offline\manage.py. Run this from the project folder." }

function AskSecret([string]$text) {
    $s = Read-Host $text -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return ([Runtime.InteropServices.Marshal]::PtrToStringAuto($b)).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

# The project URL and pooler host are remembered: read from the private Vercel notes (supabase/private/vercel-env.txt) or from
# supabase\private\connection.txt (saved the first time you type them). Only the database password is asked every time.
$private = Join-Path $root 'supabase\private'
$url = $null; $poolerHost = $null
foreach ($file in (Join-Path $private 'vercel-env.txt'), (Join-Path $private 'connection.txt')) {
    if (Test-Path $file) {
        foreach ($line in Get-Content $file) {
            if (-not $url -and $line -match '^\s*(?:SUPABASE_URL=)?(https://[a-z0-9]+\.supabase\.co)\s*$') { $url = $Matches[1] }
            if (-not $poolerHost -and $line -match '([a-z0-9-]+(?:\.[a-z0-9-]+)*\.pooler\.supabase\.com)') { $poolerHost = $Matches[1] }
        }
    }
}
if (-not $url) { $url = (Read-Host "Supabase Project URL (https://xxxx.supabase.co)").Trim().TrimEnd('/') }
if ($url -notmatch '^https://([a-z0-9]+)\.supabase\.co$') { throw "That does not look like https://<letters>.supabase.co" }
$ref = $Matches[1]
if (-not $poolerHost) { $poolerHost = (Read-Host "Pooler host (aws-0-....pooler.supabase.com)").Trim() }
if ($poolerHost -notmatch '^[a-z0-9.-]+\.pooler\.supabase\.com$') { throw "That does not look like xxxx.pooler.supabase.com" }
if (-not (Test-Path $private)) { New-Item -ItemType Directory -Path $private | Out-Null }
if (-not (Test-Path (Join-Path $private 'connection.txt'))) { Set-Content -Path (Join-Path $private 'connection.txt') -Value @($url, $poolerHost) }
Write-Host "Project: $url   Pooler: $poolerHost" -ForegroundColor DarkGray
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
    $env:DJANGO_SECRET_KEY = 'one-time-update-value'
    $env:DATABASE_URL = "postgresql://covison_app.$ref`:$dbPassword@$poolerHost`:5432/postgres"
    $env:SUPABASE_URL = $url
    Write-Host "Applying database updates..." -ForegroundColor Yellow
    & $py manage.py migrate --noinput; if ($LASTEXITCODE) { throw "migrate failed. Check the database password and pooler host." }
    & $py manage.py createcachetable; if ($LASTEXITCODE) { throw "createcachetable failed." }
    Write-Host "DONE. The database is up to date." -ForegroundColor Green
}
finally {
    Pop-Location
    foreach ($n in 'DJANGO_SECRET_KEY','DATABASE_URL','SUPABASE_URL') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}
