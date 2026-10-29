"""สร้าง EPITrack.exe, EPITrack-Updater.exe และไฟล์ zip สำหรับแจก

รัน: build.bat (หรือ .venv\\Scripts\\python.exe build.py)
ได้: dist\\EPITrack\\ (โปรแกรม) และ dist\\EPITrack-<เวอร์ชัน>.zip
build อย่างเดียวไม่ส่งให้เครื่องอื่น: ออกเวอร์ชันจริงใช้ release.bat
"""

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

from paths import PRESERVE, VERSION

ROOT = Path(__file__).parent
BUILD = ROOT / "build"
DIST = ROOT / "dist"
APP = DIST / "EPITrack"

README = f"""EPITrack v{VERSION}
บันทึกและติดตามการรับวัคซีนพื้นฐาน (EPI) ของเด็กและวัยรุ่น โดยใช้ข้อมูลคนและประวัติวัคซีนจาก JHCIS

ติดตั้ง (เครื่องที่เป็นตัวหลักเครื่องเดียว)
  1. แตกไฟล์ zip ไว้ที่ C:\\EPITrack (อย่าเปิดจากใน zip โดยตรง และอย่าไว้ใน C:\\Program Files)
  2. ดับเบิลคลิก EPITrack.exe
     ครั้งแรก Windows อาจขึ้น "Windows protected your PC" ให้กด More info แล้วกด Run anyway
     ถ้า Windows Firewall ถาม ให้กด Allow เพื่อให้เครื่องอื่นในวง LAN เข้าใช้ได้
  3. เบราว์เซอร์จะเปิดหน้าตั้งค่า กรอก IP / Port / Username / Password ของเซิร์ฟเวอร์ JHCIS
     โปรแกรมจะสร้างฐานข้อมูล epitrack บนเซิร์ฟเวอร์เดียวกันให้เอง (user ต้องมีสิทธิ์ CREATE)
  4. สร้างบัญชีผู้ดูแลคนแรก แล้วเพิ่มบัญชีเจ้าหน้าที่ในหน้า "ผู้ใช้"

ใช้งาน
  - เครื่องหลัก: เปิดจาก shortcut บน Desktop จะมีหน้าต่างดำเปิดค้างไว้ ปิดหน้าต่างนั้น = หยุดโปรแกรม
  - เครื่องอื่น: เปิดเบราว์เซอร์ไปที่ที่อยู่ "เครื่องอื่นในวง LAN" ที่แสดงในหน้าต่างดำ

ไฟล์ของเครื่องนี้ (อยู่ข้าง EPITrack.exe การอัปเดตไม่แตะไฟล์เหล่านี้)
  config.json     ค่าการเชื่อมต่อ (มีรหัสผ่านฐานข้อมูล อย่าส่งต่อให้ผู้อื่น)
  backups\\       ไฟล์สำรองฐานข้อมูล epitrack (มีข้อมูลคนไข้ เก็บในเครื่องนี้เท่านั้น)
  _previous\\     โปรแกรมเวอร์ชันก่อนหน้า (ใช้ตอนกด "กลับไปเวอร์ชันก่อน")

ต้องการ Windows 10 หรือ 11 แบบ 64 บิต
"""


def make_icon(path: Path) -> None:
    """ไอคอนโปรแกรม: สี่เหลี่ยมมุมมนสีเขียวอมฟ้า + เข็มฉีดยาสีขาว (แบบเดียวกับ favicon)"""
    from PIL import Image, ImageDraw

    size, scale = 256, 8  # วาดบนกริด 32x32 แบบ favicon แล้วขยาย 8 เท่า
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=64, fill=(15, 118, 110, 255))
    width = int(2.4 * scale)
    for (x1, y1), (x2, y2) in [((10, 22), (22, 10)), ((14, 10), (22, 18)), ((19, 7), (25, 13)), ((8, 24), (10, 22))]:
        p1, p2 = (x1 * scale, y1 * scale), (x2 * scale, y2 * scale)
        d.line((p1, p2), fill="white", width=width)
        for x, y in (p1, p2):
            d.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill="white")
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def pyinstaller(*args: str) -> None:
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
                    "--specpath", str(BUILD), *args], check=True, cwd=ROOT)


def build() -> Path:
    """build ทั้งหมด คืนที่อยู่ไฟล์ zip"""
    BUILD.mkdir(exist_ok=True)
    icon = BUILD / "EPITrack.ico"
    make_icon(icon)
    sep = ";"  # ตัวคั่น --add-data บน Windows

    pyinstaller(
        str(ROOT / "launcher.py"), "--name", "EPITrack", "--onedir", "--console",
        "--icon", str(icon), "--distpath", str(DIST), "--workpath", str(BUILD / "work"),
        "--add-data", f"{ROOT / 'templates'}{sep}templates",
        "--add-data", f"{ROOT / 'static'}{sep}static",
        "--add-data", f"{ROOT / 'defaults'}{sep}defaults",
        "--collect-submodules", "uvicorn",
        "--hidden-import", "python_multipart",
        "--exclude-module", "tkinter",
    )
    # ตัวช่วยอัปเดตเป็นไฟล์เดียว เพราะต้องคัดลอกไปรันจาก %TEMP%
    pyinstaller(
        str(ROOT / "updater.py"), "--name", "EPITrack-Updater", "--onefile", "--console",
        "--icon", str(icon), "--distpath", str(BUILD / "updater-dist"),
        "--workpath", str(BUILD / "work-updater"), "--exclude-module", "tkinter",
    )
    shutil.copy2(BUILD / "updater-dist" / "EPITrack-Updater.exe", APP / "EPITrack-Updater.exe")

    (APP / "README.txt").write_text(README, encoding="utf-8-sig")
    (APP / "VERSION.txt").write_text(VERSION, encoding="utf-8")

    zip_path = DIST / f"EPITrack-{VERSION}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in sorted(APP.rglob("*")):
            rel = f.relative_to(APP)
            if rel.parts[0] in PRESERVE or f.is_dir():
                continue
            zf.write(f, Path("EPITrack") / rel)
    return zip_path


def main() -> None:
    zip_path = build()
    size = zip_path.stat().st_size / 1024 / 1024
    print(f"\nเสร็จแล้ว: {APP / 'EPITrack.exe'}")
    print(f"ไฟล์สำหรับแจก: {zip_path} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
