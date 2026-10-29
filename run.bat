@echo off
cd /d "%~dp0"
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
start "" http://localhost:8701
.venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 8701
