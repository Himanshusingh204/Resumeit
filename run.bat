@echo off
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
if "%HOST%"=="" set HOST=127.0.0.1
if "%PORT%"=="" set PORT=8000
rem If Resumeit is already running, just open it (a second server would fight over the same database).
netstat -ano | findstr /R /C:"%HOST%:%PORT% .*LISTENING" >nul
if %errorlevel%==0 (
  start "" http://%HOST%:%PORT%
  exit /b
)
start "" http://%HOST%:%PORT%
set PY=python
if exist .venv\Scripts\python.exe set PY=.venv\Scripts\python
%PY% -m app
