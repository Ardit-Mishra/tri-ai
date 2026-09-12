[CmdletBinding()]
param(
    [string]$Python = "python",
    [string]$Board,
    [string]$Ledger,
    [string]$RunsDir,
    [string]$LogDir = (Join-Path $HOME ".tri-ai\logs"),
    [string]$IntakePolicy,
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RuntimeRoot = Join-Path $HOME ".tri-ai"
if (-not $Board) { $Board = Join-Path $RuntimeRoot "board.db" }
if (-not $Ledger) { $Ledger = Join-Path $RuntimeRoot "ledger.jsonl" }
if (-not $RunsDir) { $RunsDir = Join-Path $RuntimeRoot "runs" }

$Arguments = @(
    (Join-Path $Root "src\daemon_supervisor.py"),
    "--board", $Board,
    "--ledger", $Ledger,
    "--runs-dir", $RunsDir,
    "--log-dir", $LogDir
)
if ($IntakePolicy) { $Arguments += @("--intake-policy", $IntakePolicy) }

if ($WhatIf) {
    Write-Output ("Would run: {0} {1}" -f $Python, ($Arguments -join " "))
    exit 0
}

& $Python @Arguments
exit $LASTEXITCODE
