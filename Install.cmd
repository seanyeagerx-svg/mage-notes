@echo off
chcp 65001 >nul
cd /d "%~dp0"
py -3.14 -m venv .venv
if errorlevel 1 goto fail
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto fail
echo Installation complete. See README for next step.
pause
exit /b 0
:fail
echo Installation failed. Please check the message above.
pause
exit /b 1
