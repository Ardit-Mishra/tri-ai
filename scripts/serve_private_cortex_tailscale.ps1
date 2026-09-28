[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 3026,
    [switch]$Enable,
    [switch]$Disable
)

$ErrorActionPreference = "Stop"
if ($Enable -and $Disable) {
    throw "Choose either -Enable or -Disable."
}

$tailscale = Get-Command tailscale -ErrorAction Stop
if ($Disable) {
    & $tailscale.Source serve --https=443 off
    exit $LASTEXITCODE
}

if (-not $Enable) {
    Write-Output "Dry run: tailscale serve --bg http://127.0.0.1:$Port"
    Write-Output "Run with -Enable only after the private Cortex dashboard is healthy on loopback."
    exit 0
}

try {
    $snapshot = Invoke-RestMethod "http://127.0.0.1:$Port/api/snapshot" -TimeoutSec 10
} catch {
    throw "Private Cortex is not healthy on http://127.0.0.1:$Port. Start run_private_cortex.ps1 first."
}
if ($snapshot.demo -eq $true) {
    throw "Refusing to publish a demonstration instance as the private Cortex service."
}

# Serve is private to the tailnet. Do not substitute `tailscale funnel` here:
# Funnel would create a public-internet route and is not appropriate for a
# personal source index.
& $tailscale.Source serve --bg "http://127.0.0.1:$Port"
& $tailscale.Source serve status
exit $LASTEXITCODE
