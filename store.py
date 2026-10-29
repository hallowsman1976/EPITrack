"""ฐานข้อมูลของแอป (epitrack): รายการฉีด นัด ผู้ใช้ mapping ตารางวัคซีน และประวัติการแก้ไข

ทุกการเพิ่ม แก้ หรือซ่อนข้อมูล บันทึกลง audit_log ใน transaction เดียวกัน
การลบรายการฉีดและนัดเป็นการซ่อน (deleted_at) ไม่ลบจริง
MySQL 5.6: ไม่มีชนิด JSON จึงเก็บเป็น MEDIUMTEXT (docs/adr/0002)
"""

import hashlib
import hmac
import json
import secrets
from contextlib import contextmanager
from datetime import date, datetime

import db
import schedule

PLACES = {
    "here": "ที่นี่",
    "hospital": "รพ.",
    "private": "เอกชน",
    "unknown": "ไม่ทราบ",
}
ROLES = {"admin": "ผู้ดูแล", "staff": "เจ้าหน้าที่"}

VACCINATION_FIELDS = ("vaccine_code", "date_given", "lot_no", "expire_date", "manufacturer",
                      "place", "place_note", "giver", "note")

# แต่ละรายการคือคำสั่งของ schema เวอร์ชันนั้น เพิ่มต่อท้ายเท่านั้น ห้ามแก้ของเดิม
MIGRATIONS: list[list[str]] = [
    [
        """CREATE TABLE settings (
            k VARCHAR(64) NOT NULL PRIMARY KEY,
            v MEDIUMTEXT,
            updated_at DATETIME,
            updated_by INT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE users (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(64) NOT NULL UNIQUE,
            display_name VARCHAR(191) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role VARCHAR(16) NOT NULL,
            active TINYINT NOT NULL DEFAULT 1,
            created_at DATETIME NOT NULL
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE vaccine_map (
            code VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
            series VARCHAR(32) NOT NULL,
            product VARCHAR(16) NULL,
            PRIMARY KEY (code, series)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE vaccinations (
            id INT AUTO_INCREMENT PRIMARY KEY,
            pcucodeperson CHAR(5) NOT NULL,
            pid INT NOT NULL,
            vaccine_code VARCHAR(32) COLLATE utf8mb4_bin NOT NULL,
            date_given DATE NOT NULL,
            lot_no VARCHAR(64) NOT NULL DEFAULT '',
            expire_date DATE NULL,
            manufacturer VARCHAR(191) NOT NULL DEFAULT '',
            place VARCHAR(16) NOT NULL,
            place_note VARCHAR(191) NOT NULL DEFAULT '',
            giver VARCHAR(191) NOT NULL DEFAULT '',
            note TEXT,
            created_at DATETIME NOT NULL,
            created_by INT NOT NULL,
            updated_at DATETIME NULL,
            updated_by INT NULL,
            deleted_at DATETIME NULL,
            deleted_by INT NULL,
            delete_reason VARCHAR(255) NULL,
            KEY person (pcucodeperson, pid),
            KEY given (date_given)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE appointments (
            id INT AUTO_INCREMENT PRIMARY KEY,
            pcucodeperson CHAR(5) NOT NULL,
            pid INT NOT NULL,
            date_appoint DATE NOT NULL,
            vaccines VARCHAR(255) NOT NULL DEFAULT '',
            note VARCHAR(255) NOT NULL DEFAULT '',
            created_at DATETIME NOT NULL,
            created_by INT NOT NULL,
            deleted_at DATETIME NULL,
            deleted_by INT NULL,
            delete_reason VARCHAR(255) NULL,
            KEY person (pcucodeperson, pid),
            KEY appoint (date_appoint)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
        """CREATE TABLE audit_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            at DATETIME NOT NULL,
            user_id INT NULL,
            entity VARCHAR(32) NOT NULL,
            entity_id VARCHAR(64) NOT NULL,
            action VARCHAR(16) NOT NULL,
            reason VARCHAR(255) NULL,
            before_data MEDIUMTEXT,
            after_data MEDIUMTEXT,
            KEY entity (entity, entity_id),
            KEY at (at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""",
    ],
]


class NotFound(Exception):
    pass


# ---------- การเชื่อมต่อและ schema ----------
@contextmanager
def tx():
    """เปิด transaction: สำเร็จ = commit, มีข้อผิดพลาด = rollback"""
    conn = db.app_conn()
    try:
        with conn.cursor() as cur:
            yield cur
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def query(sql: str, params=None) -> list[dict]:
    with tx() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def ensure_schema(config: dict | None = None) -> int:
    """สร้างฐานข้อมูล epitrack และอัปเกรดตารางให้เป็นเวอร์ชันล่าสุด คืนเลขเวอร์ชัน"""
    config = config or db.load_config()
    name = config["app_database"]
    if not db.valid_db_name(name):
        raise ValueError(f"ชื่อฐานข้อมูล \"{name}\" ใช้ได้เฉพาะ A-Z a-z 0-9 _")
    with db.server_conn(config) as conn, conn.cursor() as cur:
        cur.execute(f"CREATE DATABASE IF NOT EXISTS `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci")
    conn = db.app_conn(config)
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS schema_version (version INT NOT NULL) ENGINE=InnoDB")
            cur.execute("SELECT version FROM schema_version")
            row = cur.fetchone()
            if row is None:
                cur.execute("INSERT INTO schema_version VALUES (0)")
                current = 0
            else:
                current = row["version"]
            if current > len(MIGRATIONS):
                raise RuntimeError(f"ฐานข้อมูล {name} เป็นของโปรแกรมเวอร์ชันที่ใหม่กว่า กรุณาอัปเดตโปรแกรม")
            for version in range(current, len(MIGRATIONS)):
                for sql in MIGRATIONS[version]:
                    cur.execute(sql)
                cur.execute("UPDATE schema_version SET version = %s", (version + 1,))
                if version == 0:
                    _seed(cur)
                conn.commit()
        return len(MIGRATIONS)
    finally:
        conn.close()


def _seed(cur) -> None:
    cur.executemany("INSERT INTO vaccine_map (code, series, product) VALUES (%s, %s, %s)",
                    [(r["code"], r["series"], r.get("product")) for r in schedule.default_vaccine_map()])


def _audit(cur, user_id, entity: str, entity_id, action: str, before=None, after=None, reason=None) -> None:
    dump = lambda d: json.dumps(d, ensure_ascii=False, default=str) if d is not None else None  # noqa: E731
    cur.execute(
        "INSERT INTO audit_log (at, user_id, entity, entity_id, action, reason, before_data, after_data) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (datetime.now(), user_id, entity, str(entity_id), action, reason, dump(before), dump(after)),
    )


def audit_for(entity: str, entity_id) -> list[dict]:
    rows = query(
        "SELECT a.*, u.display_name FROM audit_log a LEFT JOIN users u ON u.id = a.user_id "
        "WHERE a.entity = %s AND a.entity_id = %s ORDER BY a.id DESC",
        (entity, str(entity_id)),
    )
    for r in rows:
        r["before"] = json.loads(r["before_data"]) if r["before_data"] else None
        r["after"] = json.loads(r["after_data"]) if r["after_data"] else None
    return rows


def recent_audit(limit: int = 200) -> list[dict]:
    return query(
        "SELECT a.id, a.at, a.entity, a.entity_id, a.action, a.reason, u.display_name "
        "FROM audit_log a LEFT JOIN users u ON u.id = a.user_id ORDER BY a.id DESC LIMIT %s",
        (limit,),
    )


# ---------- ค่าตั้งค่าในฐานข้อมูล ----------
def get_setting(key: str) -> str | None:
    rows = query("SELECT v FROM settings WHERE k = %s", (key,))
    return rows[0]["v"] if rows else None


def set_setting(cur, key: str, value: str, user_id: int | None) -> None:
    cur.execute(
        "INSERT INTO settings (k, v, updated_at, updated_by) VALUES (%s, %s, %s, %s) "
        "ON DUPLICATE KEY UPDATE v = VALUES(v), updated_at = VALUES(updated_at), updated_by = VALUES(updated_by)",
        (key, value, datetime.now(), user_id),
    )


# ---------- ตารางวัคซีน ----------
def schedule_json() -> tuple[str, bool]:
    """(ข้อความ JSON ของตารางที่ใช้อยู่, เป็นค่าเริ่มต้นหรือไม่)"""
    saved = get_setting("schedule_json")
    return (saved, False) if saved else (schedule.default_json(), True)


def rules() -> schedule.Rules:
    return schedule.parse(json.loads(schedule_json()[0]))


def save_schedule(text: str | None, user_id: int, reason: str) -> None:
    """บันทึกตารางวัคซีน (None = กลับไปใช้ค่าเริ่มต้น) ตรวจรูปแบบก่อนบันทึก"""
    if text is not None:
        schedule.parse(json.loads(text))
    before, _ = schedule_json()
    with tx() as cur:
        if text is None:
            cur.execute("DELETE FROM settings WHERE k = 'schedule_json'")
        else:
            set_setting(cur, "schedule_json", text, user_id)
        _audit(cur, user_id, "schedule", "-", "reset" if text is None else "update",
               before=before, after=text, reason=reason)


# ---------- mapping รหัสวัคซีน ----------
def vaccine_map() -> dict[str, list[tuple[str, str | None]]]:
    """รหัสวัคซีน -> [(ชุดวัคซีน, product)] ชุดวัคซีนว่าง = ตั้งใจไม่นับ"""
    result: dict[str, list[tuple[str, str | None]]] = {}
    for r in query("SELECT code, series, product FROM vaccine_map"):
        result.setdefault(r["code"], []).append((r["series"], r["product"] or None))
    return result


def map_rows() -> list[dict]:
    return query("SELECT code, series, product FROM vaccine_map ORDER BY series = '', series, code")


def save_map_row(code: str, series: str, product: str | None, user_id: int) -> None:
    with tx() as cur:
        cur.execute("SELECT code, series, product FROM vaccine_map WHERE code = %s AND series = %s", (code, series))
        before = cur.fetchone()
        cur.execute(
            "INSERT INTO vaccine_map (code, series, product) VALUES (%s, %s, %s) "
            "ON DUPLICATE KEY UPDATE product = VALUES(product)",
            (code, series, product or None),
        )
        _audit(cur, user_id, "vaccine_map", f"{code}:{series}", "update" if before else "create",
               before=before, after={"code": code, "series": series, "product": product or None})


def delete_map_row(code: str, series: str, user_id: int) -> None:
    with tx() as cur:
        cur.execute("SELECT code, series, product FROM vaccine_map WHERE code = %s AND series = %s", (code, series))
        before = cur.fetchone()
        if not before:
            raise NotFound("ไม่พบแถวนี้ใน mapping")
        cur.execute("DELETE FROM vaccine_map WHERE code = %s AND series = %s", (code, series))
        _audit(cur, user_id, "vaccine_map", f"{code}:{series}", "delete", before=before)


# ---------- ผู้ใช้ ----------
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 200_000).hex()
    return f"pbkdf2_sha256$200000${salt}${digest}"


def check_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    except ValueError:
        return False
    return hmac.compare_digest(test, digest)


def user_count() -> int:
    return query("SELECT COUNT(*) AS n FROM users")[0]["n"]


def get_user(user_id: int) -> dict | None:
    rows = query("SELECT id, username, display_name, role, active FROM users WHERE id = %s", (user_id,))
    return rows[0] if rows else None


def authenticate(username: str, password: str) -> dict | None:
    rows = query("SELECT * FROM users WHERE username = %s AND active = 1", (username.strip(),))
    if rows and check_password(password, rows[0]["password_hash"]):
        return get_user(rows[0]["id"])
    return None


def list_users() -> list[dict]:
    return query("SELECT id, username, display_name, role, active, created_at FROM users ORDER BY active DESC, username")


def create_user(username: str, display_name: str, password: str, role: str, by: int | None) -> int:
    username, display_name = username.strip(), display_name.strip()
    if not username or not display_name:
        raise ValueError("กรอกชื่อผู้ใช้และชื่อที่แสดงให้ครบ")
    if len(password) < 6:
        raise ValueError("รหัสผ่านต้องยาวอย่างน้อย 6 ตัวอักษร")
    if role not in ROLES:
        raise ValueError("บทบาทไม่ถูกต้อง")
    with tx() as cur:
        cur.execute("SELECT id FROM users WHERE username = %s", (username,))
        if cur.fetchone():
            raise ValueError(f"มีผู้ใช้ชื่อ {username} อยู่แล้ว")
        cur.execute(
            "INSERT INTO users (username, display_name, password_hash, role, active, created_at) "
            "VALUES (%s, %s, %s, %s, 1, %s)",
            (username, display_name, hash_password(password), role, datetime.now()),
        )
        user_id = cur.lastrowid
        _audit(cur, by or user_id, "user", user_id, "create",
               after={"username": username, "display_name": display_name, "role": role})
    return user_id


def update_user(user_id: int, display_name: str, role: str, active: bool, password: str, by: int) -> None:
    if role not in ROLES:
        raise ValueError("บทบาทไม่ถูกต้อง")
    if password and len(password) < 6:
        raise ValueError("รหัสผ่านต้องยาวอย่างน้อย 6 ตัวอักษร")
    with tx() as cur:
        cur.execute("SELECT id, username, display_name, role, active FROM users WHERE id = %s", (user_id,))
        before = cur.fetchone()
        if not before:
            raise NotFound("ไม่พบผู้ใช้")
        if before["role"] == "admin" and (role != "admin" or not active):
            cur.execute("SELECT COUNT(*) AS n FROM users WHERE role = 'admin' AND active = 1 AND id <> %s", (user_id,))
            if cur.fetchone()["n"] == 0:
                raise ValueError("ต้องมีผู้ดูแลที่ใช้งานได้อย่างน้อย 1 คน")
        cur.execute("UPDATE users SET display_name = %s, role = %s, active = %s WHERE id = %s",
                    (display_name.strip() or before["display_name"], role, int(active), user_id))
        if password:
            cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(password), user_id))
        after = {"display_name": display_name.strip() or before["display_name"], "role": role,
                 "active": int(active), "password_changed": bool(password)}
        _audit(cur, by, "user", user_id, "update", before=before, after=after)


def change_own_password(user_id: int, old: str, new: str) -> None:
    rows = query("SELECT password_hash FROM users WHERE id = %s", (user_id,))
    if not rows or not check_password(old, rows[0]["password_hash"]):
        raise ValueError("รหัสผ่านเดิมไม่ถูกต้อง")
    if len(new) < 6:
        raise ValueError("รหัสผ่านใหม่ต้องยาวอย่างน้อย 6 ตัวอักษร")
    with tx() as cur:
        cur.execute("UPDATE users SET password_hash = %s WHERE id = %s", (hash_password(new), user_id))
        _audit(cur, user_id, "user", user_id, "password")


# ---------- รายการฉีด ----------
def _clean_vaccination(data: dict) -> dict:
    row = {k: data.get(k) for k in VACCINATION_FIELDS}
    for k in ("lot_no", "manufacturer", "place_note", "giver", "note", "vaccine_code"):
        row[k] = (row[k] or "").strip()
    if not row["vaccine_code"]:
        raise ValueError("เลือกวัคซีนก่อนบันทึก")
    if not isinstance(row["date_given"], date):
        raise ValueError("ใส่วันที่ฉีด")
    if row["date_given"] > date.today():
        raise ValueError("วันที่ฉีดเป็นวันในอนาคตไม่ได้")
    if row["place"] not in PLACES:
        raise ValueError("เลือกสถานที่ฉีด")
    if row["place"] == "here" and not row["lot_no"]:
        raise ValueError("การฉีดที่นี่ต้องใส่เลข lot")
    if row["expire_date"] and row["expire_date"] < row["date_given"]:
        raise ValueError("วัคซีนหมดอายุก่อนวันที่ฉีด ตรวจสอบวันหมดอายุอีกครั้ง")
    return row


def add_vaccination(pcucodeperson: str, pid: int, data: dict, user_id: int) -> int:
    row = _clean_vaccination(data)
    with tx() as cur:
        cur.execute(
            "SELECT id FROM vaccinations WHERE pcucodeperson = %s AND pid = %s AND vaccine_code = %s "
            "AND date_given = %s AND deleted_at IS NULL",
            (pcucodeperson, pid, row["vaccine_code"], row["date_given"]),
        )
        if cur.fetchone():
            raise ValueError("มีรายการฉีดวัคซีนนี้ในวันเดียวกันอยู่แล้ว")
        cols = ", ".join(VACCINATION_FIELDS)
        cur.execute(
            f"INSERT INTO vaccinations (pcucodeperson, pid, {cols}, created_at, created_by) "
            f"VALUES (%s, %s, {', '.join(['%s'] * len(VACCINATION_FIELDS))}, %s, %s)",
            (pcucodeperson, pid, *row.values(), datetime.now(), user_id),
        )
        new_id = cur.lastrowid
        _audit(cur, user_id, "vaccination", new_id, "create",
               after={"pcucodeperson": pcucodeperson, "pid": pid, **row})
    return new_id


def get_vaccination(record_id: int) -> dict:
    rows = query("SELECT * FROM vaccinations WHERE id = %s", (record_id,))
    if not rows:
        raise NotFound("ไม่พบรายการฉีด")
    return rows[0]


def update_vaccination(record_id: int, data: dict, reason: str, user_id: int) -> None:
    if not reason.strip():
        raise ValueError("ใส่เหตุผลของการแก้ไข")
    row = _clean_vaccination(data)
    with tx() as cur:
        cur.execute("SELECT * FROM vaccinations WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (record_id,))
        before = cur.fetchone()
        if not before:
            raise NotFound("ไม่พบรายการฉีด หรือถูกซ่อนไปแล้ว")
        sets = ", ".join(f"{k} = %s" for k in VACCINATION_FIELDS)
        cur.execute(f"UPDATE vaccinations SET {sets}, updated_at = %s, updated_by = %s WHERE id = %s",
                    (*row.values(), datetime.now(), user_id, record_id))
        _audit(cur, user_id, "vaccination", record_id, "update",
               before={k: before[k] for k in VACCINATION_FIELDS}, after=row, reason=reason.strip())


def hide_vaccination(record_id: int, reason: str, user_id: int) -> dict:
    if not reason.strip():
        raise ValueError("ใส่เหตุผลของการลบ")
    with tx() as cur:
        cur.execute("SELECT * FROM vaccinations WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (record_id,))
        before = cur.fetchone()
        if not before:
            raise NotFound("ไม่พบรายการฉีด หรือถูกซ่อนไปแล้ว")
        cur.execute("UPDATE vaccinations SET deleted_at = %s, deleted_by = %s, delete_reason = %s WHERE id = %s",
                    (datetime.now(), user_id, reason.strip(), record_id))
        _audit(cur, user_id, "vaccination", record_id, "delete",
               before={k: before[k] for k in VACCINATION_FIELDS}, reason=reason.strip())
    return before


def vaccinations_for(pcucodeperson: str, pid: int, include_deleted: bool = False) -> list[dict]:
    hidden = "" if include_deleted else " AND v.deleted_at IS NULL"
    return query(
        "SELECT v.*, c.display_name AS created_by_name FROM vaccinations v "
        "LEFT JOIN users c ON c.id = v.created_by "
        f"WHERE v.pcucodeperson = %s AND v.pid = %s{hidden} ORDER BY v.date_given, v.id",
        (pcucodeperson, pid),
    )


def vaccinations_for_many(keys: list[tuple[str, int]]) -> list[dict]:
    if not keys:
        return []
    out = []
    for i in range(0, len(keys), 500):
        part = keys[i:i + 500]
        cond = " OR ".join(["(pcucodeperson = %s AND pid = %s)"] * len(part))
        out += query(f"SELECT * FROM vaccinations WHERE deleted_at IS NULL AND ({cond})",
                     [v for k in part for v in k])
    return out


def here_vaccinations(start: date, end: date) -> list[dict]:
    """รายการที่ฉีดที่นี่ในช่วงวันที่ (ใช้หาที่ยังไม่ลง JHCIS)"""
    return query(
        "SELECT v.*, c.display_name AS created_by_name FROM vaccinations v "
        "LEFT JOIN users c ON c.id = v.created_by "
        "WHERE v.place = 'here' AND v.deleted_at IS NULL AND v.date_given BETWEEN %s AND %s "
        "ORDER BY v.date_given, v.id",
        (start, end),
    )


def recent_lots(vaccine_code: str, limit: int = 5) -> list[dict]:
    """lot ที่ใช้ล่าสุดของวัคซีนนี้ (ช่วยกรอกเร็ว)"""
    return query(
        "SELECT lot_no, MAX(expire_date) AS expire_date, MAX(manufacturer) AS manufacturer, MAX(date_given) AS last_used "
        "FROM vaccinations WHERE vaccine_code = %s AND place = 'here' AND deleted_at IS NULL AND lot_no <> '' "
        "GROUP BY lot_no ORDER BY last_used DESC LIMIT %s",
        (vaccine_code, limit),
    )


# ---------- นัด ----------
def add_appointment(pcucodeperson: str, pid: int, when: date, vaccines: str, note: str, user_id: int) -> int:
    if when < date.today():
        raise ValueError("วันนัดเป็นวันที่ผ่านมาแล้วไม่ได้")
    with tx() as cur:
        cur.execute(
            "INSERT INTO appointments (pcucodeperson, pid, date_appoint, vaccines, note, created_at, created_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (pcucodeperson, pid, when, vaccines.strip()[:255], note.strip()[:255], datetime.now(), user_id),
        )
        new_id = cur.lastrowid
        _audit(cur, user_id, "appointment", new_id, "create",
               after={"pcucodeperson": pcucodeperson, "pid": pid, "date_appoint": when,
                      "vaccines": vaccines, "note": note})
    return new_id


def cancel_appointment(appointment_id: int, reason: str, user_id: int) -> dict:
    with tx() as cur:
        cur.execute("SELECT * FROM appointments WHERE id = %s AND deleted_at IS NULL FOR UPDATE", (appointment_id,))
        before = cur.fetchone()
        if not before:
            raise NotFound("ไม่พบนัด หรือถูกยกเลิกไปแล้ว")
        cur.execute("UPDATE appointments SET deleted_at = %s, deleted_by = %s, delete_reason = %s WHERE id = %s",
                    (datetime.now(), user_id, reason.strip() or "ยกเลิกนัด", appointment_id))
        _audit(cur, user_id, "appointment", appointment_id, "delete", before=before, reason=reason.strip())
    return before


def get_appointment(appointment_id: int) -> dict:
    rows = query("SELECT * FROM appointments WHERE id = %s", (appointment_id,))
    if not rows:
        raise NotFound("ไม่พบนัด")
    return rows[0]


def appointments_for(pcucodeperson: str, pid: int) -> list[dict]:
    return query(
        "SELECT a.*, u.display_name AS created_by_name FROM appointments a LEFT JOIN users u ON u.id = a.created_by "
        "WHERE a.pcucodeperson = %s AND a.pid = %s AND a.deleted_at IS NULL ORDER BY a.date_appoint DESC",
        (pcucodeperson, pid),
    )


def next_appointments(keys: list[tuple[str, int]], today: date) -> dict[tuple[str, int], date]:
    """นัดครั้งถัดไป (วันนี้ขึ้นไป) ของแต่ละคน"""
    rows = query("SELECT pcucodeperson, pid, MIN(date_appoint) AS d FROM appointments "
                 "WHERE deleted_at IS NULL AND date_appoint >= %s GROUP BY pcucodeperson, pid", (today,))
    wanted = set(keys)
    return {(r["pcucodeperson"], r["pid"]): r["d"] for r in rows if (r["pcucodeperson"], r["pid"]) in wanted}
