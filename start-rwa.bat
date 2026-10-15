@echo off
rem ===========================================================================
rem  RWA Legal Passport - local launcher
rem
rem  Double-click this file (or the "RWA Legal Passport" desktop shortcut) to:
rem    1. start the FastAPI backend  -> http://127.0.0.1:8000  (own window)
rem    2. start the React/Vite UI    -> http://localhost:5173  (own window)
rem    3. open the UI in the default browser once it responds
rem
rem  A server of this app that is already running is reused, so duplicates are
rem  never started. If another program occupies port 8000 or 5173, nothing is
rem  started and the launcher names that program instead.
rem
rem  Both servers listen on this computer only (localhost); nothing is exposed
rem  to the network. To stop the app, press Ctrl+C in the Backend and Frontend
rem  windows, or close those two windows.
rem
rem  Internal: "start-rwa.bat --backend" / "start-rwa.bat --frontend" run one
rem  server in the current window; the launcher opens those windows itself.
rem  "--frontend-title" is a helper used by the frontend window.
rem ===========================================================================
setlocal EnableExtensions DisableDelayedExpansion

rem Absolute paths only, so the script works from a shortcut and after a reboot.
set "SELF=%~f0"
set "ROOT=%~dp0"
set "ROOT=%ROOT:~0,-1%"
set "FRONTEND_DIR=%ROOT%\frontend"
set "VENV_PY=%ROOT%\.venv\Scripts\python.exe"
set "BACKEND_PORT=8000"
set "FRONTEND_PORT=5173"
set "BACKEND_URL=http://127.0.0.1:%BACKEND_PORT%"
set "FRONTEND_URL=http://localhost:%FRONTEND_PORT%"
set "BACKEND_WAIT_SECONDS=90"
set "FRONTEND_WAIT_SECONDS=120"
set "CURL=%SystemRoot%\System32\curl.exe"
set "CURL_OPTS=-s --noproxy 127.0.0.1,localhost,::1"

rem npm: prefer PATH, fall back to the default Node.js install location.
set "NPM="
for /f "delims=" %%I in ('where npm.cmd 2^>nul') do if not defined NPM set "NPM=%%I"
if not defined NPM if exist "%ProgramFiles%\nodejs\npm.cmd" set "NPM=%ProgramFiles%\nodejs\npm.cmd"

if /i "%~1"=="--backend"  goto :run_backend
if /i "%~1"=="--frontend" goto :run_frontend
if /i "%~1"=="--frontend-title" goto :frontend_title

rem ===========================================================================
rem  Launcher
rem ===========================================================================
title RWA Legal Passport - Launcher
echo.
echo ============================================================
echo  RWA Legal Passport - starting
echo ============================================================
echo.

if not exist "%VENV_PY%" goto :err_venv
if not exist "%FRONTEND_DIR%\package.json" goto :err_frontend
if not exist "%FRONTEND_DIR%\node_modules\" goto :err_node_modules
if not defined NPM goto :err_npm
if not exist "%CURL%" goto :err_curl

rem ---- Check both ports before starting anything ----------------------------
set "START_BACKEND=0"
set "START_FRONTEND=0"

call :port_owner %BACKEND_PORT%
if not defined PORT_PID goto :backend_port_free
call :backend_alive
if errorlevel 1 goto :err_backend_port
echo [OK] Backend is already running on port %BACKEND_PORT% - reusing it.
goto :check_frontend
:backend_port_free
set "START_BACKEND=1"

:check_frontend
call :port_owner %FRONTEND_PORT%
if not defined PORT_PID goto :frontend_port_free
call :frontend_alive
if errorlevel 1 goto :err_frontend_port
echo [OK] Frontend is already running on port %FRONTEND_PORT% - reusing it.
goto :start_servers
:frontend_port_free
set "START_FRONTEND=1"

rem ---- Start what is not running yet, each in its own window -----------------
:start_servers
if "%START_BACKEND%"=="1" call :start_backend
if "%START_FRONTEND%"=="1" call :start_frontend

