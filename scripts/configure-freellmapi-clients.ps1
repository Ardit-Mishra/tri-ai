[CmdletBinding()]
param(
    [string]$RouterUrl = "http://127.0.0.1:3001",
    [string]$InstallRoot = (Join-Path $HOME ".tri-ai\freellmapi"),
    [switch]$Apply
)

$ErrorActionPreference = "Stop"
$cli = Join-Path $InstallRoot "cli\dist\index.js"
if (-not (Test-Path -LiteralPath $cli)) {
    throw "Pinned FreeLLMAPI CLI not found at $cli. Run scripts/install-freellmapi.ps1 first, or pass -InstallRoot with the installed checkout."
}

$key = $env:FREELLMAPI_API_KEY
if (-not $key) {
    $key = [Environment]::GetEnvironmentVariable("FREELLMAPI_API_KEY", "User")
}
if (-not $key) {
    throw "Missing FREELLMAPI_API_KEY. Create a unified key in the local FreeLLMAPI dashboard, save it as a User environment variable, then open a new PowerShell window."
}

$mode = if ($Apply) { @() } else { @("--dry-run") }
foreach ($client in "claude", "codex", "hermes") {
    & node $cli "setup-$client" --url $RouterUrl --api-key $key @mode
    if ($LASTEXITCODE -ne 0) {
        throw "FreeLLMAPI setup failed for $client. No later client was configured."
    }
}

if ($Apply) {
    Write-Host "Configured Claude Code, Codex, and the Hermes runtime through FreeLLMAPI." -ForegroundColor Green
} else {
    Write-Host "Dry run completed. Re-run with -Apply to change local client configuration." -ForegroundColor Yellow
}
