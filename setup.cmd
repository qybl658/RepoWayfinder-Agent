@echo off
setlocal
set "ROOT=%~dp0"
set "VENV_PY=%ROOT%.venv\Scripts\python.exe"
for %%A in (%*) do (
    if /I "%%~A"=="--dry-run" goto run_only
    if /I "%%~A"=="--uninstall" goto run_only
)
if exist "%VENV_PY%" goto check_deps
where py >nul 2>nul
if not errorlevel 1 (
    py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>nul
    if not errorlevel 1 (
        py -3 -m venv "%ROOT%.venv" || exit /b 1
        goto check_deps
    )
)
where python >nul 2>nul
if errorlevel 1 goto no_python
python -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto no_python
python -m venv "%ROOT%.venv" || exit /b 1
:check_deps
"%VENV_PY%" -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto no_python
"%VENV_PY%" -c "import requests, dotenv" >nul 2>nul
if errorlevel 1 "%VENV_PY%" -m pip install -r "%ROOT%requirements.txt" || exit /b 1
:run_only
if exist "%VENV_PY%" goto use_venv
where py >nul 2>nul
if not errorlevel 1 goto use_launcher
where python >nul 2>nul
if errorlevel 1 goto no_python
python "%ROOT%configure_clients.py" %*
exit /b %errorlevel%
:use_venv
"%VENV_PY%" -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto no_python
"%VENV_PY%" "%ROOT%configure_clients.py" %*
exit /b %errorlevel%
:use_launcher
py -3 -c "import sys; raise SystemExit(sys.version_info < (3, 11))" >nul 2>nul
if errorlevel 1 goto no_python
py -3 "%ROOT%configure_clients.py" %*
exit /b %errorlevel%
:no_python
echo Python 3.11 or newer is required. Install it separately, then rerun setup.cmd. 1>&2
exit /b 1