rem ---- Wait until both respond -------------------------------------------------
rem Probe with netstat first: on Windows a refused HTTP connection takes ~2 s.
echo.
<nul set /p "=[..] Waiting for the backend "
set /a TRIES=0
:backend_wait_loop
call :port_owner %BACKEND_PORT%
if not defined PORT_PID goto :backend_wait_next
call :backend_alive
if not errorlevel 1 goto :backend_ready
:backend_wait_next
set /a TRIES+=1
if %TRIES% geq %BACKEND_WAIT_SECONDS% goto :err_backend_timeout
<nul set /p "=."
ping -n 2 127.0.0.1 >nul
goto :backend_wait_loop
:backend_ready
echo  ready.

<nul set /p "=[..] Waiting for the frontend "
set /a TRIES=0
:frontend_wait_loop
call :port_owner %FRONTEND_PORT%
if not defined PORT_PID goto :frontend_wait_next
call :frontend_alive
if not errorlevel 1 goto :frontend_ready
:frontend_wait_next
set /a TRIES+=1
if %TRIES% geq %FRONTEND_WAIT_SECONDS% goto :err_frontend_timeout
<nul set /p "=."
ping -n 2 127.0.0.1 >nul
goto :frontend_wait_loop
:frontend_ready
echo  ready.

rem ---- Open the app ------------------------------------------------------------
echo [..] Opening %FRONTEND_URL% in your default browser...
start "" "%FRONTEND_URL%/"
echo.
echo ============================================================
echo  RWA Legal Passport is running.
echo    Application : %FRONTEND_URL%
echo    API docs    : %BACKEND_URL%/docs
echo.
echo  To stop it: press Ctrl+C in the "Backend" and "Frontend"
echo  windows, or simply close those two windows.
echo ============================================================
echo.
echo This window closes automatically in 15 seconds.
timeout /t 15 >nul 2>&1
endlocal
exit /b 0

rem ===========================================================================
rem  Server windows (started by the launcher)
rem ===========================================================================
:run_backend
title RWA Legal Passport - Backend (port %BACKEND_PORT%)
cd /d "%ROOT%"
echo ============================================================
echo  RWA Legal Passport - BACKEND (FastAPI)
echo    API      : %BACKEND_URL%
echo    API docs : %BACKEND_URL%/docs
echo.
echo  Keep this window open while you use the app.
echo  To stop: press Ctrl+C (answer Y if asked), or close this window.
echo ============================================================
echo.
"%VENV_PY%" -m uvicorn app.main:app --host 127.0.0.1 --port %BACKEND_PORT%
echo.
echo Backend stopped (exit code %ERRORLEVEL%). Press any key to close this window.
pause >nul
exit /b

:run_frontend
title RWA Legal Passport - Frontend (port %FRONTEND_PORT%)
cd /d "%FRONTEND_DIR%"
rem Make sure node.exe (next to npm.cmd) is on PATH for the vite shim.
for %%D in ("%NPM%") do set "PATH=%%~dpD;%PATH%"
echo ============================================================
echo  RWA Legal Passport - FRONTEND (React + Vite)
echo    Application : %FRONTEND_URL%
echo.
echo  Keep this window open while you use the app.
echo  To stop: press Ctrl+C (answer Y if asked), or close this window.
echo ============================================================
echo.
rem npm renames this window ("npm", then "cmd.exe"); a background helper puts
rem the title back once Vite is listening. start /b: it ignores Ctrl+C.
start "" /b "%ComSpec%" /d /c ""%SELF%" --frontend-title"
rem --strictPort: fail instead of silently moving to another port.
call "%NPM%" run dev -- --port %FRONTEND_PORT% --strictPort
echo.
echo Frontend stopped (exit code %ERRORLEVEL%). Press any key to close this window.
pause >nul
exit /b

:frontend_title
set /a TRIES=0
:frontend_title_loop
call :port_owner %FRONTEND_PORT%
if defined PORT_PID goto :frontend_title_set
set /a TRIES+=1
if %TRIES% geq %FRONTEND_WAIT_SECONDS% exit /b 0
ping -n 2 127.0.0.1 >nul
goto :frontend_title_loop
:frontend_title_set
ping -n 2 127.0.0.1 >nul
title RWA Legal Passport - Frontend (port %FRONTEND_PORT%)
exit /b 0

