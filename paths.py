"""ตำแหน่งไฟล์ของโปรแกรม ใช้ได้ทั้งตอนรันจากโค้ด และตอนรันเป็น EPITrack.exe (PyInstaller)

RESOURCE_DIR  ไฟล์ที่มากับโปรแกรม (templates, static, ตารางวัคซีนเริ่มต้น) อ่านอย่างเดียว
DATA_DIR      ไฟล์ของแต่ละเครื่อง (config.json, backups) อยู่ข้าง EPITrack.exe
              อัปเดตโปรแกรมด้วยการแตก zip ทับ ไฟล์เหล่านี้จึงไม่ถูกทับ
"""

import os
import sys
from pathlib import Path

VERSION = "0.1.0"

FROZEN = getattr(sys, "frozen", False)
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
DATA_DIR = Path(sys.executable).parent if FROZEN else Path(__file__).parent

# ไฟล์/โฟลเดอร์ในโฟลเดอร์โปรแกรมที่เป็นของเครื่องนี้ ไม่อยู่ใน zip และการอัปเดตห้ามแตะ
PRESERVE = {"config.json", "session.key", "backups", ".shortcut_created", "_previous", "_update", "update.log"}

# พอร์ตที่โปรแกรมใช้ (EPITRACK_PORTS="8801-8820" ใช้ตอนทดสอบ ไม่ให้ชนกับตัวที่เปิดใช้งานอยู่)
_first, _last = os.environ.get("EPITRACK_PORTS", "8701-8720").split("-")
PORTS = range(int(_first), int(_last) + 1)

# แหล่งอัปเดตบน Google Drive: รหัสไฟล์ latest.json และ EPITrack-latest.zip
# ยังว่างจนกว่าจะรัน release.bat --init แล้วนำรหัสไฟล์มาใส่
UPDATE_MANIFEST_ID = ""
UPDATE_ZIP_ID = ""


def version_tuple(version: str) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in version.strip().lstrip("v").split("."))
    except ValueError:
        return (0,)
