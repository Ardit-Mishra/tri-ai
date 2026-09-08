# Run the board tests under the Hermes install's interpreter, which is the one
# that can import the kanban kernel and its dependencies.
$ErrorActionPreference = 'Stop'
$hermes = if ($env:TRIAI_HERMES_HOME) { $env:TRIAI_HERMES_HOME }
          else { Join-Path $HOME 'AppData\Local\hermes\hermes-agent' }
$python = Join-Path $hermes 'venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }
& $python (Join-Path $PSScriptRoot 'run.py') @args
exit $LASTEXITCODE
