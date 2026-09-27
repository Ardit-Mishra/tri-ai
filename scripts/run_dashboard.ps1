# run_dashboard.ps1
#
# Serve the read-only KAYA dashboard, which every completion card links to.
#
# The dashboard port is explicit so completion links and the bound server stay
# aligned. The URL handed to a private-network client is derived from the same
# parameters as the bind rather than copied from an operator's machine.
#
# A non-loopback binding can expose task titles, workspace paths, and log tails
# to another device. It is therefore opt-in, even for a Tailscale address.

[CmdletBinding()]
param(
    [string]$Python = "python",
    [int]$Port = 8081,
    [string]$TailscaleAddress,
    [switch]$AllowTailnetBinding,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path

if ($AllowTailnetBinding -and -not $TailscaleAddress) {
    $TailscaleAddress = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -like "100.*" } |
        Select-Object -First 1).IPAddress
}

$HostAddress = "127.0.0.1"
if ($AllowTailnetBinding) {
    if (-not $TailscaleAddress) {
        throw "No Tailscale address found. Provide -TailscaleAddress explicitly or use loopback only."
    }
    $HostAddress = $TailscaleAddress
}

$Arguments = @(
    "-m", "dashboard.kaya_web",
    "--port", $Port,
    "--host", $HostAddress
)
if ($AllowTailnetBinding) {
    $Arguments += "--allow-non-loopback"
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
