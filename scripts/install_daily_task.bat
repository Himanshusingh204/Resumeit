@echo off
rem Registers a Windows Task Scheduler job that runs the daily scan at 09:00. It only finds jobs and builds resumes.
rem It NEVER applies. Limits (jobs/day, AI calls) come from PUT /api/saved-search. Create data\STOP to halt a run.
rem Remove it any time:  schtasks /Delete /TN ResumeitDailyScan /F
cd /d "%~dp0.."
schtasks /Create /SC DAILY /ST 09:00 /TN ResumeitDailyScan /TR "cmd /c cd /d \"%CD%\" && .venv\Scripts\python.exe -m app.scan" /F
