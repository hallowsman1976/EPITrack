"""ออกเวอร์ชันใหม่ให้ทุกเครื่องอัปเดต (ผ่าน Google Drive for desktop)

  release.bat          ออกเวอร์ชันตาม VERSION ใน paths.py
  release.bat --init   ครั้งแรกเท่านั้น: สร้างไฟล์ว่างบน Drive เพื่อเอารหัสไฟล์ไปฝังในโปรแกรม

ตรวจก่อนออก (ไม่ผ่านข้อใด หยุดทันที ไม่มีอะไรถูกส่ง):
  1. เลขเวอร์ชันมากกว่าเวอร์ชันบน Drive
  2. มีหัวข้อ "## v<เวอร์ชัน>" ใน CHANGELOG.md
  3. git commit ครบ และยังไม่มี tag v<เวอร์ชัน>
  4. ตารางวัคซีนและ mapping เริ่มต้นอ่านได้และผ่านการตรวจ
แล้ว build -> เขียนทับ EPITrack-latest.zip -> เขียนทับ latest.json (ไฟล์สุดท้าย) -> ติด tag -> สำรอง git

เขียนทับไฟล์เดิมเสมอ (ไม่ลบแล้วสร้างใหม่) รหัสไฟล์บน Drive จึงไม่เปลี่ยน
โฟลเดอร์ปลายทาง (เลือกตามลำดับ):
  1. EPITRACK_RELEASE_DIR ถ้ากำหนดไว้
  2. ...\\My Drive\\EPITrack-updates ถ้ามี Google Drive for desktop (ซิงก์ขึ้นเอง)
  3. release\\EPITrack-updates ในโปรเจกต์ = อัปโหลดเอง: เปิด drive.google.com แล้ว "จัดการเวอร์ชัน"
     อัปโหลดทับไฟล์เดิมทีละไฟล์ (ห้ามลากเป็นไฟล์ใหม่ รหัสไฟล์จะเปลี่ยน)
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import date
from pathlib import Path

from paths import UPDATE_MANIFEST_ID, UPDATE_ZIP_ID, VERSION, version_tuple

ROOT = Path(__file__).parent
FOLDER = "EPITrack-updates"


class Stop(Exception):
    pass


MANUAL_DIR = ROOT / "release" / FOLDER  # ไม่มี Google Drive for desktop: อัปโหลดเองผ่านเว็บ


def drive_dir() -> tuple[Path, bool]:
    """(โฟลเดอร์ปลายทาง, ต้องอัปโหลดเองหรือไม่)"""
    env = os.environ.get("EPITRACK_RELEASE_DIR")
    if env:
        return Path(env), False
    for letter in "GHIJKLMNOPQRSTUVWXYZDEF":
        for name in ("My Drive", "ไดรฟ์ของฉัน"):
            base = Path(f"{letter}:/{name}")
            if base.is_dir():
                return base / FOLDER, False
    return MANUAL_DIR, True


def git(*args: str) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def changelog_notes(version: str) -> str:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"^## v{re.escape(version)}\b.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not match or not match.group(1).strip():
        raise Stop(f"CHANGELOG.md ยังไม่มีหัวข้อ \"## v{version}\" หรือยังไม่มีรายการใต้หัวข้อ")
    return match.group(1).strip()


def check_defaults() -> int:
    import schedule
    try:
        rules = schedule.load_default()
        schedule.default_vaccine_map()
    except Exception as exc:
        raise Stop(f"ตารางวัคซีนเริ่มต้นไม่ถูกต้อง: {exc}") from exc
    return len(rules.series)


def overwrite(src: Path, dst: Path) -> None:
    """เขียนทับเนื้อไฟล์เดิม (ไม่ลบไฟล์) ให้ Google Drive เก็บรหัสไฟล์เดิมไว้"""
    shutil.copyfile(src, dst)


def init() -> None:
    folder, manual = drive_dir()
    folder.mkdir(parents=True, exist_ok=True)
    # ไฟล์ตั้งต้น: เวอร์ชัน 0.0.0 ทุกเครื่องจึงไม่เห็นว่ามีอัปเดต จนกว่าจะออกเวอร์ชันจริง
    for name, content in (("latest.json", json.dumps({"version": "0.0.0", "sha256": "0" * 64})),
                          ("EPITrack-latest.zip", "placeholder: replaced by release.bat")):
        path = folder / name
        if path.exists():
            print(f"มีอยู่แล้ว: {path}")
        else:
            path.write_text(content, encoding="utf-8")
            print(f"สร้าง: {path}")
    first = (f"1. เปิด drive.google.com สร้างโฟลเดอร์ชื่อ {FOLDER} ใน \"ไดรฟ์ของฉัน\"\n"
             f"     แล้วลาก 2 ไฟล์จาก {folder} ขึ้นไปไว้ในโฟลเดอร์นั้น"
             if manual else
             f"1. รอให้ Google Drive ซิงก์เสร็จ แล้วเปิด drive.google.com โฟลเดอร์ {FOLDER}")
    print(f"""
