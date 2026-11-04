@echo off
setlocal
pushd "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"%~dp0.venv\Scripts\python.exe" -m agent_comm.rpc_client %*
set "clientExitCode=%ERRORLEVEL%"
popd
exit /b %clientExitCode%
