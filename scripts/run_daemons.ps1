[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Board,
    [string]$Ledger,
    [string]$RunsDir,
    [string]$LogDir = (Join-Path $HOME ".tri-ai\logs"),
    [string]$IntakePolicy,
    [string]$DashboardUrl,
    [int]$RouterPort = 20129,
    [int]$RouterWaitSeconds = 180,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeRoot = Join-Path $HOME ".tri-ai"
if (-not $Board) { $Board = Join-Path $RuntimeRoot "board.db" }
if (-not $Ledger) { $Ledger = Join-Path $RuntimeRoot "ledger.jsonl" }
if (-not $RunsDir) { $RunsDir = Join-Path $RuntimeRoot "runs" }
if (-not $IntakePolicy) {
    $DefaultIntakePolicy = Join-Path $RuntimeRoot "intake_policy.json"
    if (Test-Path -LiteralPath $DefaultIntakePolicy -PathType Leaf) {
        $IntakePolicy = $DefaultIntakePolicy
    }
}

$Arguments = @(
    (Join-Path $Root "src\daemon_supervisor.py"),
    "--board", $Board,
    "--ledger", $Ledger,
    "--runs-dir", $RunsDir,
    "--log-dir", $LogDir
)
if ($IntakePolicy) { $Arguments += @("--intake-policy", $IntakePolicy) }
if ($DashboardUrl) { $Arguments += @("--dashboard-url", $DashboardUrl) }

if ($WhatIf) {
    Write-Output ("Would run: {0} {1}" -f $Python, ($Arguments -join " "))
    exit 0
}

# Let the router get its socket open first.
#
# Both this and "OmniRoute Router" are AtStartup tasks, and nothing sequences
# them. Every model call Tri-AI makes resolves through the router, so a worker
# that wins the race claims a task, gets a connection refused, and burns an
# attempt on an environment fault - recoverable, but it is the first thing that
# would happen after every reboot.
#
# Bounded, and it never blocks: a router that does not come up is a degraded
# system, while a worker that never starts is a stopped one. Past the budget
# the daemons start anyway and the local fallback models carry what they can.
if ($RouterPort -gt 0 -and $RouterWaitSeconds -gt 0) {
    $deadline = (Get-Date).AddSeconds($RouterWaitSeconds)
    $listening = $false
    while (-not $listening -and (Get-Date) -lt $deadline) {
        $listening = [bool](Get-NetTCPConnection -LocalPort $RouterPort -State Listen -ErrorAction SilentlyContinue)
        if (-not $listening) { Start-Sleep -Seconds 3 }
    }
    if ($listening) {
        Write-Output "run_daemons: router listening on 127.0.0.1:$RouterPort"
    } else {
        Write-Warning "run_daemons: router not listening on $RouterPort after $RouterWaitSeconds s - starting anyway on the fallback chain"
    }
}

& $Python @Arguments
exit $LASTEXITCODE
