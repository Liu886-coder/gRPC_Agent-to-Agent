$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& (Join-Path $projectRoot ".venv\Scripts\python.exe") scripts/start_system.py
exit $LASTEXITCODE

