[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidateRange(1, 65535)]
    [int]$Port = 3026,
    [string]$SourceDirectory = (Join-Path $HOME ".tri-ai\private-sources")
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$initializer = Join-Path $PSScriptRoot "initialize_private_cortex.ps1"

& $initializer -SourceDirectory $SourceDirectory
$env:PYTHONPATH = Join-Path $repoRoot "src"
$env:TRI_AI_PRIVATE_SOURCE_DIR = [System.IO.Path]::GetFullPath($SourceDirectory)

# Loopback is intentional. Use a separate, authenticated Tailnet/Access
# configuration for phone access rather than making private source metadata a
# public Render route.
& $Python -m dashboard.kaya_web --host 127.0.0.1 --port $Port
exit $LASTEXITCODE
