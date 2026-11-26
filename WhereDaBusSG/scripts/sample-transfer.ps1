param(
    [Parameter(Mandatory=$true)][ValidateSet('Export','Restore')][string]$Action,
    [string]$Archive,
    [string]$ProjectPath,
    [ValidateRange(1,65535)][int]$CheckMorningPort
)
$ErrorActionPreference = 'Stop'
if (-not $ProjectPath) {
    $ProjectPath = Split-Path -Parent $PSScriptRoot
}
$projectRoot = (Resolve-Path -LiteralPath $ProjectPath).Path
$envFile = Join-Path $projectRoot '.env'
if (-not (Test-Path -LiteralPath $envFile -PathType Leaf)) {
    throw 'For the submitted dataset, run Setup.cmd. To transfer a separate live backup, run this command in the configured live installation.'
}
$prefix = 'wdb-transfer-' + [Guid]::NewGuid().ToString('N')
$remoteTool = "/tmp/$prefix.py"
$remoteZip = "/tmp/$prefix.zip"
$compose = @('compose', '--file', (Join-Path $projectRoot 'compose.yaml'), '--env-file', $envFile)
function Invoke-TransferDocker([string[]]$CommandArgs) {
    & docker @compose @CommandArgs
    if ($LASTEXITCODE -ne 0) { throw "Docker sample transfer failed. Verify Docker Desktop and the project's API are running." }
}
if ($Action -eq 'Restore') {
    if (-not $Archive) { $Archive = (Read-Host 'Path to the sample backup ZIP').Trim().Trim('"') }
    $archivePath = (Resolve-Path -LiteralPath $Archive).Path
} else {
    if (-not $Archive) {
        $backupDir = Join-Path $projectRoot 'sample-backups'
        [IO.Directory]::CreateDirectory($backupDir) | Out-Null
        $Archive = Join-Path $backupDir ("WhereDaBus-samples-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + ".zip")
    }
    $archivePath = [IO.Path]::GetFullPath($Archive)
    if (Test-Path -LiteralPath $archivePath) { throw 'The selected backup already exists. Choose a new filename.' }
    $part = Join-Path ([IO.Path]::GetDirectoryName($archivePath)) ($prefix + '.partial')
}
try {
    Invoke-TransferDocker @('cp', (Join-Path (Split-Path -Parent $PSScriptRoot) 'backend/sample_transfer.py'), "api:$remoteTool")
    if ($Action -eq 'Export') {
        Invoke-TransferDocker @('exec','-T','-e','PYTHONPATH=/app','api','python',$remoteTool,'export',$remoteZip)
        Invoke-TransferDocker @('cp',"api:$remoteZip",$part)
        [IO.File]::Move($part, $archivePath)
        Write-Host "Verified sample backup: $archivePath"
    } else {
        Invoke-TransferDocker @('cp',$archivePath,"api:$remoteZip")
        Invoke-TransferDocker @('exec','-T','-e','PYTHONPATH=/app','api','python',$remoteTool,'restore',$remoteZip)
    }
} finally {
    # Compose copies as root; /tmp's sticky bit requires that owner for this helper.
    & docker @compose exec -T --user root api python -c "from pathlib import Path; import sys; [Path(p).unlink(missing_ok=True) for p in sys.argv[1:]]" $remoteTool $remoteZip 2>$null | Out-Null
    if ($part -and (Test-Path -LiteralPath $part)) { [IO.File]::Delete($part) }
}
if ($CheckMorningPort -and $Action -eq 'Restore') {
    $url = "http://127.0.0.1:$CheckMorningPort/analytics/peak?service=860&stop=59059&date_from=2026-11-18&date_to=2026-11-18"
    $morning = Invoke-RestMethod -Uri $url -TimeoutSec 30
    if (-not ($morning.rows | Where-Object { $_.validSamples -gt 0 })) {
        # A live installation expires old observations; the review copy preserves them.
        exit 3
    }
}
