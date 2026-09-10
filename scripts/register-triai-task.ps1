# TriAI-Nightly - schedule the ASSIGNMENT of nightly work, then run the worker.
#
# WHY ASSIGN COMES FIRST (TRIG-03):
# A worker CLAIMS work that already exists; it never creates any. Scheduling
# only the worker would mean the scheduled path never calls `assign()` at all -
# not a trigger. So the registered task's first action is
#   python src\assign.py --from-queue <nightly queue>
# and its second is
#   python src\worker.py --max-tasks N
# Task Scheduler executes a task's actions sequentially and, on a non-zero exit
# from an action, marks the task failed and does not run the next one. A failed
# assign therefore never starts a worker - which is the safe outcome.
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

foreach ($needed in @(
    @{ Kind = 'interpreter';    Path = $Python },
    @{ Kind = 'assign script';  Path = $Assign },
    @{ Kind = 'nightly queue';  Path = $NightlyQueue }
)) {
    if (-not (Test-Path $needed.Path)) {
        throw "missing $($needed.Kind): $($needed.Path)"
    }
}
if (-not (Test-Path $Worker)) {
    throw "missing worker script: $Worker (worker.py is not built yet; register once the teammate lands it)"
}

# One task, two ordered actions: assign, then worker.
$Trigger     = New-ScheduledTaskTrigger -Daily -At $StartTime
$Settings    = New-ScheduledTaskSettingsSet -StartWhenAvailable `
                  -MultipleInstances IgnoreNew `
                  -ExecutionTimeLimit (New-TimeSpan -Hours 12)
$Principal   = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
                  -LogonType Interactive -RunLevel Limited

# -Execute takes the interpreter; -Argument holds everything after it. Paths
# carry spaces, so each is double-quoted.
$AssignAction = New-ScheduledTaskAction -Execute $Python `
                   -Argument ('"{0}" --from-queue "{1}"' -f $Assign, $NightlyQueue)
$WorkerAction = New-ScheduledTaskAction -Execute $Python `
                   -Argument ('"{0}" --max-tasks {1}' -f $Worker, $MaxTasks)

Write-Host ("Registering '{0}' with actions in this exact order (assign BEFORE worker):" -f $TaskName) -ForegroundColor Cyan
Write-Host ("  1) & '{0}' '{1}' --from-queue '{2}'" -f $Python, $Assign, $NightlyQueue)
Write-Host ("  2) & '{0}' '{1}' --max-tasks {2}" -f $Python, $Worker, $MaxTasks)
Write-Host ("  daily at {0}; runs when the machine is available; one instance at a time" -f $StartTime)

$Description = "Tri-AI nightly (TRIG-03): assign the queue FIRST - a worker claims work, it never creates it - then run the worker for up to {0} tasks. Verify commands decide pass/fail, never the agent's report." -f $MaxTasks

Register-ScheduledTask -TaskName $TaskName `
    -Action $AssignAction, $WorkerAction `
    -Trigger $Trigger `
    -Settings $Settings `
    -Principal $Principal `
    -Description $Description | Out-Null

Write-Host "Registered." -ForegroundColor Green
Write-Host ("Verify:  Get-ScheduledTask -TaskName '{0}' | Select -Expand Actions" -f $TaskName)
Write-Host ("Unregister:  Unregister-ScheduledTask -TaskName '{0}' -Confirm:`$false" -f $TaskName)