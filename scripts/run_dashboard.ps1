# run_dashboard.ps1
#
# Serve the read-only KAYA dashboard, which every completion card links to.
#
# It was not running on the desktop at all, and the URL the cards carried -
# http://100.67.149.86:8080 - pointed at a port held by svchost.exe, the same
# way 20128 was held when OmniRoute moved here. So every card the user received
# offered a link that could not resolve. Hence 8081, checked free, and the URL
# the daemons hand out is set from the same parameters as the bind.
#
# Non-loopback binding is deliberate and is what --allow-non-loopback exists to
# make an explicit choice: the dashboard stays strictly read-only - SQLite in
# mode=ro with PRAGMA query_only, and no route that mutates anything - but task
# titles, workspace paths and live log tails become readable by anything on the
# bound network. Here that network is Tailscale, which is how the phone reaches
# it, and loopback, which is how this machine does.

[CmdletBinding()]
param(
    [string]$Python = "python",
    [int]$Port = 8081,
    [string]$TailscaleAddress,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if (-not $TailscaleAddress) {
    $TailscaleAddress = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "100.*" } |
        Select-Object -First 1).IPAddress
}

$Arguments = @(
    "-m", "dashboard.kaya_web",
    "--port", $Port,
    "--host", "127.0.0.1"
)
if ($TailscaleAddress) {
    $Arguments += @("--host", $TailscaleAddress, "--allow-non-loopback")
} else {
    Write-Warning "run_dashboard: no 100.x address found - serving loopback only"
}

if ($WhatIf) {
    Write-Output ("Would run: {0} {1}  (cwd {2})" -f $Python, ($Arguments -join " "), (Join-Path $Root "src"))
    exit 0
}

# `-m dashboard.kaya_web` from src/, so the package's own relative imports
# resolve the same way they do for the worker.
Set-Location (Join-Path $Root "src")
& $Python @Arguments
exit $LASTEXITCODE
