@echo off
REM ============================================================================
REM  install_service.bat - install the Trading Assistant as a Windows Service
REM  using NSSM (the "Non-Sucking Service Manager"), with restart-on-failure.
REM
REM  Requirements:
REM    1. Run this file as Administrator (right-click -> Run as administrator).
REM    2. NSSM must be available. Download it from https://nssm.cc/download,
REM       extract nssm.exe, and either:
REM         - add its folder to PATH, or
REM         - place nssm.exe next to this script (scripts\nssm.exe).
REM    3. The virtual environment must already exist at .venv (see SETUP.md).
REM
REM  The service is configured to:
REM    - start automatically at boot
REM    - restart automatically if the process exits
REM    - write stdout/stderr to logs\ and rotate them at 10 MB
REM ============================================================================
setlocal enabledelayedexpansion

REM --- Resolve the project root (parent of this script's folder) --------------
pushd "%~dp0.."
set "ROOT=%CD%"
popd

set "SERVICE_NAME=TradingAssistant"
set "PYTHON=%ROOT%\.venv\Scripts\python.exe"
set "LOGDIR=%ROOT%\logs"

REM --- Locate nssm (PATH first, then next to this script) ---------------------
set "NSSM=nssm"
where nssm >nul 2>&1
if errorlevel 1 (
    if exist "%~dp0nssm.exe" (
        set "NSSM=%~dp0nssm.exe"
    ) else (
        echo [ERROR] NSSM not found in PATH and not found at "%~dp0nssm.exe".
        echo         Download it from https://nssm.cc/download and retry.
        pause
        exit /b 1
    )
)

REM --- Require administrator privileges ---------------------------------------
net session >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This script must be run as Administrator.
    echo         Right-click install_service.bat and choose "Run as administrator".
    pause
    exit /b 1
)

REM --- Validate the virtual environment ---------------------------------------
if not exist "%PYTHON%" (
    echo [ERROR] Python not found at "%PYTHON%".
    echo         Create the virtual environment first:
    echo             python -m venv .venv
    echo             .venv\Scripts\activate
    echo             pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%ROOT%\main.py" (
    echo [ERROR] main.py not found in "%ROOT%". Run this script from the repo.
    pause
    exit /b 1
)

if not exist "%LOGDIR%" mkdir "%LOGDIR%"

echo ============================================================
echo  Installing service  : %SERVICE_NAME%
echo  Python             : %PYTHON%
echo  Working directory  : %ROOT%
echo ============================================================

echo Removing any previous "%SERVICE_NAME%" service (if present)...
"%NSSM%" stop   "%SERVICE_NAME%" >nul 2>&1
"%NSSM%" remove "%SERVICE_NAME%" confirm >nul 2>&1

echo Installing...
"%NSSM%" install "%SERVICE_NAME%" "%PYTHON%" "main.py"
if errorlevel 1 (
    echo [ERROR] nssm install failed.
    pause
    exit /b 1
)

REM --- Service metadata -------------------------------------------------------
"%NSSM%" set "%SERVICE_NAME%" AppDirectory   "%ROOT%"
"%NSSM%" set "%SERVICE_NAME%" DisplayName    "Trading Assistant"
"%NSSM%" set "%SERVICE_NAME%" Description    "Monitors TradingView/MT5 signals and executes pre-planned trades."
"%NSSM%" set "%SERVICE_NAME%" Start          SERVICE_AUTO_START

REM --- Logging (NSSM rotates stdout/stderr at 10 MB) --------------------------
"%NSSM%" set "%SERVICE_NAME%" AppStdout      "%LOGDIR%\service-out.log"
"%NSSM%" set "%SERVICE_NAME%" AppStderr      "%LOGDIR%\service-err.log"
"%NSSM%" set "%SERVICE_NAME%" AppRotateFiles 1
"%NSSM%" set "%SERVICE_NAME%" AppRotateOnline 1
"%NSSM%" set "%SERVICE_NAME%" AppRotateBytes 10485760

REM --- Restart on failure -----------------------------------------------------
REM  NSSM has no native "max restart count"; crash-loop protection is provided
REM  by the throttle below, which caps restarts to at most one per 10 seconds,
REM  and a 10-second delay before each restart. This prevents hammering the
REM  broker's API if the process is dying in a loop. To stop a loop, run
REM  "nssm stop TradingAssistant" (a deliberate stop is not restarted).
"%NSSM%" set "%SERVICE_NAME%" AppExit Default Restart
"%NSSM%" set "%SERVICE_NAME%" AppRestartDelay 10000
"%NSSM%" set "%SERVICE_NAME%" AppThrottle 10000

REM --- Give the process 15s to shut down gracefully on stop -------------------
"%NSSM%" set "%SERVICE_NAME%" AppStopMethodConsole 15000

echo Starting service...
"%NSSM%" start "%SERVICE_NAME%"

echo.
echo ============================================================
echo  Done.
echo    Check status : sc query %SERVICE_NAME%
echo    Stop         : nssm stop %SERVICE_NAME%
echo    Start        : nssm start %SERVICE_NAME%
echo    Remove       : nssm remove %SERVICE_NAME% confirm
echo    Logs         : %LOGDIR%\service-out.log  and  %LOGDIR%\trading-assistant.log
echo ============================================================
pause
endlocal
