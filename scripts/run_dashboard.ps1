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

# The session key, read here rather than inherited.
#
# A scheduled task does not receive the environment of whoever started it,
# so a key exported before Start-ScheduledTask never reaches this process.
# Without one `kaya_web` cannot refuse a Tailnet caller - which is how this
# dashboard served the whole board unauthenticated on 2026-09-29: task
# titles, workspace paths and log tails, to any device on the Tailnet.
#
# No key plus a Tailnet bind is refused outright. Serving nothing is
# recoverable; serving the board unauthenticated is not.
$KeyFile = Join-Path $env:USERPROFILE ".tri-ai\kaya-session-key.txt"
if (Test-Path $KeyFile) {
    $env:KAYA_SESSION_TOKEN = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
} elseif ($AllowTailnetBinding) {
    throw ("Refusing to bind $HostAddress with no session key. Create " +
           "$KeyFile first, or drop -AllowTailnetBinding and serve loopback only.")
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
