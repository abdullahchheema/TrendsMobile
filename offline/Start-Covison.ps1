$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (-not (Test-Path '.env')) {
    function New-LocalSecret {
        $bytes = New-Object byte[] 48
        $random = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $random.GetBytes($bytes)
        $random.Dispose()
        return [Convert]::ToBase64String($bytes).Replace('+','-').Replace('/','_').TrimEnd('=')
    }
    $secret = New-LocalSecret
    $databaseSecret = New-LocalSecret
    @("DJANGO_SECRET_KEY=$secret", "POSTGRES_PASSWORD=$databaseSecret", 'DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,web,testserver', 'COVISON_BIND_ADDRESS=127.0.0.1') | Set-Content -Encoding ASCII '.env'
}
New-Item -ItemType Directory -Force imports,backups,artifacts | Out-Null
docker image inspect covison/offline:development *> $null
if ($LASTEXITCODE -ne 0) { throw 'Load the supplied images with docker load -i covison-images.tar, or build once while online using docker compose build web.' }
docker compose up -d --no-build --pull never
if ($LASTEXITCODE -ne 0) { throw 'The local server could not start. Check Docker Desktop.' }
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    try { Invoke-WebRequest -UseBasicParsing 'http://localhost:8081/health/' -TimeoutSec 2 | Out-Null; break }
    catch { if ($attempt -eq 29) { throw 'The local server did not become ready. Run docker compose logs web.' }; Start-Sleep -Seconds 2 }
}
docker compose exec -T web python manage.py bootstrap_owner
if ($LASTEXITCODE -ne 0) { throw 'Owner setup did not finish.' }
if (-not (Test-Path 'LOCAL-LOGIN.txt')) { docker compose cp web:/data/initial-owner.txt LOCAL-LOGIN.txt }
Write-Host 'First login details are in LOCAL-LOGIN.txt. Change the password after signing in.'
Start-Process 'http://localhost:8081'
