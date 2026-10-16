@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo First-time setup:
  echo   python -m venv .venv
  echo   .venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-dev.txt
  pause
  exit /b 1
)
echo Chameleon workbench - keep this window open. Press Ctrl+C to stop.
".venv\Scripts\python.exe" -m engine.workbench --open-browser
if errorlevel 1 pause
