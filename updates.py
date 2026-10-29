r"""ระบบอัปเดตโปรแกรมจาก Google Drive

แหล่งอัปเดตมี 2 ไฟล์ชื่อตายตัว (แชร์แบบ "ทุกคนที่มีลิงก์ดูได้")
  latest.json          {"version", "sha256", "size", "date", "notes"}
  EPITrack-latest.zip   ตัวโปรแกรม (zip เดียวกับที่แจก)

ขั้นตอนติดตั้ง (กด "อัปเดตเลย" เท่านั้น ไม่ติดตั้งเอง)
  1. ดาวน์โหลด zip ไว้ที่ _update\ แล้วตรวจ SHA-256 กับ latest.json
  2. คัดลอก EPITrack-Updater.exe ไป %TEMP% แล้วเปิด (ตัวมันเองก็ต้องถูกแทนที่)
  3. โปรแกรมนี้ปิดตัวเอง ตัวช่วยย้ายเวอร์ชันเดิมไป _previous\ แตกเวอร์ชันใหม่ แล้วเปิดใหม่
     ถ้าเวอร์ชันใหม่เปิดไม่ขึ้นใน 60 วินาที ตัวช่วยย้อนกลับเอง (ดู updater.py)

ทดสอบ: ตั้ง EPITRACK_UPDATE_DIR=<โฟลเดอร์> ให้อ่าน latest.json / EPITrack-latest.zip จากโฟลเดอร์นั้นแทน Drive
"""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

import db
from paths import DATA_DIR, FROZEN, UPDATE_MANIFEST_ID, UPDATE_ZIP_ID, VERSION, version_tuple

UPDATE_DIR = DATA_DIR / "_update"
PREVIOUS_DIR = DATA_DIR / "_previous"
UPDATER = "EPITrack-Updater.exe"
DRIVE_URL = "https://drive.usercontent.google.com/download?id={id}&export=download&confirm=t"
TIMEOUT = 20  # วินาที

_state = {"checked": None, "latest": None, "error": None, "dismissed": False}
_lock = threading.Lock()


class UpdateError(Exception):
    """ข้อความภาษาไทยที่แสดงให้ผู้ใช้ได้เลย"""


# ---------- แหล่งอัปเดต ----------
def source_ids() -> tuple[str, str]:
    config = db.load_config()
    return (config.get("update_manifest_id") or UPDATE_MANIFEST_ID,
            config.get("update_zip_id") or UPDATE_ZIP_ID)


def _local_dir() -> Path | None:
    value = os.environ.get("EPITRACK_UPDATE_DIR")
    return Path(value) if value else None


def configured() -> bool:
    return bool(_local_dir() or all(source_ids()))


def _fetch(name: str, file_id: str) -> bytes:
    local = _local_dir()
    if local:
        try:
            return (local / name).read_bytes()
        except OSError as exc:
            raise UpdateError(f"อ่านไฟล์ {local / name} ไม่ได้ ({exc.strerror})") from exc
    request = urllib.request.Request(DRIVE_URL.format(id=file_id), headers={"User-Agent": f"EPITrack/{VERSION}"})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read()
            html = "text/html" in response.headers.get("Content-Type", "")
    except Exception as exc:
        raise UpdateError("เชื่อมต่อ Google Drive ไม่ได้ (ตรวจสอบอินเทอร์เน็ต)") from exc
    if html:
        raise UpdateError(f"ดาวน์โหลด {name} จาก Google Drive ไม่ได้ ตรวจสอบรหัสไฟล์ และตั้งค่าแชร์เป็น \"ทุกคนที่มีลิงก์\"")
    return data


# ---------- ตรวจหาเวอร์ชันใหม่ ----------
def check() -> dict:
    """อ่าน latest.json ล่าสุด (ไม่ throw: เก็บข้อผิดพลาดไว้ใน status)"""
    manifest_id, _ = source_ids()
    latest, error = None, None
    if not configured():
        error = "ยังไม่ได้ตั้งค่าแหล่งอัปเดต"
    else:
        try:
            latest = json.loads(_fetch("latest.json", manifest_id).decode("utf-8-sig"))
            if not latest.get("version") or len(latest.get("sha256", "")) != 64:
                raise UpdateError("ไฟล์ latest.json ไม่ถูกต้อง")
        except UpdateError as exc:
            latest, error = None, str(exc)
        except ValueError:
            latest, error = None, "ไฟล์ latest.json ไม่ถูกต้อง"
    with _lock:
        _state.update(checked=datetime.now(), latest=latest, error=error)
    return status()


