[CmdletBinding()]
param(
    [string]$SourceDirectory = (Join-Path $HOME ".tri-ai\private-sources"),
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$directory = [System.IO.Path]::GetFullPath($SourceDirectory)
$registry = Join-Path $directory "sources.json"

if ((Test-Path -LiteralPath $registry) -and -not $Force) {
    Write-Output "Private Cortex source registry already exists: $registry"
    exit 0
}

New-Item -ItemType Directory -Force -Path $directory | Out-Null

# This is a visual authorization registry, not a connector configuration. It
# contains no file paths, account identifiers, or credentials. Each region
# remains pending until an approved device-side source agent writes its index.
$payload = [ordered]@{
    version = 1
    sources = @(
        "desktop", "laptop", "drive", "phone", "obsidian", "github",
        "vercel-render", "claude-codex", "ollama", "omniroute", "freellmapi"
    )
}
$payload | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $registry -Encoding utf8NoBOM
Write-Output "Initialized private Cortex source registry: $registry"
