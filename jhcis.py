"""คำสั่ง SQL บนฐานข้อมูล JHCIS (อ่านอย่างเดียวผ่าน db.jhcis_query)

ตารางที่ใช้:
- person -> house -> village   คน วันเกิด เพศ ประเภทการอยู่อาศัย (typelive) การจำหน่าย (dischargetype)
- visitepi                     ประวัติการฉีดวัคซีน (hosservice = หน่วยที่ฉีด)
- visitepiappoint              นัดฉีดวัคซีนใน JHCIS (แสดงประกอบเท่านั้น)
- cdrug (drugtype 05)          รหัสและชื่อวัคซีน
- chospital                    ชื่อหน่วยบริการ
"""

from datetime import date

from db import jhcis_query as q

IN_AREA = ("1", "3")  # typelive: อยู่ในเขตรับผิดชอบ
NOT_DISCHARGED = "9"

_PERSON_COLS = """
    p.pcucodeperson, p.pid, p.idcard, p.birth, p.sex, p.typelive, p.dischargetype,
    TRIM(CONCAT(COALESCE(t.titlename, ''), p.fname, ' ', p.lname)) AS name,
    p.mother, COALESCE(NULLIF(p.mobile, ''), p.telephoneperson) AS phone,
    h.hno, v.villcode, v.villno, v.villname
"""
_PERSON_JOIN = """
    FROM person p
    LEFT JOIN house h ON h.pcucode = p.pcucodeperson AND h.hcode = p.hcode
    LEFT JOIN village v ON v.villcode = h.villcode
    LEFT JOIN ctitle t ON t.titlecode = p.prename
"""


def in_area(person: dict) -> bool:
    return person.get("typelive") in IN_AREA and person.get("dischargetype") == NOT_DISCHARGED


def villages() -> list[dict]:
    return q("SELECT villcode, villno, villname FROM village WHERE villno > 0 ORDER BY villno")


def unit_name(pcucode: str) -> str:
    rows = q("SELECT hosname FROM chospital WHERE hoscode = %s", (pcucode,))
    return rows[0]["hosname"] if rows else pcucode


def hospital_names(codes) -> dict[str, str]:
    codes = sorted({c for c in codes if c})
    if not codes:
        return {}
    rows = q(f"SELECT hoscode, hosname FROM chospital WHERE hoscode IN ({', '.join(['%s'] * len(codes))})", codes)
    return {r["hoscode"]: r["hosname"] for r in rows}


def vaccines() -> list[dict]:
    """รหัสวัคซีนทั้งหมดใน cdrug"""
    return q("SELECT drugcode AS code, drugname AS name FROM cdrug WHERE drugtype = '05' ORDER BY drugcode")


def used_codes(since_birth: date) -> list[dict]:
    """รหัสวัคซีนที่เคยฉีดให้คนที่เกิดตั้งแต่วันที่นี้ พร้อมจำนวนครั้ง (ใช้หารหัสที่ยังไม่ได้ map)"""
    return q(
        """
        SELECT e.vaccinecode AS code, COALESCE(d.drugname, e.vaccinecode) AS name, COUNT(*) AS n,
               MAX(e.dateepi) AS last_date
        FROM visitepi e
        JOIN person p ON p.pcucodeperson = e.pcucodeperson AND p.pid = e.pid
        LEFT JOIN cdrug d ON d.drugcode = e.vaccinecode
        WHERE p.birth >= %s AND e.vaccinecode <> ''
        GROUP BY e.vaccinecode, d.drugname
        ORDER BY n DESC
        """,
        (since_birth,),
    )


def search(text: str, limit: int = 50) -> list[dict]:
    """ค้นหาคนด้วยเลขบัตรประชาชน, HN (pid) หรือชื่อ/นามสกุล"""
    text = text.strip()
    if not text:
        return []
    if text.isdigit() and len(text) == 13:
        where, params = "p.idcard = %s", [text]
    elif text.isdigit():
        where, params = "p.pid = %s", [int(text)]
    else:
        words = text.split()
        where = " AND ".join(["(p.fname LIKE %s OR p.lname LIKE %s)"] * len(words))
        params = [v for w in words for v in (f"%{w}%", f"{w}%")]
    return q(
        f"SELECT {_PERSON_COLS} {_PERSON_JOIN} WHERE {where} "
        "ORDER BY p.dischargetype = '9' DESC, p.birth DESC LIMIT %s",
        [*params, limit],
    )


def person(pcucodeperson: str, pid: int) -> dict | None:
    rows = q(f"SELECT {_PERSON_COLS} {_PERSON_JOIN} WHERE p.pcucodeperson = %s AND p.pid = %s", (pcucodeperson, pid))
    return rows[0] if rows else None


def persons(keys: list[tuple[str, int]]) -> dict[tuple[str, int], dict]:
    out = {}
    for part in _chunks(keys):
        cond = " OR ".join(["(p.pcucodeperson = %s AND p.pid = %s)"] * len(part))
        for r in q(f"SELECT {_PERSON_COLS} {_PERSON_JOIN} WHERE {cond}", [v for k in part for v in k]):
            out[(r["pcucodeperson"], r["pid"])] = r
    return out


def children(born_from: date, born_to: date, villcodes: list[str] | None = None) -> list[dict]:
    """เด็กในความรับผิดชอบที่เกิดในช่วงวันที่นี้"""
    vsql = ""
    params: list = [born_from, born_to, *IN_AREA, NOT_DISCHARGED]
    if villcodes:
        vsql = f" AND v.villcode IN ({', '.join(['%s'] * len(villcodes))})"
        params += villcodes
    return q(
        f"""
        SELECT {_PERSON_COLS} {_PERSON_JOIN}
        WHERE p.birth BETWEEN %s AND %s
          AND p.typelive IN (%s, %s) AND p.dischargetype = %s {vsql}
        ORDER BY v.villno, h.hno + 0, p.birth
        """,
        params,
    )


def _chunks(keys: list[tuple[str, int]], size: int = 500):
    keys = list(dict.fromkeys(keys))
    for i in range(0, len(keys), size):
        yield keys[i:i + size]


def epi_history(keys: list[tuple[str, int]]) -> list[dict]:
    """ประวัติการฉีดจาก visitepi ของคนเหล่านี้"""
    out = []
    for part in _chunks(keys):
        cond = " OR ".join(["(e.pcucodeperson = %s AND e.pid = %s)"] * len(part))
        out += q(
            f"""
            SELECT e.pcucodeperson, e.pid, e.dateepi, e.vaccinecode, e.lotno, e.hosservice,
                   COALESCE(d.drugname, e.vaccinecode) AS vaccinename
            FROM visitepi e LEFT JOIN cdrug d ON d.drugcode = e.vaccinecode
            WHERE ({cond}) AND e.vaccinecode <> ''
            ORDER BY e.dateepi
            """,
            [v for k in part for v in k],
        )
    return out


def epi_appointments(pcucodeperson: str, pid: int) -> list[dict]:
    return q(
        """
        SELECT a.dateappoint, a.vaccinecode, COALESCE(d.drugname, a.vaccinecode) AS vaccinename,
               a.hoscodeappoint
        FROM visitepiappoint a LEFT JOIN cdrug d ON d.drugcode = a.vaccinecode
        WHERE a.pcucodeperson = %s AND a.pid = %s
        ORDER BY a.dateappoint DESC
        """,
        (pcucodeperson, pid),
    )
