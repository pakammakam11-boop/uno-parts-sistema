@echo off
cd /d "%~dp0"
rem Demo prisijungimas (personos) veikia tik su HR_DEMO=1.
set HR_DEMO=1
start "" http://localhost:8770
py -3.12 -m uvicorn app.main:app --port 8770