rem ===========================================================================
rem  Helpers
rem ===========================================================================
:start_backend
echo [..] Starting the backend  on port %BACKEND_PORT% in a new window...
start "RWA Legal Passport - Backend" /D "%ROOT%" "%ComSpec%" /d /s /c ""%SELF%" --backend"
exit /b 0

:start_frontend
echo [..] Starting the frontend on port %FRONTEND_PORT% in a new window...
start "RWA Legal Passport - Frontend" /D "%FRONTEND_DIR%" "%ComSpec%" /d /s /c ""%SELF%" --frontend"
exit /b 0

:backend_alive
rem errorlevel 0 when THIS app's backend answers its /health endpoint.
"%CURL%" %CURL_OPTS% --max-time 5 "%BACKEND_URL%/health" 2>nul | findstr /R /C:"status.:.ok.*environment" >nul
exit /b %errorlevel%

:frontend_alive
rem errorlevel 0 when THIS app's frontend is served on the port.
"%CURL%" %CURL_OPTS% --max-time 10 "%FRONTEND_URL%/" 2>nul | findstr /C:"<title>RWA Legal Passport</title>" >nul
exit /b %errorlevel%

:port_owner
rem Sets PORT_PID (left undefined when the port is free) and PORT_PROC for
rem the program listening on TCP port %1, on IPv4 or IPv6.
rem The state column of netstat is localized (e.g. Turkish Windows), so
rem listening sockets are matched by their foreign address 0.0.0.0:0 / [::]:0.
set "PORT_PID="
set "PORT_PROC=unknown"
for /f "tokens=5" %%P in ('netstat -ano -p TCP ^| findstr /R /C:":%~1  *0\.0\.0\.0:0 "') do if not defined PORT_PID set "PORT_PID=%%P"
for /f "tokens=5" %%P in ('netstat -ano -p TCPv6 ^| findstr /R /C:":%~1  *\[::\]:0 "') do if not defined PORT_PID set "PORT_PID=%%P"
if not defined PORT_PID exit /b 0
for /f "tokens=1 delims=," %%N in ('tasklist /FI "PID eq %PORT_PID%" /FO CSV /NH ^| findstr /C:",\"%PORT_PID%\","') do set "PORT_PROC=%%~N"
exit /b 0

rem ===========================================================================
rem  Errors
rem ===========================================================================
:err_backend_port
echo [ERROR] Port %BACKEND_PORT% is already in use by another program:
echo         program: %PORT_PROC%   PID: %PORT_PID%
echo         It is not the RWA Legal Passport backend, so no second backend
echo         was started. Close that program, then run this launcher again.
goto :fail

:err_frontend_port
echo [ERROR] Port %FRONTEND_PORT% is already in use by another program:
echo         program: %PORT_PROC%   PID: %PORT_PID%
echo         It is not the RWA Legal Passport frontend, so no second frontend
echo         was started. Close that program, then run this launcher again.
goto :fail

:err_backend_timeout
echo.
echo [ERROR] The backend did not respond within %BACKEND_WAIT_SECONDS% seconds.
echo         Check the "RWA Legal Passport - Backend" window for errors.
goto :fail

:err_frontend_timeout
echo.
echo [ERROR] The frontend did not respond within %FRONTEND_WAIT_SECONDS% seconds.
echo         Check the "RWA Legal Passport - Frontend" window for errors.
goto :fail

:err_venv
echo [ERROR] Python virtual environment not found:
echo         %VENV_PY%
goto :fail

:err_frontend
echo [ERROR] Frontend folder not found: %FRONTEND_DIR%
goto :fail

:err_node_modules
echo [ERROR] Frontend packages are missing: %FRONTEND_DIR%\node_modules
echo         Run "npm install" once in the frontend folder, then try again.
goto :fail

:err_npm
echo [ERROR] npm was not found. Install Node.js from https://nodejs.org
goto :fail

:err_curl
echo [ERROR] curl.exe was not found in %SystemRoot%\System32.
goto :fail

:fail
echo.
echo The application was not started. Press any key to close this window.
pause >nul
endlocal
exit /b 1
