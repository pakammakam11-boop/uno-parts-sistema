@echo off
rem UNO apskaita: gamybinis paleidimas (be demo duomenu).
cd /d "%~dp0"
set UNO_DEMO=0
set HR_DEMO=1
set UNO_DB=data\uno.db
start "" http://127.0.0.1:8780/app
py -3.12 -m uvicorn app.main:app --host 127.0.0.1 --port 8780
