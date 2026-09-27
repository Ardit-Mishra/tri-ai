[CmdletBinding()]
param(
    [string]$LogDir = (Join-Path $HOME ".tri-ai\logs")
)

$ErrorActionPreference = "Stop"
$StatePath = Join-Path $LogDir "daemons.json"
if (-not (Test-Path -LiteralPath $StatePath)) {
    throw "No Tri-AI daemon state file exists at $StatePath"
}

$State = Get-Content -LiteralPath $StatePath -Raw | ConvertFrom-Json
if ($State.status -ne "running" -or -not $State.stop_path) {
    Write-Output "Tri-AI daemons are not running (state: $($State.status))."
    exit 0
}

New-Item -ItemType File -Path $State.stop_path -Force | Out-Null
Write-Output "Requested clean Tri-AI daemon shutdown. Supervisor PID: $($State.supervisor_pid)"
