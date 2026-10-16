@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Please run Install.cmd first.
  pause
  exit /b 1
)
".venv\Scripts\python.exe" -X utf8 app.py %*
if errorlevel 1 pause
