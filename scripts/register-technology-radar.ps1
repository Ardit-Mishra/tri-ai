[CmdletBinding()]
param(
    [string]$TaskName = "TriAI-TechnologyRadar",
    [string]$StartTime = "06:00",
    [ValidateSet("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")]
    [string]$Day = "Sunday",
    [string]$Python = "python",
    [int]$EvaluateLimit = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$Runner = (Resolve-Path (Join-Path $PSScriptRoot "run-technology-radar.ps1")).Path
$PowerShell = (Get-Command powershell.exe -ErrorAction Stop).Source

$Trigger = New-ScheduledTaskTrigger -Weekly -WeeksInterval 1 -DaysOfWeek $Day -At $StartTime
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 4)
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME `
    -LogonType Interactive -RunLevel Limited
$Action = New-ScheduledTaskAction -Execute $PowerShell -Argument (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Python "{1}" -EvaluateLimit {2}' `
    -f $Runner, $Python, $EvaluateLimit
)

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger `
    -Settings $Settings -Principal $Principal `
    -Description "Tri-AI weekly capability discovery and quarantined evaluation report." | Out-Null

Write-Host "Registered $TaskName for $Day at $StartTime." -ForegroundColor Green
