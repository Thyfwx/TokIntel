@echo off
REM Windows double-click launcher for TokIntel.
REM Sets up its own virtual environment and dependencies on first run.
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo First run - setting up a local Python environment...
    python -m venv venv
)

venv\Scripts\python.exe -c "import importlib.util as u,sys;sys.exit(0 if all(u.find_spec(m) for m in ('requests','colorama','rich')) else 1)" 2>nul || venv\Scripts\python.exe -m pip install -q -r requirements.txt

venv\Scripts\python.exe tiktok_ui.py %*
pause
