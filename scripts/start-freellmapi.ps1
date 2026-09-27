# Start the FreeLLMAPI router the way install-freellmapi.ps1 does.
# Kept as a file rather than an inline -Command because backtick line
# continuations do not survive being passed through bash.
$root = Join-Path $env:USERPROFILE '.tri-ai\freellmapi'
$logs = Join-Path $env:USERPROFILE '.tri-ai\logs'
New-Item -ItemType Directory -Force $logs | Out-Null

$existing = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
if ($existing) {
    Write-Output "port 3001 already listening (pid $($existing[0].OwningProcess)); not starting a second router"
    exit 0
}

Start-Process -FilePath 'node' `
    -ArgumentList 'server/dist/index.js' `
    -WorkingDirectory $root `
    -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logs 'freellmapi.stdout.log') `
    -RedirectStandardError (Join-Path $logs 'freellmapi.stderr.log')

Start-Sleep -Seconds 8
$now = Get-NetTCPConnection -LocalPort 3001 -State Listen -ErrorAction SilentlyContinue
if ($now) {
    foreach ($c in $now) { Write-Output "listening on $($c.LocalAddress):$($c.LocalPort) pid $($c.OwningProcess)" }
} else {
    Write-Output "did not come up - check $logs\freellmapi.stderr.log"
    exit 1
}
