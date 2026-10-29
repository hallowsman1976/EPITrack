@echo off
rem สร้าง EPITrack.exe และ dist\EPITrack-<เวอร์ชัน>.zip ไว้ทดสอบในเครื่อง (ไม่ส่งให้เครื่องอื่น)
cd /d "%~dp0"
chcp 65001 >nul
if not exist .venv (
  python -m venv .venv
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
.venv\Scripts\python.exe -m pip install -q -r requirements-build.txt
.venv\Scripts\python.exe build.py
pause
