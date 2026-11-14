# Covison ERP - check the live books. READ ONLY: nothing is changed (the checks run inside a read-only database transaction).
# Run it after an update, or whenever you want to be sure the books, the stock and the online orders all agree. Safe to run any time.
#
#   powershell -ExecutionPolicy Bypass -File supabase\hosted\check-database.ps1
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
if (-not (Test-Path $py)) { throw "Run supabase\hosted\update-database.ps1 once first (it prepares Python for these scripts)." }
Push-Location $app
try {
    $env:DJANGO_SECRET_KEY = 'one-time-check-value'
    $env:DATABASE_URL = "postgresql://covison_app.$ref`:$dbPassword@$poolerHost`:5432/postgres"
    $env:SUPABASE_URL = $url
    Write-Host "Checking the books (read only, nothing is changed)..." -ForegroundColor Yellow
    & $py manage.py audit_invariants
    if ($LASTEXITCODE) { Write-Host "SOME CHECKS FOUND PROBLEMS. Send the lines marked FAIL above." -ForegroundColor Red }
    else { Write-Host "ALL CHECKS PASSED." -ForegroundColor Green }
}
finally {
    Pop-Location
    foreach ($n in 'DJANGO_SECRET_KEY','DATABASE_URL','SUPABASE_URL') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}
