[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 3018
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path -Parent $PSScriptRoot
$env:PYTHONPATH = Join-Path $repoRoot 'src'

# This entry point is deliberately loopback-only and always pins --demo. It
# cannot accidentally become a launcher for the operator's real runtime.
& python -m dashboard.kaya_web --host 127.0.0.1 --port $Port --demo
exit $LASTEXITCODE
