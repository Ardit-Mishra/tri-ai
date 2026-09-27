# TriAI-Nightly - schedule the ASSIGNMENT of nightly work, then run the worker.
#
# WHY ASSIGN COMES FIRST (TRIG-03):
# A worker CLAIMS work that already exists; it never creates any. Scheduling
# only the worker would mean the scheduled path never calls `assign()` at all -
# not a trigger. The registered action invokes run-triai-scheduled.ps1, which
# runs python src\assign.py --from-queue <nightly queue> and starts
# python src\worker.py --max-tasks N only when assignment exits 0. The
# conditional belongs in that runner, rather than in an assumption about how
# Task Scheduler handles a failed action.
#
# The worker's CLI is pinned to the plan's shape `python src\worker.py
# --max-tasks N`. worker.py is built alongside this phase by a teammate; this
# script refuses to register a task whose second action cannot exist yet.
#
# USAGE (from this repo):
#   powershell -ExecutionPolicy Bypass -File scripts\register-triai-task.ps1
# Options (all have defaults):
#   -TaskName TriAI-Nightly
#   -MaxTasks 5
#   -NightlyQueue <abs path to a queue file>   (default: src\chores.example.jsonl)
#   -StartTime "03:30"
#   -Python   (default: the Hermes venv interpreter)
#
# UNREGISTER:
#   Unregister-ScheduledTask -TaskName TriAI-Nightly -Confirm:$false
# Inspect what was registered:
#   Get-ScheduledTask -TaskName TriAI-Nightly | Select -Expand Actions

param(
    [string]$TaskName     = "TriAI-Nightly",
    [string]$MaxTasks     = "5",
    [string]$NightlyQueue = "",
    [string]$StartTime    = "03:30",
    [string]$Python = (Join-Path $env:USERPROFILE "AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe")
)

$ErrorActionPreference = 'Stop'

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
if (-not $NightlyQueue) {
    $NightlyQueue = Join-Path $RepoRoot 'src\chores.example.jsonl'
}
$Assign = Join-Path $RepoRoot 'src\assign.py'
$Worker = Join-Path $RepoRoot 'src\worker.py'
$Runner = Join-Path $PSScriptRoot 'run-triai-scheduled.ps1'

foreach ($needed in @(
    @{ Kind = 'interpreter';    Path = $Python },
    @{ Kind = 'assign script';  Path = $Assign },
    @{ Kind = 'scheduled runner'; Path = $Runner },
    @{ Kind = 'nightly queue';  Path = $NightlyQueue }
)) {
    if (-not (Test-Path $needed.Path)) {
        throw "missing $($needed.Kind): $($needed.Path)"
    }
}
if (-not (Test-Path $Worker)) {
    throw "missing worker script: $Worker (worker.py is not built yet; register once the teammate lands it)"
}

# One scheduled action calls a runner that gates worker startup on assignment's
# exit code. Task Scheduler can sequence separate actions, but the condition
# belongs in our runner rather than in an assumption about its failure policy.
$Trigger     = New-ScheduledTaskTrigger -Daily -At $StartTime
$Settings    = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                  -MultipleInstances IgnoreNew `
                  -ExecutionTimeLimit (New-TimeSpan -Hours 12)
$Principal   = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
                  -LogonType Interactive -RunLevel Limited

$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source
$RunnerAction = New-ScheduledTaskAction -Execute $PowerShell `
    -Argument ('-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Python "{1}" -NightlyQueue "{2}" -MaxTasks "{3}"' -f $Runner, $Python, $NightlyQueue, $MaxTasks)

Write-Host ("Registering '{0}' with assignment gated before the worker:" -f $TaskName) -ForegroundColor Cyan
Write-Host ("  & '{0}' -File '{1}' -Python '{2}' -NightlyQueue '{3}' -MaxTasks '{4}'" -f $PowerShell, $Runner, $Python, $NightlyQueue, $MaxTasks)
Write-Host ("  daily at {0}; runs when the machine is available; one instance at a time" -f $StartTime)

$Description = "Tri-AI nightly (TRIG-03): assign the queue FIRST - a worker claims work, it never creates it - then run the worker for up to {0} tasks. Verify commands decide pass/fail, never the agent's report." -f $MaxTasks

Register-ScheduledTask -TaskName $TaskName `
    -Action $RunnerAction `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description $Description | Out-Null

Write-Host "Registered." -ForegroundColor Green
Write-Host ("Verify:  Get-ScheduledTask -TaskName '{0}' | Select -Expand Actions" -f $TaskName)
Write-Host ("Unregister:  Unregister-ScheduledTask -TaskName '{0}' -Confirm:`$false" -f $TaskName)
