@echo off
rem Offline real-browser tests, for an operator (E2E_TESTING.md, "Offline run").
rem Start it while the computer is still online: it prints the instructions, waits until you
rem have turned airplane mode on, runs the tests, and says when you may turn it off again.
rem It never changes the network settings itself.
setlocal
cd /d "%~dp0.."
".venv\Scripts\python.exe" scripts\e2e_run.py --offline-host %*
set "RESULT=%ERRORLEVEL%"
set "MEANING=an unexpected exit code"
if "%RESULT%"=="0" set "MEANING=PASSED"
if "%RESULT%"=="1" set "MEANING=FAILED - a test, a check or the isolation evidence failed"
if "%RESULT%"=="2" set "MEANING=REFUSED - nothing was started"
echo.
echo Exit code %RESULT%: %MEANING%.
echo This number is the runner's exit code, not a count of tests: 0 means passed, 1 failed, 2 refused.
echo Details: the RESULT line above, or the newest folder in scratch\e2e\ and its summary.json.
pause
exit /b %RESULT%
