r"""EPITrack-Updater.exe: ติดตั้งเวอร์ชันใหม่ หรือย้อนกลับไปเวอร์ชันก่อน

โปรแกรมหลักคัดลอกไฟล์นี้ไปไว้ใน %TEMP% แล้วเปิด (ไฟล์ในโฟลเดอร์โปรแกรมกำลังจะถูกแทนที่)
  --mode install  --zip <zip>   ย้ายเวอร์ชันเดิมไป _previous\ แตก zip ลงโฟลเดอร์โปรแกรม
  --mode rollback               สลับเวอร์ชันปัจจุบันกับ _previous\
ทั้งสองแบบ: รอโปรแกรมหลักปิด -> สลับไฟล์ -> เปิดโปรแกรมใหม่ -> ถ้าไม่ขึ้นใน 60 วินาที สลับกลับเอง
ไม่แตะไฟล์ของเครื่อง (config.json, ssop_backups ...) ตาม paths.PRESERVE
บันทึกการทำงานไว้ที่ update.log ในโฟลเดอร์โปรแกรม
"""

import argparse
import ctypes
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path

from paths import PORTS, PRESERVE

START_TIMEOUT = 60  # วินาที
LOG: Path | None = None


def log(message: str) -> None:
    line = f"{datetime.now():%Y-%m-%d %H:%M:%S}  {message}"
    print(line)
    if LOG:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


def wait_for_exit(pid: int, timeout: float = 30) -> None:
    synchronize = 0x00100000
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if handle:
        ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        ctypes.windll.kernel32.CloseHandle(handle)
    time.sleep(1.5)  # ให้ Windows ปล่อยไฟล์ DLL


def program_items(folder: Path) -> list[Path]:
    """ไฟล์ของตัวโปรแกรม (ไม่รวมไฟล์ของเครื่อง)"""
    return [p for p in folder.iterdir() if p.name not in PRESERVE]


def move(src: Path, dst_dir: Path) -> None:
    for attempt in range(15):
        try:
            shutil.move(str(src), str(dst_dir / src.name))
            return
        except PermissionError:
            if attempt == 14:
                raise
            time.sleep(1)


def move_all(items: list[Path], dst_dir: Path) -> None:
    dst_dir.mkdir(parents=True, exist_ok=True)
    for item in items:
        move(item, dst_dir)


