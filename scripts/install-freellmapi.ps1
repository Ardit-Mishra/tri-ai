[CmdletBinding()]
param(
    [string]$InstallRoot = (Join-Path $HOME ".tri-ai\freellmapi"),
    [string]$Revision = "b882473c3a23251be312a7270e2e0dc1eae1329d",
    [int]$Port = 3001,
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$Repository = "https://github.com/tashfeenahmed/freellmapi.git"

foreach ($command in "git", "node", "npm") {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "FreeLLMAPI requires '$command' on PATH."
    }
}

if (Test-Path $InstallRoot) {
    if (-not (Test-Path (Join-Path $InstallRoot ".git"))) {
        throw "Install root exists but is not a git checkout: $InstallRoot"
    }
    $head = (git -C $InstallRoot rev-parse HEAD).Trim()
    if ($head -ne $Revision) {
        throw "Install root is pinned to $head, not the reviewed revision $Revision. Refusing to replace it."
    }
} else {
    $parent = Split-Path -Parent $InstallRoot
    New-Item -ItemType Directory -Force $parent | Out-Null
    git clone $Repository $InstallRoot
    git -C $InstallRoot checkout --detach $Revision
}

Push-Location $InstallRoot
try {
    npm ci

    $envPath = Join-Path $InstallRoot ".env"
    if (-not (Test-Path $envPath)) {
        $bytes = New-Object byte[] 32
        [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
        $encryptionKey = -join ($bytes | ForEach-Object { "{0:x2}" -f $_ })
        # HOST is the one that binds. HOST_BIND is Docker-only - it selects the
        # host interface a *container's* port is published on and does nothing
        # to a native node process. Setting only HOST_BIND left the server on
        # its default of `::`, i.e. every interface, reachable across the
        # tailnet and guarded by nothing but the unified API key. Both are
        # written now: HOST for this install, HOST_BIND for a later dockerised
        # one.
        @(
            "ENCRYPTION_KEY=$encryptionKey"
            "PORT=$Port"
            "HOST=127.0.0.1"
            "HOST_BIND=127.0.0.1"
        ) | Set-Content -LiteralPath $envPath -Encoding utf8NoBOM
        Write-Host "Created a localhost-only FreeLLMAPI configuration at $envPath" -ForegroundColor Green
    }

    npm run build

    if ($Start) {
        $existing = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        if ($existing) {
            throw "Port $Port is already listening. Refusing to attach to an unknown router."
        }
        $logRoot = Join-Path $HOME ".tri-ai\logs"
        New-Item -ItemType Directory -Force $logRoot | Out-Null
        $stdout = Join-Path $logRoot "freellmapi.stdout.log"
        $stderr = Join-Path $logRoot "freellmapi.stderr.log"
        Start-Process -FilePath "node" -ArgumentList "server/dist/index.js" -WorkingDirectory $InstallRoot -WindowStyle Hidden -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        Write-Host "FreeLLMAPI started on http://127.0.0.1:$Port (logs: $logRoot)." -ForegroundColor Green
    } else {
        Write-Host "Installed and built. Start with: .\scripts\install-freellmapi.ps1 -Start" -ForegroundColor Cyan
    }
} finally {
    Pop-Location
}
