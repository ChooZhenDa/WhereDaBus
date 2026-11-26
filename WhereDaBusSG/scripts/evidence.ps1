param(
    [ValidateSet('Start','Stop','Status','Collect')]
    [string]$Action = 'Status',
    [string]$ProjectPath = (Split-Path -Parent $PSScriptRoot)
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Utf8 = New-Object System.Text.UTF8Encoding($false)
function Invoke-Compose([string[]]$Arguments) {
    & docker @script:Compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'Docker command failed. Check Docker Desktop and the message above.' }
}
try {
    $root = (Resolve-Path -LiteralPath $ProjectPath).Path
    $envPath = Join-Path $root '.env'
    if (-not (Test-Path -LiteralPath $envPath)) { throw 'Run Setup.cmd first.' }
    $public = @{}
    $hasKey = $false
    foreach ($line in [IO.File]::ReadAllLines($envPath)) {
        if ($line -match '^\s*(WDB_PROJECT|APP_PORT|SAMPLE_STOPS|SAMPLE_SERVICES)\s*=\s*(.*?)\s*$') {
            $public[$Matches[1]] = $Matches[2].Trim().Trim('"').Trim("'")
        }
        if ($line -match '^\s*LTA_ACCOUNT_KEY\s*=\s*(.+?)\s*$') { $hasKey = $true }
    }
    if (-not $public.ContainsKey('WDB_PROJECT') -or $public.WDB_PROJECT -cnotmatch '^wheredabus-[a-f0-9]{16}$') {
        throw 'Installation identity is missing or invalid. Run Setup.cmd in this folder.'
    }
    $script:Compose = @('compose','--project-name',$public.WDB_PROJECT,'--file',(Join-Path $root 'compose.yaml'),'--env-file',$envPath,'--profile','analytics')
    Invoke-Compose @('config','--quiet')
    switch ($Action) {
        'Start' {
            if (-not $hasKey) { throw 'Add your LTA_ACCOUNT_KEY to .env and save it first.' }
            $stops = @()
            if ($public.ContainsKey('SAMPLE_STOPS')) { $stops = @($public.SAMPLE_STOPS.Split(',') | ForEach-Object { $_.Trim() } | Select-Object -Unique) }
            if ($stops.Count -lt 1 -or $stops.Count -gt 20 -or @($stops | Where-Object { $_ -notmatch '^[0-9]{5}$' }).Count -gt 0) {
                throw 'Save SAMPLE_STOPS=22599,44181,59059,65069,66359 (or your own 1-20 five-digit stop codes) in .env first.'
            }
            if ($public.ContainsKey('SAMPLE_SERVICES') -and $public.SAMPLE_SERVICES -and @($public.SAMPLE_SERVICES.Split(',') | Where-Object { $_.Trim() -notmatch '^[A-Za-z0-9]{1,12}$' }).Count -gt 0) {
                throw 'SAMPLE_SERVICES must be empty or comma-separated service numbers.'
            }
            Invoke-Compose @('up','--build','-d','api','sampler')
            Write-Host 'Sampler started. It polls every 60 seconds. Keep the computer awake and Docker Desktop running.'
            Write-Host 'Open the website, select Live data, then Half-hour trends and Today so far.'
            Write-Host 'Use Sampling-Status.cmd to check fresh timestamps and empty failedStops.'
            Invoke-Compose @('logs','--tail','5','sampler')
        }
        'Stop' {
            Invoke-Compose @('stop','sampler')
            Write-Host 'Sampling stopped. Existing observations remain stored.'
        }
        'Status' {
            Invoke-Compose @('ps','-a','sampler')
            Invoke-Compose @('logs','--tail','5','sampler')
            Write-Host 'Running alone is insufficient: check recent sampled timestamps, projected > 0, and failedStops/deferredStops.'
        }
        'Collect' {
            $stamp = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [Guid]::NewGuid().ToString('N').Substring(0,8)
            $folder = Join-Path $root ('evidence/' + $stamp)
            New-Item -ItemType Directory -Path $folder -Force | Out-Null
            $failed = $false
            foreach ($mode in @('audit','crud','benchmark')) {
                Write-Host "Recording $mode evidence..."
                $lines = @(& docker @script:Compose exec -T api python -m backend.evidence $mode)
                $code = $LASTEXITCODE
                $body = $lines -join [Environment]::NewLine
                try { $document = $body | ConvertFrom-Json } catch { throw "No valid evidence was returned. Apply the upgrade and check Docker Desktop. No $mode result is claimed." }
                if ($document.evidenceVersion -ne 1 -or $document.mode -ne $mode) { throw 'Unexpected evidence response.' }
                [IO.File]::WriteAllText((Join-Path $folder ($mode + '.json')), $body, $Utf8)
                if ($code -ne 0 -or $document.status -ne 'completed') { $failed = $true; Write-Warning "$mode contains failed or skipped stages. Read its JSON before presenting." }
            }
            Write-Host "Evidence saved: $folder"
            Write-Host 'Read EVIDENCE_GUIDE.txt for interpretation and screenshot steps. These files contain actual results from this installation.'
            if ($failed) { throw 'Evidence collection finished with incomplete stages; no successful result is implied for those stages.' }
        }
    }
    exit 0
} catch {
    Write-Host ('Action stopped: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