def read_version(folder: Path) -> str:
    try:
        return (folder / "VERSION.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return "?"


def running_version() -> str | None:
    for port in PORTS:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
                data = json.load(r)
                if data.get("app") == "EPITrack":
                    return data.get("version")
        except Exception:
            continue
    return None


def start(app_dir: Path) -> subprocess.Popen:
    return subprocess.Popen([str(app_dir / "EPITrack.exe")], cwd=app_dir,
                            creationflags=subprocess.CREATE_NEW_CONSOLE)


def started(expected: str) -> bool:
    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if running_version() == expected:
            return True
        time.sleep(1)
    return False


def stop(proc: subprocess.Popen) -> None:
    subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
    time.sleep(2)


def swap_in(app_dir: Path, new_dir: Path, old_dir: Path) -> None:
    """ย้ายโปรแกรมปัจจุบันไป old_dir แล้วย้ายของใน new_dir เข้ามาแทน (ถ้าพังกลางทาง คืนของเดิม)"""
    current = program_items(app_dir)
    move_all(current, old_dir)
    try:
        move_all(list(new_dir.iterdir()), app_dir)
    except Exception:
        log("ย้ายไฟล์เวอร์ชันใหม่ไม่สำเร็จ กำลังคืนเวอร์ชันเดิม")
        for item in program_items(app_dir):
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
        move_all(list(old_dir.iterdir()), app_dir)
        raise


def install(app_dir: Path, zip_path: Path) -> bool:
    work = app_dir / "_update"
    staging = work / "staging"
    shutil.rmtree(staging, ignore_errors=True)
    log(f"แตกไฟล์ {zip_path.name}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(staging)
    new_dir = staging / "EPITrack"
    if not (new_dir / "EPITrack.exe").exists():
        raise RuntimeError("zip ไม่มี EPITrack/EPITrack.exe")
    new_version = read_version(new_dir)
    old_version = read_version(app_dir)

    previous = app_dir / "_previous"
    shutil.rmtree(previous, ignore_errors=True)
    log(f"ย้ายเวอร์ชัน {old_version} ไป _previous แล้วติดตั้งเวอร์ชัน {new_version}")
    swap_in(app_dir, new_dir, previous)

    log("เปิดเวอร์ชันใหม่")
    proc = start(app_dir)
    if started(new_version):
        log(f"อัปเดตเป็นเวอร์ชัน {new_version} สำเร็จ")
        shutil.rmtree(staging, ignore_errors=True)
        zip_path.unlink(missing_ok=True)
        return True

    log(f"เวอร์ชัน {new_version} เปิดไม่ขึ้นใน {START_TIMEOUT} วินาที กำลังย้อนกลับไปเวอร์ชัน {old_version}")
    stop(proc)
    failed = work / "failed"
    shutil.rmtree(failed, ignore_errors=True)
    swap_in(app_dir, previous, failed)
    start(app_dir)
    for leftover in (failed, staging):
        shutil.rmtree(leftover, ignore_errors=True)
    zip_path.unlink(missing_ok=True)
    log(f"ย้อนกลับไปเวอร์ชัน {old_version} แล้ว")
    return False


def rollback(app_dir: Path) -> bool:
    previous = app_dir / "_previous"
    if not (previous / "EPITrack.exe").exists():
        raise RuntimeError("ไม่มีเวอร์ชันก่อนหน้าให้ย้อนกลับ")
    target, current = read_version(previous), read_version(app_dir)
    swap = app_dir / "_update" / "swap"
    shutil.rmtree(swap, ignore_errors=True)
    log(f"ย้อนกลับจากเวอร์ชัน {current} ไปเวอร์ชัน {target}")
    swap_in(app_dir, previous, swap)       # ปัจจุบัน -> swap, _previous -> โปรแกรม
    move_all(list(swap.iterdir()), previous)  # เวอร์ชันที่เพิ่งถอดออก -> _previous (กลับไปได้อีก)
    proc = start(app_dir)
    ok = started(target)
    if ok:
        log(f"ย้อนกลับเป็นเวอร์ชัน {target} สำเร็จ")
    else:
        log(f"เวอร์ชัน {target} เปิดไม่ขึ้น กำลังกลับไปเวอร์ชัน {current}")
        stop(proc)
        swap_in(app_dir, previous, swap)
        move_all(list(swap.iterdir()), previous)
        start(app_dir)
    shutil.rmtree(swap, ignore_errors=True)
    return ok


def main() -> int:
    global LOG
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("install", "rollback"), required=True)
    parser.add_argument("--app-dir", required=True)
    parser.add_argument("--pid", type=int, required=True)
    parser.add_argument("--zip")
    args = parser.parse_args()
    app_dir = Path(args.app_dir)
    LOG = app_dir / "update.log"
    try:
        ctypes.windll.kernel32.SetConsoleTitleW("EPITrack – กำลังอัปเดต อย่าปิดหน้าต่างนี้")
    except Exception:
        pass

    log(f"เริ่ม {args.mode} ที่ {app_dir} รอโปรแกรมเดิมปิด (PID {args.pid})")
    wait_for_exit(args.pid)
    try:
        ok = install(app_dir, Path(args.zip)) if args.mode == "install" else rollback(app_dir)
    except Exception as exc:
        log(f"ผิดพลาด: {exc}")
        if not running_version() and (app_dir / "EPITrack.exe").exists():
            start(app_dir)
        ok = False
    if not ok:
        print("\nการอัปเดตไม่สำเร็จ ดูรายละเอียดใน update.log หน้าต่างนี้จะปิดเองใน 30 วินาที")
        time.sleep(30)
    else:
        time.sleep(2)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
