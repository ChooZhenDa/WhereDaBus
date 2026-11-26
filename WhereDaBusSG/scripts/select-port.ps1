param([string]$ProjectPath = (Split-Path -Parent $PSScriptRoot))
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Test-AvailableTcpPort([int]$Port) {
    $listener = New-Object Net.Sockets.TcpListener([Net.IPAddress]::Loopback, $Port)
    try {
        $listener.Server.ExclusiveAddressUse = $true
        $listener.Start()
        return ([Net.IPEndPoint]$listener.LocalEndpoint).Port
    } catch [Net.Sockets.SocketException] {
        return 0
    } finally {
        $listener.Stop()
    }
}

function Select-PortValue([scriptblock]$Probe, [int]$First = 18081, [int]$Last = 18181) {
    foreach ($candidate in $First..$Last) {
        $available = & $Probe $candidate
        if ($available -eq $candidate) { return $candidate }
    }
    $available = & $Probe 0
    if ($available -ge 1024 -and $available -le 65535) { return [int]$available }
    throw 'No available local port was found. Close an unused local server or restart Docker Desktop and retry.'
}

# Dot-sourcing exposes the selection functions for offline development tests.
if ($MyInvocation.InvocationName -eq '.') { return }
try {
    $root = (Resolve-Path -LiteralPath $ProjectPath).Path
    $port = Select-PortValue -Probe { param($candidate) Test-AvailableTcpPort $candidate }
    $utf8 = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Join-Path $root '.launcher-port'), ([string]$port + "`n"), $utf8)
} catch {
    Write-Host 'Could not select an available local port. Close an unused local server or restart Docker Desktop, then run Setup.cmd again.'
    exit 1
}
