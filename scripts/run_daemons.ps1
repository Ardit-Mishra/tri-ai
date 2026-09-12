[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Board,
    [string]$Ledger,
    [string]$RunsDir,
    [string]$LogDir = (Join-Path $HOME ".tri-ai\logs"),
    [string]$IntakePolicy,
    [string]$DashboardUrl,
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

& $Python @Arguments
exit $LASTEXITCODE