ขั้นต่อไป (ทำครั้งเดียว):
  {first}
  2. คลิกขวาที่ latest.json -> แชร์ -> การเข้าถึงทั่วไป: "ทุกคนที่มีลิงก์" (ผู้มีสิทธิ์อ่าน) -> คัดลอกลิงก์
     รหัสไฟล์คือส่วนหลัง /d/ เช่น https://drive.google.com/file/d/<รหัส>/view
  3. ทำแบบเดียวกันกับ EPITrack-latest.zip
  4. ใส่รหัสทั้งสองใน paths.py (UPDATE_MANIFEST_ID, UPDATE_ZIP_ID) แล้ว commit
     อย่าแชร์ทั้งโฟลเดอร์ แชร์เฉพาะ 2 ไฟล์นี้""")


def release() -> None:
    if not (UPDATE_MANIFEST_ID and UPDATE_ZIP_ID):
        raise Stop("ยังไม่ได้ใส่รหัสไฟล์ Google Drive ใน paths.py (รัน release.bat --init ก่อน)")
    folder, manual = drive_dir()
    manifest_path, zip_target = folder / "latest.json", folder / "EPITrack-latest.zip"
    if not manifest_path.exists() or not zip_target.exists():
        raise Stop(f"ไม่พบไฟล์ใน {folder} (รัน release.bat --init ก่อน)")

    published = json.loads(manifest_path.read_text(encoding="utf-8-sig")).get("version", "0.0.0")
    print(f"[1/4] เวอร์ชันบน Drive: {published} -> เวอร์ชันใหม่: {VERSION}")
    if version_tuple(VERSION) <= version_tuple(published):
        raise Stop(f"เปลี่ยน VERSION ใน paths.py ให้มากกว่า {published} ก่อน")

    notes = changelog_notes(VERSION)
    print("[2/4] CHANGELOG ครบ")

    if git("status", "--porcelain"):
        raise Stop("ยังมีไฟล์ที่ไม่ได้ commit (ดู git status) commit ให้ครบก่อนออกเวอร์ชัน")
    if git("tag", "--list", f"v{VERSION}"):
        raise Stop(f"มี tag v{VERSION} อยู่แล้ว")
    print("[3/4] git commit ครบ")

    print(f"[4/4] ตารางวัคซีนเริ่มต้นผ่านการตรวจ ({check_defaults()} ชนิด)")

    import build
    zip_path = build.build()
    data = zip_path.read_bytes()
    manifest = {
        "version": VERSION,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "date": date.today().isoformat(),
        "notes": notes,
    }
    overwrite(zip_path, zip_target)  # zip ก่อน latest.json เครื่องอื่นจะได้ไม่เห็นเวอร์ชันใหม่ก่อนไฟล์พร้อม
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    git("tag", "-a", f"v{VERSION}", "-m", f"EPITrack v{VERSION}")

    backup = folder.parent / "EPITrack-backup"
    backup.mkdir(exist_ok=True)
    git("bundle", "create", str(backup / "EPITrack-source.bundle"), "--all")

    print(f"""
ออกเวอร์ชัน {VERSION} แล้ว
  {zip_target} ({len(data) / 1024 / 1024:.1f} MB)
  {manifest_path}
  สำรองโค้ด: {backup / 'EPITrack-source.bundle'} (อย่าแชร์ไฟล์นี้)""")
    if manual:
        print(f"""
อัปโหลดขึ้น Google Drive เอง (ทำตามลำดับ zip ก่อน แล้วค่อย latest.json):
  1. drive.google.com > โฟลเดอร์ {FOLDER} > คลิกขวา EPITrack-latest.zip > ข้อมูลไฟล์ > จัดการเวอร์ชัน
     > อัปโหลดเวอร์ชันใหม่ > เลือก {zip_target}  รอจนอัปโหลดเสร็จ
  2. ทำแบบเดียวกันกับ latest.json > เลือก {manifest_path}
  ห้ามลากเป็นไฟล์ใหม่หรือลบไฟล์เดิม รหัสไฟล์จะเปลี่ยน และเครื่องอื่นจะหาไม่เจอ
  (ถ้าต้องการสำรองโค้ด อัปโหลด EPITrack-source.bundle ไว้ที่อื่นที่ไม่ได้แชร์)
เมื่ออัปโหลดเสร็จ เครื่องอื่นจะเห็นแถบแจ้งเตือนตอนเปิดโปรแกรมครั้งถัดไป""")
    else:
        print("รอ Google Drive ซิงก์เสร็จ (ไอคอนในแถบงาน) แล้วเครื่องอื่นจะเห็นแถบแจ้งเตือนตอนเปิดโปรแกรมครั้งถัดไป")


def main() -> int:
    try:
        init() if "--init" in sys.argv else release()
        return 0
    except Stop as exc:
        print(f"\nหยุด: {exc}")
        return 1
    except subprocess.CalledProcessError as exc:
        print(f"\nหยุด: คำสั่ง {' '.join(exc.cmd)} ไม่สำเร็จ\n{exc.stderr}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
