param(
    [string]$ProjectPath,
    [string]$GroupId
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
if (-not $ProjectPath) {
    $ProjectPath = Split-Path -Parent $PSScriptRoot
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Utf8 = New-Object System.Text.UTF8Encoding($false)

function Write-Utf8([string]$Path, [string]$Content) {
    [IO.File]::WriteAllText($Path, $Content, $Utf8)
}

function File-Sha256([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $hasher = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant() }
    finally { $stream.Dispose(); $hasher.Dispose() }
}

function Invoke-Compose([string[]]$Arguments) {
    & docker @script:Compose @Arguments
    if ($LASTEXITCODE -ne 0) { throw 'A Docker export command failed. Check the message above and that the application is running.' }
}

function Export-Text([string[]]$Arguments, [string]$Destination) {
    $writer = New-Object IO.StreamWriter($Destination, $false, $Utf8)
    try {
        & docker @script:Compose @Arguments | ForEach-Object { $writer.WriteLine([string]$_) }
        if ($LASTEXITCODE -ne 0) { throw 'Database export failed; no submission ZIP will be created.' }
    } finally { $writer.Dispose() }
}

function Safe-Source([string]$Relative) {
    if ([string]::IsNullOrWhiteSpace($Relative) -or [IO.Path]::IsPathRooted($Relative) -or $Relative.Contains(':')) {
        throw 'The source manifest contains an unsafe path.'
    }
    $parts = $Relative -split '[\\/]'
    if ($parts[0] -eq 'data' -or $Relative -in @('USER_MANUAL.txt', 'FILE_MANIFEST.json')) {
        throw 'The input source manifest contains a reserved generated submission path.'
    }
    foreach ($part in $parts) {
        if ($part -in @('', '.', '..', '.git', 'node_modules', '__pycache__', '.venv', '.repair-backups') -or
            ($part -like '.env*' -and $part -cne '.env.example') -or $part -like '.launcher-*') {
            throw 'The source manifest includes an unsafe or private path.'
        }
    }
    if ($Relative -match '\.(zip|pyc|log|exe|dump|bak|pem|key|pfx|p12)$') { throw 'Unexpected generated or private file in source manifest.' }
    $resolved = [IO.Path]::GetFullPath((Join-Path $script:ProjectRoot $Relative))
    if (-not $resolved.StartsWith($script:ProjectRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source path escaped the project.'
    }
    $current = $script:ProjectRoot
    foreach ($part in $parts) {
        $current = Join-Path $current $part
        $item = Get-Item -LiteralPath $current -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw 'Linked files/directories are not allowed in a submission source path.'
        }
    }
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) { throw 'A source file listed in the manifest is missing.' }
    return $resolved
}

$remoteDump = $null
$locationPushed = $false
try {
    $script:ProjectRoot = (Resolve-Path -LiteralPath $ProjectPath).Path.TrimEnd('\', '/')
    foreach ($required in @('FILE_MANIFEST.json', 'compose.yaml', '.env', 'START_HERE.txt', 'Setup.cmd')) {
        if (-not (Test-Path -LiteralPath (Join-Path $ProjectRoot $required) -PathType Leaf)) {
            throw 'Run Setup.cmd in the standalone project first, then run the bundled submission-tools/Create-Submission.cmd.'
        }
    }
    if (-not $GroupId) { $GroupId = Read-Host 'Enter your group number or ID (example: 009)' }
    if ($GroupId -notmatch '^[A-Za-z0-9_-]{1,30}$') { throw 'Use 1-30 letters, digits, hyphens or underscores for the group ID.' }
    $sourceManifest = Get-Content -LiteralPath (Join-Path $ProjectRoot 'FILE_MANIFEST.json') -Raw | ConvertFrom-Json
    # An exported package also lists its old generated snapshots. Re-export
    # current databases; never treat those old snapshots as source inputs.
    $sourcePaths = @($sourceManifest.files | ForEach-Object { ([string]$_.path).Replace('\', '/') } | Where-Object {
        $_ -notmatch '^data/' -and $_ -notin @('USER_MANUAL.txt', 'FILE_MANIFEST.json')
    })
    if ($sourcePaths.Count -lt 20) { throw 'The final release source manifest is incomplete.' }
    $seen = @{}
    foreach ($relative in $sourcePaths) {
        $normal = $relative.Replace('\', '/')
        if ($seen.ContainsKey($normal)) { throw 'Duplicate source paths in manifest.' }
        $seen[$normal] = Safe-Source $relative
    }
    foreach ($required in @('backend/api.py', 'backend/bootstrap.py', 'backend/web/index.html', 'sql/001_schema.sql',
                             'sql/002_queries.sql', 'scripts/manage.cmd', 'scripts/configure.py', 'scripts/install_config.py', 'scripts/select-port.ps1', '.env.example',
                             'Dockerfile', 'compose.yaml', 'requirements.txt', 'Setup.cmd', 'START_HERE.txt')) {
        if (-not $seen.ContainsKey($required)) { throw "Source manifest is missing required file: $required" }
    }
    $template = Get-Content -LiteralPath $seen['.env.example'] -Raw
    $expectedTemplate = @{
        LTA_ACCOUNT_KEY = ''; POSTGRES_PASSWORD = 'GENERATE_A_RANDOM_HEX_PASSWORD'
        MONGO_PASSWORD = 'GENERATE_A_DIFFERENT_RANDOM_HEX_PASSWORD'
        APP_PORT = '18081'; SAMPLE_STOPS = ''; SAMPLE_SERVICES = ''; WDB_PROJECT = ''
    }
    $templateValues = @{}
    foreach ($line in ($template -split '\r?\n')) {
        if (-not $line.Trim() -or $line.TrimStart().StartsWith('#')) { continue }
        if ($line -notmatch '^([A-Z_]+)=(.*)$') { throw 'Unexpected line in .env.example. Restore the original template.' }
        $name, $value = $Matches[1], $Matches[2].Trim()
        if ($templateValues.ContainsKey($name) -or -not $expectedTemplate.ContainsKey($name) -or $value -cne $expectedTemplate[$name]) {
            throw '.env.example must have only the original placeholders, with no duplicate or secret values.'
        }
        $templateValues[$name] = $value
    }
    if ($templateValues.Count -ne $expectedTemplate.Count) { throw 'The .env.example template is incomplete.' }
    Push-Location -LiteralPath $ProjectRoot
    $locationPushed = $true
    $projectNames = @()
    foreach ($line in [IO.File]::ReadAllLines((Join-Path $ProjectRoot '.env'))) {
        if ($line -match '^\s*WDB_PROJECT\s*=\s*(.*?)\s*$') { $projectNames += $Matches[1] }
    }
    if ($projectNames.Count -ne 1 -or $projectNames[0] -cnotmatch '^wheredabus-[a-f0-9]{16}$') {
        throw 'This folder has no valid standalone installation identity. Run Setup.cmd in this folder first.'
    }
    $installationName = $projectNames[0]
    $script:Compose = @('compose', '--project-name', $installationName, '--file', 'compose.yaml', '--env-file', '.env')
    Invoke-Compose @('version')

    $tag = (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + ([Guid]::NewGuid().ToString('N').Substring(0, 8))
    $buildRoot = Join-Path $ProjectRoot ('.submission-build/' + $tag)
    $appRoot = Join-Path $buildRoot 'WhereDaBusSG'
    $dataRoot = Join-Path $appRoot 'data'
    New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $dataRoot 'mongodb') -Force | Out-Null

    Write-Host 'Exporting saved bus-arrival records first, before the live cache expires...'
    $mongoCode = @'
import sys
from bson.json_util import dumps, CANONICAL_JSON_OPTIONS
from backend.storage import mongo_database
name = sys.argv[1]
if name not in {'arrival_live','arrival_latest','arrival_inbox','arrival_observations'}:
    raise SystemExit(2)
client = None
try:
    client, db = mongo_database()
    query = {'capturedAt': {'$exists': True}} if name == 'arrival_live' else {}
    for document in db[name].find(query):
        print(dumps(document, json_options=CANONICAL_JSON_OPTIONS), flush=True)
except Exception:
    print('Could not export bus-arrival records. Check the application database connection.', file=sys.stderr)
    raise SystemExit(1)
finally:
    if client is not None:
        client.close()
'@
    $mongoCounts = [ordered]@{}
    foreach ($collection in @('arrival_live', 'arrival_latest', 'arrival_inbox', 'arrival_observations')) {
        $destination = Join-Path $dataRoot ('mongodb/' + $collection + '.jsonl')
        Export-Text @('exec', '-T', 'api', 'python', '-c', $mongoCode, $collection) $destination
        # Measure-Object's Count is the number of exported JSON documents.
        $mongoCounts[$collection] = ([IO.File]::ReadLines($destination) | Measure-Object).Count
    }
    if ($mongoCounts['arrival_observations'] -eq 0) {
        throw 'The analytics history export is empty. Run Start-Sampling.cmd, confirm successful samples, then export again. A live-cache response alone is not analytics evidence.'
    }
    if (($mongoCounts.Values | Measure-Object -Sum).Sum -eq 0) {
        throw 'No saved bus-arrival data was found. Open Live arrivals in Live data mode, select real stops, then immediately rerun this helper while arrivals refresh.'
    }
    $countsCode = @'
import json, sys
from backend.storage import sql_connection
try:
    with sql_connection() as connection:
        counts = {}
        for name in ('bus_stops','bus_services','bus_routes','import_runs'):
            counts[name] = connection.execute('SELECT count(*) FROM transport.' + name).fetchone()[0]
        if any(counts[name] == 0 for name in ('bus_stops','bus_services','bus_routes')):
            raise ValueError('missing routes')
        print(json.dumps(counts))
except Exception:
    print('Static bus data is unavailable. Run Refresh-Data.cmd successfully before exporting.', file=sys.stderr)
    raise SystemExit(1)
'@
    Export-Text @('exec', '-T', 'api', 'python', '-c', $countsCode) (Join-Path $dataRoot 'sql_counts.json')
    $sqlCounts = Get-Content -LiteralPath (Join-Path $dataRoot 'sql_counts.json') -Raw | ConvertFrom-Json
    foreach ($name in @('bus_stops', 'bus_services', 'bus_routes')) {
        if ([long]$sqlCounts.$name -le 0) { throw 'The static dataset is empty. Refresh the data first.' }
    }
    Write-Host 'Exporting PostgreSQL bus data and schema...'
    $remoteDump = '/tmp/wheredabus-submission-' + [Guid]::NewGuid().ToString('N') + '.sql'
    Invoke-Compose @('exec', '-T', 'postgres', 'pg_dump', '-U', 'wheredabus', '-d', 'wheredabus',
        '--schema=transport', '--no-owner', '--no-privileges', '--exclude-table-data=transport.users',
        '--exclude-table-data=transport.favourite_routes', '--exclude-table-data=transport.saved_journeys', '--file', $remoteDump)
    Invoke-Compose @('cp', ('postgres:' + $remoteDump), (Join-Path $dataRoot 'postgresql.sql'))
    if ((Get-Item -LiteralPath (Join-Path $dataRoot 'postgresql.sql')).Length -eq 0) { throw 'The SQL export is empty.' }

    foreach ($relative in $sourcePaths) {
        $destination = Join-Path $appRoot $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $seen[$relative.Replace('\', '/')] -Destination $destination
    }
    $captured = [DateTime]::UtcNow.ToString('o')
    $metadata = [ordered]@{
        groupId = $GroupId; exportedAtUtc = $captured
        source = 'Application database records; normal production imports use LTA DataMall'
        sqlCountsBeforeDump = $sqlCounts; mongoExportedDocuments = $mongoCounts
        personalSqlTableDataExcluded = @('transport.users', 'transport.favourite_routes', 'transport.saved_journeys')
        note = 'Snapshots are historical, not current live arrivals. No API key or database password included.'
    }
    Write-Utf8 (Join-Path $dataRoot 'EXPORT_METADATA.json') ($metadata | ConvertTo-Json -Depth 10)
    $dataReadme = @'
DATA INCLUDED IN THIS SUBMISSION

postgresql.sql contains the transport schema, functions, indexes and stored bus
data from the running project. users/favourite_routes/saved_journeys definitions remain, but
their personal table contents are excluded. Credentials/roles are not dumped.
mongodb/*.jsonl contains the four application's bus-arrival collections as
canonical Extended JSON, one document per line. Empty files mean that optional
collection had no saved documents. Lock-only live-cache documents are omitted.
EXPORT_METADATA.json records export time and counts (SQL counts taken shortly
before the dump; active imports can change these). Avoid refreshing routes
while exporting if you need those counts to match the SQL dump exactly.

Normal real-data source: LTA DataMall Bus Stops, Bus Services, Bus Routes and
Bus Arrival APIs: https://datamall.lta.gov.sg/
Review the records and complete attribution in the report for the dataset you
actually used. The exporter copies database records; it cannot prove their
original source. Synthetic fixtures in ../fixtures/ are labelled examples.
Original arrival timestamps are preserved. These snapshots are historical
evidence; they must not be presented as current live service information.

RUNNING THE APPLICATION
Follow ../USER_MANUAL.txt (Docker Desktop, Setup.cmd and an LTA AccountKey).
No shared API key is supplied. The live application imports current static data
and requests new arrivals. It does not pretend this old snapshot is live.

INSPECT THE SQL SNAPSHOT IN A SEPARATE REVIEW DATABASE
After setting up this package, open a terminal in the application folder:
  docker compose exec -T postgres createdb -U wheredabus wheredabus_submission
  docker compose cp data/postgresql.sql postgres:/tmp/submission-review.sql
  docker compose exec -T postgres psql -U wheredabus -d wheredabus_submission -v ON_ERROR_STOP=1 --single-transaction -f /tmp/submission-review.sql
The first command must succeed. If that review database already exists, choose
another new database name in both commands. Do not import this full schema into
the application's existing wheredabus database. This separate database is for
inspection; normal application configuration continues to use wheredabus.

INSPECT THE MONGO SNAPSHOT WITHOUT EXPIRING IT
JSONL files can be inspected directly. To query a file with MongoDB, import it
into a new review database/collection using mongoimport (Extended JSON format),
not the app's arrival_live collection. Do not add TTL indexes in the review
database: expiresAt/observedAt are the original historical timestamps. The live
application's TTL indexes would remove old documents after a restore there.

Official tool documentation:
https://www.postgresql.org/docs/16/app-pgdump.html
https://www.mongodb.com/docs/database-tools/mongoimport/
'@
    Write-Utf8 (Join-Path $dataRoot 'DATA_README.txt') $dataReadme
    $setupGuide = Get-Content -LiteralPath (Join-Path $appRoot 'START_HERE.txt') -Raw
    $setupGuide = $setupGuide.Replace('WhereDaBusSG-Standalone.zip', 'the source-code ZIP')
    $setupGuide = $setupGuide.Replace('WhereDaBusSG-Standalone folder', 'WhereDaBusSG folder')
    $manual = "WhereDaBusSG - user manual`r`nGroup: $GroupId`r`n`r`n" + $setupGuide +
        "`r`nSUBMITTED DATA`r`nRead data/DATA_README.txt for provenance and snapshot inspection.`r`n"
    Write-Utf8 (Join-Path $appRoot 'USER_MANUAL.txt') $manual

    $entries = @()
    foreach ($file in Get-ChildItem -LiteralPath $appRoot -File -Recurse -Force) {
        $relative = $file.FullName.Substring($appRoot.Length + 1).Replace('\', '/')
        if ($relative -eq 'FILE_MANIFEST.json') { continue }
        $entries += [ordered]@{path = $relative; sha256 = (File-Sha256 $file.FullName)}
    }
    Write-Utf8 (Join-Path $appRoot 'FILE_MANIFEST.json') (([ordered]@{
        format = 'source-data-manual-submission'; groupId = $GroupId; exportedAtUtc = $captured; files = $entries
    }) | ConvertTo-Json -Depth 10)
    $outputDir = Join-Path $ProjectRoot 'submission'
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
    $zipPath = Join-Path $outputDir ("Group_${GroupId}_WhereDaBusSG_Source_${tag}.zip")
    $pendingZip = $zipPath + '.partial'
    [IO.Compression.ZipFile]::CreateFromDirectory($buildRoot, $pendingZip, [IO.Compression.CompressionLevel]::Optimal, $false)
    $archive = [IO.Compression.ZipFile]::OpenRead($pendingZip)
    try {
        if ($archive.Entries.Count -ne ($entries.Count + 1)) { throw 'ZIP file count check failed.' }
        $expectedHashes = @{}
        foreach ($entry in $entries) { $expectedHashes['WhereDaBusSG/' + $entry.path] = $entry.sha256 }
        foreach ($entry in $archive.Entries) {
            $name = $entry.FullName.Replace('\', '/')
            if ($name -match '(^|/)\.env($|[^/])' -and $name -notmatch '(^|/)\.env\.example$') { throw 'A private configuration file entered the ZIP.' }
            if ($name -eq 'WhereDaBusSG/FILE_MANIFEST.json') { continue }
            if (-not $expectedHashes.ContainsKey($name)) { throw 'An unexpected file entered the ZIP.' }
            $stream = $entry.Open()
            $hasher = [Security.Cryptography.SHA256]::Create()
            try {
                $actual = [BitConverter]::ToString($hasher.ComputeHash($stream)).Replace('-', '').ToLowerInvariant()
                if ($actual -ne $expectedHashes[$name]) { throw 'A ZIP payload hash does not match its source file.' }
            } finally { $stream.Dispose(); $hasher.Dispose() }
        }
    } finally { $archive.Dispose() }
    [IO.File]::Move($pendingZip, $zipPath)
    Write-Host ''
    Write-Host 'Created source + data + user manual ZIP:'
    Write-Host $zipPath
    Write-Host 'Review its contents, then place it with the final PDF, PowerPoint and MP4 in your overall submission ZIP.'
    Write-Host 'The application source came from FILE_MANIFEST.json. If you added new code files, add them to that manifest and rerun.'
    Write-Host 'No application code or database records were changed.'
} catch {
    Write-Host ('Submission preparation stopped: ' + $_.Exception.Message)
    exit 1
} finally {
    if ($remoteDump) {
        # Remove only the exact temporary export created by this run.
        try { & docker @script:Compose exec -T postgres rm -- $remoteDump 2>$null | Out-Null }
        catch { Write-Warning 'Could not remove the temporary SQL export inside the container.' }
    }
    if ($locationPushed) { Pop-Location }
}
