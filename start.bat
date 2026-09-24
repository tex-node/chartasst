@echo off
REM ============================================================================
REM  start.bat - start the Trading Assistant
REM
REM  Runs the Flask server + MT5 connection using the project's virtual
REM  environment. Trading stays ALERT-ONLY while LIVE_TRADING=false in .env.
REM
REM  Usage:
REM      start.bat                       normal start
REM      start.bat --telegram-listen     also enable Telegram commands
REM      start.bat --dry-run             force alert-only for this run
REM
REM  Stop with Ctrl+C, or just close the window.
REM ============================================================================
setlocal

REM --- Project root is this file's own folder, which ends with a backslash ----
set "ROOT=%~dp0"
set "PYTHON=%ROOT%.venv\Scripts\python.exe"

title Trading Assistant

if not exist "%PYTHON%" (
    echo [ERROR] Python not found at "%PYTHON%"
    echo.
    echo Create the virtual environment first:
    echo     python -m venv .venv
    echo     .venv\Scripts\activate
    echo     pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "%ROOT%main.py" (
    echo [ERROR] main.py not found in "%ROOT%"
    echo         Put start.bat in the project folder next to main.py.
    pause
    exit /b 1
)

cd /d "%ROOT%"

echo ============================================================
echo  Trading Assistant
echo  Project : %ROOT%
echo  Python  : %PYTHON%
echo  Mode    : set by LIVE_TRADING in .env - alert-only means no orders
echo  URL     : http://127.0.0.1:5000/health
echo  Stop    : Ctrl+C
echo ============================================================
echo.

"%PYTHON%" main.py %*
set "EXITCODE=%ERRORLEVEL%"

echo.
echo Trading Assistant stopped with exit code %EXITCODE%.
echo.
pause
endlocal
