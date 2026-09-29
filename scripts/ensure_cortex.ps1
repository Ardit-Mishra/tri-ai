<#
.SYNOPSIS
    Start the private Cortex dashboard if it is not already serving.

.DESCRIPTION
    Idempotent, and meant to run at logon and on a short repeat, like
    ensure_routers.ps1. The Cortex is the page reached over the Tailnet
    from a phone, and it kept dying: started from an interactive session,
    it is torn down with that session. A scheduled task owns it instead.

    The session key is read from its file at start time and passed through
    the environment, never as an argument - a command line is readable by
    any local process on this machine, an environment block is not.

    Setting KAYA_SESSION_TOKEN also turns loopback trust off, so every
    caller signs in at /login including this machine. That is deliberate:
    `tailscale serve` proxies the Tailnet from 127.0.0.1, so any rule that
    trusted loopback would hand the dashboard to the whole Tailnet.
#>
[CmdletBinding()]
param(
    [int]$Port = 3026,
    [string]$SourceRoot,
    [string]$KeyFile,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Continue"

if (-not $SourceRoot) {
    $SourceRoot = Join-Path $env:USERPROFILE "worktrees\tri-ai-public-release\src"
}
if (-not $KeyFile) {
    $KeyFile = Join-Path $env:USERPROFILE ".tri-ai\kaya-session-key.txt"
}

$logRoot = Join-Path $env:USERPROFILE ".tri-ai\logs"
New-Item -ItemType Directory -Force $logRoot | Out-Null
$transcript = Join-Path $logRoot "ensure-cortex.log"

function Write-Line([string]$message) {
    $line = "{0}  {1}" -f (Get-Date).ToString("yyyy-MM-dd HH:mm:ss"), $message
    Write-Output $line
    Add-Content -LiteralPath $transcript -Value $line -Encoding utf8
}

if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
    Write-Line "cortex: already listening on $Port"
} elseif (-not (Test-Path (Join-Path $SourceRoot "dashboard\kaya_web.py"))) {
    Write-Line "cortex: NOT INSTALLED (no kaya_web.py under $SourceRoot)"
} elseif ($WhatIfOnly) {
    Write-Line "cortex: would start"
} else {
    if (Test-Path $KeyFile) {
        $env:KAYA_SESSION_TOKEN = (Get-Content -LiteralPath $KeyFile -Raw).Trim()
    } else {
        # Without a key the dashboard refuses every non-loopback caller
        # outright. That is the fail-closed default and is better than
        # serving the Tailnet unauthenticated, so this starts anyway and
        # says what is missing.
        Write-Line "cortex: no session key at $KeyFile - remote callers will be sealed"
    }
    Start-Process -FilePath "python" `
        -ArgumentList "-m", "dashboard.kaya_web", "--host", "127.0.0.1", "--port", "$Port" `
        -WorkingDirectory $SourceRoot -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logRoot "cortex.log") `
        -RedirectStandardError  (Join-Path $logRoot "cortex.err.log")
    $env:KAYA_SESSION_TOKEN = $null
    Write-Line "cortex: started"
    Start-Sleep -Seconds 12
}

$listeners = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if (-not $listeners) {
    Write-Line "cortex: nothing listening on $Port"
} else {
    $addresses = ($listeners | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique) -join ","
    $exposed = $listeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1", "::1") }
    $code = (curl.exe -s -m 8 -o NUL -w "%{http_code}" "http://127.0.0.1:$Port/login")
    Write-Line ("cortex: {0}:{1} /login -> HTTP {2}{3}" -f $addresses, $Port, $code,
        $(if ($exposed) { "  *** WARNING: bound beyond loopback ***" } else { "" }))
}
