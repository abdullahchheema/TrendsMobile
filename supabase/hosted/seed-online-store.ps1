# Covison ERP - fill the demo stock with selling prices, website specifications and illustrated product photos.
#
#   powershell -ExecutionPolicy Bypass -File supabase\hosted\seed-online-store.ps1
#
# Safe to run again: it never overwrites prices, specifications or photos that already exist. The photos are generated illustrations;
# replace them with real photos in Online Store > Products & Photos whenever you like.
# It asks only for the database password (never saved). The project URL, pooler host and the storage key are read from supabase\private.

$ErrorActionPreference = 'Stop'
$root = Resolve-Path (Join-Path $PSScriptRoot '..\..')
$app = Join-Path $root 'offline'
if (-not (Test-Path (Join-Path $app 'manage.py'))) { throw "Cannot find offline\manage.py. Run this from the project folder." }

function AskSecret([string]$text) {
    $s = Read-Host $text -AsSecureString
    $b = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)
    try { return ([Runtime.InteropServices.Marshal]::PtrToStringAuto($b)).Trim() } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($b) }
}

$private = Join-Path $root 'supabase\private'
$url = $null; $poolerHost = $null; $secretKey = $null; $publishable = $null
foreach ($file in (Join-Path $private 'vercel-env.txt'), (Join-Path $private 'connection.txt')) {
    if (Test-Path $file) {
        foreach ($line in Get-Content $file) {
            if (-not $url -and $line -match '^\s*(?:SUPABASE_URL=)?(https://[a-z0-9]+\.supabase\.co)\s*$') { $url = $Matches[1] }
            if (-not $poolerHost -and $line -match '([a-z0-9-]+(?:\.[a-z0-9-]+)*\.pooler\.supabase\.com)') { $poolerHost = $Matches[1] }
            if (-not $secretKey -and $line -match '^\s*SUPABASE_SECRET(?:_KEY)?=(\S+)\s*$') { $secretKey = $Matches[1] }
            if (-not $publishable -and $line -match '^\s*SUPABASE_PUBLISHABLE_KEY=(\S+)\s*$') { $publishable = $Matches[1] }
        }
    }
}
if (-not $url) { $url = (Read-Host "Supabase Project URL (https://xxxx.supabase.co)").Trim().TrimEnd('/') }
if ($url -notmatch '^https://([a-z0-9]+)\.supabase\.co$') { throw "That does not look like https://<letters>.supabase.co" }
$ref = $Matches[1]
if (-not $poolerHost) { $poolerHost = (Read-Host "Pooler host (aws-0-....pooler.supabase.com)").Trim() }
if ($poolerHost -notmatch '^[a-z0-9.-]+\.pooler\.supabase\.com$') { throw "That does not look like xxxx.pooler.supabase.com" }
if (-not $secretKey) { $secretKey = AskSecret "Supabase secret key for photo storage (hidden)" }
Write-Host "Project: $url   Pooler: $poolerHost" -ForegroundColor DarkGray

$dbPassword = AskSecret "Database password for covison_app (hidden)"
if ($dbPassword -notmatch '^[A-Za-z0-9]{12,}$') { throw "Use only letters and digits, 12 or more characters." }

$py = Join-Path $app '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { throw "Run supabase\hosted\update-database.ps1 once first; it sets up the Python environment and the database." }
Push-Location $app
try {
    $env:DJANGO_SECRET_KEY = 'one-time-seed-value'
    $env:DATABASE_URL = "postgresql://covison_app.$ref`:$dbPassword@$poolerHost`:5432/postgres"
    $env:SUPABASE_URL = $url
    $env:SUPABASE_SECRET_KEY = $secretKey
    if ($publishable) { $env:SUPABASE_PUBLISHABLE_KEY = $publishable }
    Write-Host "Seeding the online store (about a minute)..." -ForegroundColor Yellow
    & $py manage.py seed_online_store
    if ($LASTEXITCODE) { throw "The seed failed. Check the database password, and that update-database.ps1 was run after the latest release." }
    Write-Host "DONE. Open Online Store > Products & Photos to see the pages." -ForegroundColor Green
}
finally {
    Pop-Location
    foreach ($n in 'DJANGO_SECRET_KEY','DATABASE_URL','SUPABASE_URL','SUPABASE_SECRET_KEY','SUPABASE_PUBLISHABLE_KEY') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
}
