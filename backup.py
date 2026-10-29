"""สำรองฐานข้อมูล epitrack เป็นไฟล์ .sql (ไม่ต้องใช้ mysqldump)

- อัตโนมัติวันละครั้ง: ตอนเปิดโปรแกรม และตรวจซ้ำทุกชั่วโมง ถ้าวันนี้ยังไม่มีไฟล์สำรองก็สร้าง
- เก็บไว้ KEEP ชุดล่าสุด ชุดเก่ากว่านั้นลบทิ้ง
- กู้คืน: สร้างฐานข้อมูลเปล่าแล้วรันไฟล์ .sql (เช่น mysql -u root -p epitrack < ไฟล์.sql)
"""

import threading
import time
from datetime import date, datetime
from pathlib import Path

import db
from paths import DATA_DIR

KEEP = 30
PREFIX = "epitrack-"
CHECK_EVERY = 3600  # วินาที

_lock = threading.Lock()
_state = {"last": None, "error": None}


def backup_dir(config: dict | None = None) -> Path:
    config = config or db.load_config()
    return Path(config.get("backup_dir") or DATA_DIR / "backups")


def _literal(conn, value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (date, datetime)):
        return f"'{value.isoformat(sep=' ') if isinstance(value, datetime) else value.isoformat()}'"
    return conn.escape(str(value))


def run(config: dict | None = None) -> Path:
    """สร้างไฟล์สำรองใหม่ทันที คืนที่อยู่ไฟล์"""
    config = config or db.load_config()
    folder = backup_dir(config)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{PREFIX}{datetime.now():%Y%m%d-%H%M%S}.sql"
    tmp = path.with_suffix(".tmp")
    with _lock, db.app_conn(config) as conn, conn.cursor() as cur, tmp.open("w", encoding="utf-8") as out:
        cur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur.execute("START TRANSACTION WITH CONSISTENT SNAPSHOT")
        out.write(f"-- EPITrack backup {datetime.now():%Y-%m-%d %H:%M:%S} database {config['app_database']}\n")
        out.write("SET NAMES utf8mb4;\nSET FOREIGN_KEY_CHECKS = 0;\n\n")
        cur.execute("SHOW TABLES")
        tables = [next(iter(r.values())) for r in cur.fetchall()]
        for table in tables:
            cur.execute(f"SHOW CREATE TABLE `{table}`")
            create = cur.fetchone()["Create Table"]
            out.write(f"DROP TABLE IF EXISTS `{table}`;\n{create};\n")
            cur.execute(f"SELECT * FROM `{table}`")
            rows = cur.fetchall()
            for i in range(0, len(rows), 200):
                part = rows[i:i + 200]
                cols = ", ".join(f"`{c}`" for c in part[0])
                values = ",\n".join("(" + ", ".join(_literal(conn, v) for v in r.values()) + ")" for r in part)
                out.write(f"INSERT INTO `{table}` ({cols}) VALUES\n{values};\n")
            out.write("\n")
        conn.rollback()
    tmp.replace(path)
    _prune(folder)
    _state.update(last=datetime.now(), error=None)
    return path


def _prune(folder: Path) -> None:
    for old in list_backups(folder)[KEEP:]:
        try:
            old.unlink()
        except OSError:
            pass


def list_backups(folder: Path | None = None) -> list[Path]:
    folder = folder or backup_dir()
    if not folder.is_dir():
        return []
    return sorted(folder.glob(f"{PREFIX}*.sql"), reverse=True)


def has_today(folder: Path | None = None) -> bool:
    today = f"{PREFIX}{date.today():%Y%m%d}-"
    return any(p.name.startswith(today) for p in list_backups(folder))


def status() -> dict:
    return {"last": _state["last"], "error": _state["error"]}


def _loop() -> None:
    while True:
        if db.is_configured():
            try:
                if not has_today():
                    run()
            except Exception as exc:  # สำรองไม่ได้ไม่ให้โปรแกรมล้ม แสดงในหน้าตั้งค่าแทน
                _state["error"] = f"{datetime.now():%H:%M} สำรองไม่สำเร็จ: {exc}"
        time.sleep(CHECK_EVERY)


def start_daily() -> None:
    threading.Thread(target=_loop, daemon=True, name="daily-backup").start()
