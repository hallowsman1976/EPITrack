@echo off
rem ออกเวอร์ชันใหม่ให้ทุกเครื่องอัปเดตผ่าน Google Drive (ดูรายละเอียดใน release.py)
rem ครั้งแรกใช้: release.bat --init
cd /d "%~dp0"
chcp 65001 >nul
.venv\Scripts\python.exe -m pip install -q -r requirements-build.txt
.venv\Scripts\python.exe release.py %*
pause
