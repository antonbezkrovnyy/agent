@echo off
cd /d "%~dp0"
uv venv webui/.venv --python .venv/Scripts/python.exe
if errorlevel 1 exit /b 1
uv pip install --python webui/.venv/Scripts/python.exe -r webui/requirements.txt
if errorlevel 1 exit /b 1
cd webui/frontend
call npm install
if errorlevel 1 exit /b 1
call npm run build
