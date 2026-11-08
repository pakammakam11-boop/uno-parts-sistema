@echo off
rem UNO Parts: saskaitu atsisiuntimas is UNO sistemos (Vercel) i C:\UNOPARTS_IMPORT Rivilei.
rem   uno_import.bat           - vienas paleidimas (rezultatas log.txt)
rem   uno_import.bat install   - suplanuota uzduotis kas 1 min. (SYSTEM)
rem   uno_import.bat uninstall - pasalinti suplanuota uzduoti
setlocal
set "DIR=%~dp0"
if /i "%~1"=="install" goto install
if /i "%~1"=="uninstall" goto uninstall
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%DIR%uno_import.ps1"
exit /b %ERRORLEVEL%

:install
schtasks /Create /TN "UNO Parts saskaitu importas" /TR "\"%DIR%uno_import.bat\"" /SC MINUTE /MO 1 /RU SYSTEM /RL HIGHEST /F
exit /b %ERRORLEVEL%

:uninstall
schtasks /Delete /TN "UNO Parts saskaitu importas" /F
exit /b %ERRORLEVEL%
