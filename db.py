"""การเชื่อมต่อฐานข้อมูล 2 ชุดบนเซิร์ฟเวอร์เดียวกัน (docs/adr/0002)

jhcisdb   ข้อมูลคนและประวัติวัคซีนจาก JHCIS อ่านอย่างเดียว (jhcis_query ยอมเฉพาะ SELECT)
epitrack  ข้อมูลของแอป อ่านเขียนได้ (app_conn)

ค่าการเชื่อมต่อเก็บใน config.json (ข้างโปรแกรม) แก้ไขได้จากหน้า "ตั้งค่า"
"""

import json
import re

import pymysql

from paths import DATA_DIR

CONFIG_PATH = DATA_DIR / "config.json"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 3333,
    "user": "root",
    "password": "",
    "database": "jhcisdb",
    "charset": "utf8",
    "app_database": "epitrack",
    "backup_dir": "",  # ว่าง = โฟลเดอร์ backups ข้างโปรแกรม
    "update_manifest_id": "",  # รหัสไฟล์ Google Drive ว่าง = ใช้ค่าที่ฝังในโปรแกรม (paths.py)
    "update_zip_id": "",
}

CHARSETS = ["utf8", "tis620"]
CONNECT_TIMEOUT = 5  # วินาที
_DB_NAME = re.compile(r"^[A-Za-z0-9_]{1,64}$")


def is_configured() -> bool:
    return CONFIG_PATH.exists()


def load_config() -> dict:
    """อ่านค่าจาก config.json ถ้ายังไม่มีไฟล์หรือขาดบางค่า ใช้ค่าเริ่มต้นแทน"""
    config = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        try:
            config.update(json.loads(CONFIG_PATH.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            pass
    config["port"] = int(config["port"])
    return config


def save_config(config: dict) -> None:
    data = {key: config[key] for key in DEFAULTS}
    data["port"] = int(data["port"])
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def valid_db_name(name: str) -> bool:
    return bool(_DB_NAME.match(name or ""))


def _connect(config: dict, database: str | None, charset: str, autocommit: bool):
    return pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["user"],
        password=config["password"],
        database=database,
        charset=charset,
        connect_timeout=CONNECT_TIMEOUT,
        read_timeout=60,
        autocommit=autocommit,
        cursorclass=pymysql.cursors.DictCursor,
    )


def jhcis_query(sql: str, params=None, config: dict | None = None) -> list[dict]:
    """รัน SELECT บน jhcisdb แล้วคืนผลเป็น list ของ dict (อนุญาตเฉพาะคำสั่งอ่านข้อมูล)"""
    first_word = sql.lstrip().split(None, 1)[0].upper() if sql.strip() else ""
    if first_word not in ("SELECT", "SHOW", "DESCRIBE", "DESC", "EXPLAIN"):
        raise ValueError("อนุญาตเฉพาะคำสั่งอ่านข้อมูล (SELECT) บนฐานข้อมูล JHCIS")
    config = config or load_config()
    with _connect(config, config["database"], config["charset"], True) as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def app_conn(config: dict | None = None):
    """การเชื่อมต่อ epitrack (ไม่ autocommit: ผู้เรียกต้อง commit เอง ใช้ with app_conn() as conn:)"""
    config = config or load_config()
    return _connect(config, config["app_database"], "utf8mb4", False)


def server_conn(config: dict | None = None):
    """การเชื่อมต่อแบบยังไม่เลือกฐานข้อมูล ใช้สร้าง epitrack ครั้งแรก"""
    config = config or load_config()
    return _connect(config, None, "utf8mb4", True)


def explain_error(exc: Exception, config: dict) -> str:
    """แปลงข้อผิดพลาดของ MySQL เป็นข้อความภาษาไทยที่บอกว่าต้องแก้อะไร"""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    target = f"{config['host']}:{config['port']}"
    if code == 1045:
        return f"username หรือ password ไม่ถูกต้อง (ผู้ใช้ {config['user']})"
    if code == 1049:
        return f"ไม่พบฐานข้อมูลชื่อ {config['database']} บนเซิร์ฟเวอร์"
    if code in (1044, 1142):
        return f"ผู้ใช้ {config['user']} ไม่มีสิทธิ์สร้างหรือเขียนฐานข้อมูล {config['app_database']}"
    if code in (2003, 2005):
        return f"ติดต่อเซิร์ฟเวอร์ {target} ไม่ได้ ตรวจสอบ IP, พอร์ต หรือเครื่องเซิร์ฟเวอร์ปิดอยู่"
    if code == 2013:
        return f"การเชื่อมต่อกับ {target} ถูกตัดระหว่างทาง ลองใหม่อีกครั้ง"
    if code == 1130:
        return "เซิร์ฟเวอร์ไม่อนุญาตให้เครื่องนี้เชื่อมต่อ (ต้องให้สิทธิ์ user จาก IP เครื่องนี้)"
    if isinstance(exc, LookupError) or code == 2019:
        return f"ไม่รองรับ charset {config['charset']}"
    return f"เชื่อมต่อไม่สำเร็จ: {exc}"


def test_connection(config: dict | None = None) -> tuple[bool, str]:
    """ลองเชื่อมต่อ jhcisdb คืนค่า (สำเร็จหรือไม่, ข้อความภาษาไทย)"""
    config = config or load_config()
    try:
        rows = jhcis_query("SELECT VERSION() AS version, (SELECT COUNT(*) FROM person) AS persons", config=config)
        return True, f"เชื่อมต่อสำเร็จ (MySQL {rows[0]['version']}, คนใน JHCIS {rows[0]['persons']:,} คน)"
    except Exception as exc:
        return False, explain_error(exc, config)
