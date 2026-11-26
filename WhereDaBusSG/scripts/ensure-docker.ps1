param([ValidateRange(0, 600)][int]$TimeoutSeconds = 120)

$installUrl = 'https://docs.docker.com/desktop/setup/install/windows-install/'
$docker = Get-Command docker -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $docker) {
    Write-Host "Docker CLI was not found. Install Docker Desktop for Windows from $installUrl, or add its resources\bin folder to PATH. Then reopen Setup.cmd."
    exit 1
}

function Test-LinuxEngine {
    $reply = & $docker.Source info --format '{{.OSType}}' 2>&1
    if ($LASTEXITCODE -eq 0) {
        if ($reply -contains 'linux') { return $true }
        if ($reply -contains 'windows') {
            Write-Host 'Open the Docker Desktop tray menu, select "Switch to Linux containers", then rerun Setup.cmd.'
            exit 1
        }
    }
    if ($reply -match 'permission denied|access is denied') {
        Write-Host 'Access to the Docker engine was denied. Open Docker Desktop under this Windows account and check that docker info works before rerunning Setup.cmd.'
        exit 1
    }
    return $false
}

if (Test-LinuxEngine) { exit 0 }

Write-Host 'Starting Docker Desktop and waiting for the Linux engine...'
& $docker.Source desktop start --detach *> $null
if ($LASTEXITCODE -ne 0) {
    $candidates = @(
        "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe"
        "$env:ProgramFiles\Docker\Docker\Docker Desktop.exe"
    )
    $desktop = $candidates | Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $desktop) {
        Write-Host "Open Docker Desktop from the Start menu. If it is missing, install it from $installUrl. Once its engine is running, rerun Setup.cmd."
        exit 1
    }
    try {
        Start-Process -FilePath $desktop -WindowStyle Hidden -ErrorAction Stop
    } catch {
        Write-Host 'Docker Desktop could not be started. Open it from the Start menu, complete its setup, then rerun Setup.cmd.'
        exit 1
    }
}

$timer = [Diagnostics.Stopwatch]::StartNew()
do {
    if (Test-LinuxEngine) { exit 0 }
    if ($timer.Elapsed.TotalSeconds -ge $TimeoutSeconds) { break }
    Start-Sleep -Seconds 2
} while ($timer.Elapsed.TotalSeconds -lt $TimeoutSeconds)

Write-Host "The Docker Linux engine did not become ready within $TimeoutSeconds seconds. Open Docker Desktop, complete any first-run prompts and wait for Engine running. Then rerun Setup.cmd."
exit 1
