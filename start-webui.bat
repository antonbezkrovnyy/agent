@echo off
cd /d "%~dp0"
if not exist "webui\.venv\Scripts\python.exe" (
  echo Web UI environment is missing. Run setup-webui.bat first.
  pause
  exit /b 1
)
"webui\.venv\Scripts\python.exe" run_webui.py
