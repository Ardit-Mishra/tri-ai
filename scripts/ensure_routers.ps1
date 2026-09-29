<#
.SYNOPSIS
    Start OmniRoute and FreeLLMAPI if they are not already running.

.DESCRIPTION
    Idempotent by design. This runs at boot, at logon, and again every
    fifteen minutes, so it must be safe to run when everything is already
    up - it starts only what is missing and reports what it found.

    Detection differs per service on purpose:

      * OmniRoute is matched by *process*, not port. It binds several ports
        and will quietly fall back to the next free one if its own is taken -
        which is how the desktop ended up serving on 20129 for five days
        while every client looked at 20128. A port check would not have seen
        that, and worse, would have started a second copy.

      * FreeLLMAPI is matched by *port*, because that is the contract its
        clients depend on and it binds exactly one.

    Both are loopback-only. Neither is exposed to the tailnet by this script.

.NOTES
    Written for Windows PowerShell 5.1, which is what both machines run.
    Native executables writing to stderr surface as terminating errors under
    ErrorActionPreference = Stop, so this file deliberately does not set it
    and checks state instead of trusting exit codes.
#>
[CmdletBinding()]
param(
    [int]$OmniRoutePort = 20128,
    [int]$FreeLlmApiPort = 3001,
    [switch]$WhatIfOnly
)

$ErrorActionPreference = "Continue"

$logRoot = Join-Path $env:USERPROFILE ".tri-ai\logs"
New-Item -ItemType Directory -Force $logRoot | Out-Null
$transcript = Join-Path $logRoot "ensure-routers.log"

function Write-Line([string]$message) {
    $stamp = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    $line = "$stamp  $message"
    Write-Output $line
    Add-Content -LiteralPath $transcript -Value $line -Encoding utf8
}

function Get-OmniRouteProcess {
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match "omniroute" } |
        Select-Object -First 1
}

function Test-PortListening([int]$port) {
    [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)
}

# --- OmniRoute --------------------------------------------------------------

$omni = Get-OmniRouteProcess
if ($omni) {
    Write-Line "omniroute: already running (pid $($omni.ProcessId))"
} else {
    $shim = Join-Path $env:APPDATA "npm\omniroute.cmd"
    if (-not (Test-Path $shim)) {
        Write-Line "omniroute: NOT INSTALLED (no $shim)"
    } elseif ($WhatIfOnly) {
        Write-Line "omniroute: would start"
    } else {
        # An npm shim is a .cmd; CreateProcess cannot execute one directly,
        # so it goes through the interpreter with an explicit argument list.
        $interpreter = if ($env:COMSPEC) { $env:COMSPEC } else { "cmd.exe" }
        Start-Process -FilePath $interpreter `
            -ArgumentList "/c", "`"$shim`"", "serve" `
            -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logRoot "omniroute.out.log") `
            -RedirectStandardError  (Join-Path $logRoot "omniroute.err.log")
        Write-Line "omniroute: started"
    }
}

# --- FreeLLMAPI -------------------------------------------------------------

if (Test-PortListening $FreeLlmApiPort) {
    Write-Line "freellmapi: already listening on $FreeLlmApiPort"
} else {
    $root = Join-Path $env:USERPROFILE ".tri-ai\freellmapi"
    $entry = Join-Path $root "server\dist\index.js"
    if (-not (Test-Path $entry)) {
        Write-Line "freellmapi: NOT INSTALLED (no $entry)"
    } elseif ($WhatIfOnly) {
        Write-Line "freellmapi: would start"
    } else {
        Start-Process -FilePath "node" -ArgumentList "server/dist/index.js" `
            -WorkingDirectory $root -WindowStyle Hidden `
            -RedirectStandardOutput (Join-Path $logRoot "freellmapi.stdout.log") `
            -RedirectStandardError  (Join-Path $logRoot "freellmapi.stderr.log")
        Write-Line "freellmapi: started"
    }
}

# --- report what is actually serving ----------------------------------------

if (-not $WhatIfOnly) { Start-Sleep -Seconds 25 }

function Write-ServiceState([string]$name, [int[]]$ports) {
    if (-not $ports) { Write-Line "${name}: no ports to check"; return }
    foreach ($port in $ports) {
        $listeners = Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue
        if (-not $listeners) { Write-Line ("{0}: nothing listening on {1}" -f $name, $port); continue }
        $addresses = ($listeners | ForEach-Object { $_.LocalAddress } | Sort-Object -Unique) -join ","
        $exposed = $listeners | Where-Object { $_.LocalAddress -notin @("127.0.0.1", "::1") }
        $code = (curl.exe -s -m 8 -o NUL -w "%{http_code}" "http://127.0.0.1:$port/v1/models")
        $warning = if ($exposed) { "  *** WARNING: bound beyond loopback ***" } else { "" }
        Write-Line ("{0}: {1}:{2} -> HTTP {3}{4}" -f $name, $addresses, $port, $code, $warning)
    }
}

# OmniRoute's port differs per machine - the laptop serves on 20128, the
# desktop on 20129 - and it binds three, only one of which is the API. Ask
# the process what it actually bound rather than asserting a number: a
# hardcoded port reported the desktop as down while it was serving perfectly
# well, which is the same mistake the stale portproxy made.
# OmniRoute runs as two processes: a launcher that binds nothing, and the
# server that binds everything. Taking the first match found the launcher
# and reported the service down while it was serving on three ports, so
# every OmniRoute process is asked and the ports are unioned.
$omniPorts = @(
    Get-CimInstance Win32_Process -Filter "Name = 'node.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match "omniroute" } |
        ForEach-Object {
            Get-NetTCPConnection -State Listen -OwningProcess $_.ProcessId -ErrorAction SilentlyContinue
        } | ForEach-Object { $_.LocalPort } | Sort-Object -Unique
)
if (-not $omniPorts) { $omniPorts = @($OmniRoutePort) }

Write-ServiceState "omniroute" $omniPorts
Write-ServiceState "freellmapi" @($FreeLlmApiPort)
