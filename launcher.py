"""ตัวเปิดโปรแกรม EPITrack.exe

- ถ้า EPITrack เปิดอยู่แล้ว: เปิดเบราว์เซอร์ไปที่ตัวเดิม แล้วจบ (ไม่เปิดซ้ำ)
- หาพอร์ตว่างตั้งแต่ 8701 ขึ้นไป แล้วเปิดเซิร์ฟเวอร์ให้เครื่องอื่นในวง LAN เข้าได้ (0.0.0.0)
- เปิดครั้งแรก: สร้าง shortcut บน Desktop
- ปิดหน้าต่างนี้ = หยุดโปรแกรม
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

from paths import DATA_DIR, FROZEN, PORTS, VERSION

HOST = "127.0.0.1"  # ใช้ตรวจว่าเปิดอยู่แล้วหรือยัง
BIND = "0.0.0.0"  # เครื่องอื่นในวง LAN เปิดผ่านเบราว์เซอร์ได้
FIRST_PORT, LAST_PORT = PORTS[0], PORTS[-1]
TITLE = "EPITrack – ปิดหน้าต่างนี้เพื่อหยุดโปรแกรม"


def running_on(port: int) -> bool:
    """พอร์ตนี้มี EPITrack ทำงานอยู่หรือไม่"""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/api/ping", timeout=1) as r:
            return json.load(r).get("app") == "EPITrack"
    except Exception:
        return False


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((BIND, port))
            return True
        except OSError:
            return False


def lan_ips() -> list[str]:
    try:
        infos = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    except OSError:
        return []
    return sorted({i[4][0] for i in infos if not i[4][0].startswith("127.")})


def create_shortcut() -> None:
    """สร้าง shortcut บน Desktop ครั้งแรกที่เปิด (ลบเองแล้วจะไม่สร้างซ้ำ)"""
    marker = DATA_DIR / ".shortcut_created"
    if not FROZEN or marker.exists() or os.environ.get("EPITRACK_NO_SHORTCUT"):
        return
    exe = sys.executable.replace("'", "''")
    workdir = str(DATA_DIR).replace("'", "''")
    script = (
        "$d=[Environment]::GetFolderPath('Desktop');"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $d 'EPITrack.lnk'));"
        f"$s.TargetPath='{exe}';$s.WorkingDirectory='{workdir}';$s.IconLocation='{exe},0';"
        "$s.Description='EPITrack บันทึกและติดตามวัคซีนเด็กจาก JHCIS';$s.Save()"
    )
    try:
        subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                       check=True, capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        marker.write_text("1", encoding="utf-8")
        print("  สร้าง shortcut \"EPITrack\" บน Desktop แล้ว")
    except Exception:
        print("  (สร้าง shortcut บน Desktop ไม่ได้ เปิดจากไฟล์ EPITrack.exe ได้ตามปกติ)")


def open_browser_when_ready(url: str) -> None:
    def wait_and_open():
        for _ in range(100):
            if running_on(int(url.rsplit(":", 1)[1].split("/")[0])):
                break
            time.sleep(0.2)
        if not os.environ.get("EPITRACK_NO_BROWSER"):
            webbrowser.open(url)
    threading.Thread(target=wait_and_open, daemon=True).start()


def main() -> None:
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(TITLE)
        except Exception:
            pass

    for port in range(FIRST_PORT, LAST_PORT + 1):
        if running_on(port):
            print(f"EPITrack เปิดอยู่แล้วที่ http://localhost:{port} กำลังเปิดเบราว์เซอร์...")
            if not os.environ.get("EPITRACK_NO_BROWSER"):
                webbrowser.open(f"http://localhost:{port}/")
            time.sleep(2)
            return

    port = next((p for p in range(FIRST_PORT, LAST_PORT + 1) if port_free(p)), None)
    if port is None:
        print(f"ไม่มีพอร์ตว่างระหว่าง {FIRST_PORT}–{LAST_PORT} ปิดโปรแกรมอื่นแล้วลองใหม่")
        input("กด Enter เพื่อปิด")
        return

    url = f"http://localhost:{port}/"
    print("=" * 56)
    print(f"  EPITrack v{VERSION}")
    print(f"  เปิดใช้งานที่ {url}")
    for ip in lan_ips():
        print(f"  เครื่องอื่นในวง LAN: http://{ip}:{port}/")
    print(f"  ข้อมูลของเครื่องนี้: {DATA_DIR}")
    print("  ปิดหน้าต่างนี้เมื่อเลิกใช้งาน")
    print("=" * 56)
    create_shortcut()
    open_browser_when_ready(url)

    import uvicorn
    import server

    uvicorn.run(server.app, host=BIND, port=port, log_level="warning", access_log=False)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # ให้เห็นข้อผิดพลาดก่อนหน้าต่างปิด
        print(f"\nเกิดข้อผิดพลาด: {exc}")
        input("กด Enter เพื่อปิด")
        raise