def check_in_background() -> None:
    threading.Thread(target=check, daemon=True).start()


def dismiss() -> None:
    """ "ไว้ทีหลัง": ซ่อนแถบแจ้งเตือนจนกว่าจะเปิดโปรแกรมครั้งถัดไป"""
    _state["dismissed"] = True


def previous_version() -> str | None:
    try:
        return (PREVIOUS_DIR / "VERSION.txt").read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def status() -> dict:
    with _lock:
        latest, checked, error = _state["latest"], _state["checked"], _state["error"]
    available = bool(latest) and version_tuple(latest["version"]) > version_tuple(VERSION)
    return {
        "current": VERSION,
        "latest": latest["version"] if latest else None,
        "notes": latest.get("notes", "") if latest else "",
        "date": latest.get("date", "") if latest else "",
        "available": available,
        "checked": checked.strftime("%H:%M") if checked else None,
        "error": error,
        "configured": configured(),
        "dismissed": _state["dismissed"],
        "can_install": FROZEN and (DATA_DIR / UPDATER).exists(),
        "previous": previous_version() if FROZEN else None,
    }


# ---------- ติดตั้ง ----------
def download() -> Path:
    """ดาวน์โหลด zip ของเวอร์ชันล่าสุด ตรวจ SHA-256 แล้วคืนที่อยู่ไฟล์"""
    with _lock:
        latest = _state["latest"]
    if not latest:
        raise UpdateError("ยังไม่พบเวอร์ชันใหม่ กด \"ตรวจหาอัปเดต\" ก่อน")
    _, zip_id = source_ids()
    data = _fetch("EPITrack-latest.zip", zip_id)
    if hashlib.sha256(data).hexdigest() != latest["sha256"].lower():
        raise UpdateError("ไฟล์ที่ดาวน์โหลดไม่ตรงกับที่ประกาศไว้ (อาจยังอัปโหลดไม่เสร็จ) ลองใหม่ภายหลัง")
    UPDATE_DIR.mkdir(exist_ok=True)
    path = UPDATE_DIR / f"EPITrack-{latest['version']}.zip"
    path.write_bytes(data)
    try:
        with zipfile.ZipFile(path) as zf:
            if "EPITrack/EPITrack.exe" not in zf.namelist():
                raise UpdateError("ไฟล์ zip ไม่มี EPITrack.exe")
    except zipfile.BadZipFile as exc:
        raise UpdateError("ไฟล์ zip เสียหาย") from exc
    return path


def launch_updater(mode: str, zip_path: Path | None = None) -> None:
    """เปิดตัวช่วยอัปเดตจากสำเนาใน %TEMP% แล้วปิดโปรแกรมนี้ภายใน 1 วินาที"""
    if not FROZEN:
        raise UpdateError("อัปเดตได้เฉพาะตอนใช้งานจาก EPITrack.exe")
    source = DATA_DIR / UPDATER
    if not source.exists():
        raise UpdateError(f"ไม่พบ {UPDATER} ในโฟลเดอร์โปรแกรม แตก zip เวอร์ชันล่าสุดทับด้วยตนเอง")
    temp = Path(tempfile.gettempdir())
    for old in temp.glob("EPITrack-Updater-*.exe"):  # สำเนาจากการอัปเดตครั้งก่อน
        try:
            old.unlink()
        except OSError:
            pass
    copy = temp / f"EPITrack-Updater-{int(time.time())}.exe"
    shutil.copy2(source, copy)
    args = [str(copy), "--mode", mode, "--app-dir", str(DATA_DIR), "--pid", str(os.getpid())]
    if zip_path:
        args += ["--zip", str(zip_path)]
    env = dict(os.environ, EPITRACK_NO_BROWSER="1")  # หน้าเว็บเดิมรอและโหลดใหม่เอง
    subprocess.Popen(args, cwd=tempfile.gettempdir(), env=env,
                     creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    threading.Timer(1.0, os._exit, [0]).start()
