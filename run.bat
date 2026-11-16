@echo off
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
rem If Resumeit is already running, just open it (a second server would fight over the same database).
netstat -ano | findstr /R /C:"127.0.0.1:8000 .*LISTENING" >nul
if %errorlevel%==0 (
  start "" http://127.0.0.1:8000
  exit /b
)
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
