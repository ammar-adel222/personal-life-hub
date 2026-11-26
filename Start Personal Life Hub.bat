@echo off
cd /d "%~dp0"
".venv\Scripts\python.exe" "run_app.py"
if errorlevel 1 pause
