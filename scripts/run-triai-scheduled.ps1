# Tri-AI scheduled-task runner. Assignment must succeed before a worker starts.
# Task Scheduler orders separate actions but does not provide this exit-code
# gate between them, so the condition lives in the one action it runs.

param(
    [Parameter(Mandatory = $true)]
    [string]$Python,
    [Parameter(Mandatory = $true)]
    [string]$NightlyQueue,
    [string]$MaxTasks = "5"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$Assign = Join-Path $RepoRoot 'src\assign.py'
$Worker = Join-Path $RepoRoot 'src\worker.py'

foreach ($needed in @(
    @{ Kind = 'interpreter'; Path = $Python },
    @{ Kind = 'assign script'; Path = $Assign },
    @{ Kind = 'worker script'; Path = $Worker },
    @{ Kind = 'nightly queue'; Path = $NightlyQueue }
)) {
    if (-not (Test-Path -LiteralPath $needed.Path)) {
        throw "missing $($needed.Kind): $($needed.Path)"
    }
}

& $Python $Assign --from-queue $NightlyQueue
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $Python $Worker --max-tasks $MaxTasks
exit $LASTEXITCODE
