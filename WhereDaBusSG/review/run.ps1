param([ValidateSet('Start','Stop')][string]$Action='Start')
$ErrorActionPreference = 'Stop'
$config = Join-Path $PSScriptRoot '.env.review'
if (-not (Test-Path -LiteralPath $config)) {
    if ($Action -eq 'Stop') { Write-Host 'No review installation found.'; exit 0 }
    $id = [Guid]::NewGuid().ToString('N').Substring(0,12)
    $project = "wdb-review-$id"
    @("COMPOSE_PROJECT_NAME=$project",
      "REVIEW_IMAGE=${project}-app:local",
      "REVIEW_SQL_PASSWORD=$([Guid]::NewGuid().ToString('N'))",
      "REVIEW_MONGO_PASSWORD=$([Guid]::NewGuid().ToString('N'))") |
      Set-Content -LiteralPath $config -Encoding Ascii
}
$project = (Get-Content -LiteralPath $config | Where-Object { $_ -like 'COMPOSE_PROJECT_NAME=*' }) -replace '^COMPOSE_PROJECT_NAME=',''
if ($project -notmatch '^wdb-review-[a-f0-9]{12}$') { throw 'Invalid review installation identifier.' }
$compose = @('compose','--env-file',$config,'-p',$project,'-f',(Join-Path $PSScriptRoot 'compose.yaml'))
function Run-Compose([string[]]$Arguments) {
    & docker @compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Docker Compose failed. Check the output above." }
}
if ($Action -eq 'Stop') {
    Run-Compose @('stop')
    Write-Host 'Review stopped. This copy retains its databases.'
    exit 0
}
Run-Compose @('build','api')
Run-Compose @('up','-d','--no-build','api')
Run-Compose @('logs','--no-log-prefix','loader')
$address = & docker @compose port api 8080
if ($LASTEXITCODE -ne 0 -or $address -notmatch '^127\.0\.0\.1:\d+$') { throw 'Review API did not publish a local port.' }
$base = "http://$address"
$ready = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try { Invoke-RestMethod -Uri "$base/health" -TimeoutSec 3 | Out-Null; $ready = $true; break }
    catch { Start-Sleep -Seconds 2 }
}
if (-not $ready) { throw 'Review API did not become ready.' }
Write-Host "Open $base/#trend"
Write-Host "Historical trend JSON: $base/analytics/trend?service=70M&stop=66359&date_from=2026-11-22&date_to=2026-11-22"
