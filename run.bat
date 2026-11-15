@echo off
cd /d "%~dp0"
if not exist .env copy .env.example .env >nul
start "" http://127.0.0.1:8000
.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
