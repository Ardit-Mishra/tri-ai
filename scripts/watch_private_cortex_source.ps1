[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$OutputName,
    [Parameter(Mandatory)]
    [string[]]$Root,
    [ValidateRange(1, 86400)]
    [int]$EverySeconds = 300,
    [string]$Python = "python",
    [string]$SourceDirectory = (Join-Path $HOME ".tri-ai\private-sources")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $repoRoot "src"
$output = Join-Path ([System.IO.Path]::GetFullPath($SourceDirectory)) $OutputName

# `Root` is intentionally explicit, for example:
# -Root "Desktop=C:\Users\you\Desktop","Documents=C:\Users\you\Documents"
# The agent reads metadata only and atomically replaces the private index.
$arguments = @("-m", "dashboard.private_index_agent", "--output", $output)
foreach ($entry in $Root) {
    $arguments += @("--root", $entry)
}
$arguments += @("--watch-seconds", $EverySeconds)
& $Python @arguments
exit $LASTEXITCODE
