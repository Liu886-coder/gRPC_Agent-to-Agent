$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $venvPython)) {
    $runtimePython = $null
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $foundPython = & py -3 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0) { $runtimePython = $foundPython.Trim() }
    }
    if (-not $runtimePython) {
        $bundledPython = Join-Path $env:USERPROFILE ".cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
        if (Test-Path -LiteralPath $bundledPython) { $runtimePython = $bundledPython }
    }
    if (-not $runtimePython) { throw "请先安装 Python 3.11+，然后重新执行本脚本。" }
    & $runtimePython -m venv (Join-Path $projectRoot ".venv")
    if ($LASTEXITCODE -ne 0) { throw "创建虚拟环境失败" }
}
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
& $venvPython -m pip install -r requirements-lock.txt
if ($LASTEXITCODE -ne 0) { throw "依赖安装失败，请检查网络" }
& $venvPython scripts/generate_proto.py
if ($LASTEXITCODE -ne 0) { throw "Protobuf 生成失败" }
Write-Host "安装完成。启动：.\.venv\Scripts\python.exe scripts/start_system.py"

