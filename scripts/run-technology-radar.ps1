[CmdletBinding()]
param(
    [string]$Python = "python",
    [int]$EvaluateLimit = 3,
    [string]$Report = (Join-Path $HOME ".tri-ai\radar\latest.json")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Catalog = Join-Path $RepoRoot "src\capability_catalog.py"
$Radar = Join-Path $RepoRoot "src\technology_radar.py"

& $Python $Catalog --repo-root $RepoRoot
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $Python $Radar --report $Report --evaluate-limit $EvaluateLimit
exit $LASTEXITCODE
