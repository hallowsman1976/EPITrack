"""ประวัติวัคซีน = visitepi ของ JHCIS รวมกับรายการฉีดในแอป แล้วประเมินกับตารางวัคซีน

คนเดียวกัน + ชุดวัคซีนเดียวกัน + วันเดียวกัน นับเป็นการฉีดครั้งเดียว (docs/adr/0001, 0003)
"""

from dataclasses import dataclass, field
from datetime import date, timedelta

import jhcis
import schedule
import store

Key = tuple[str, int]
MISMATCH_DAYS = 30  # JHCIS มีชุดวัคซีนเดียวกันห่างไม่เกินกี่วัน = "วันที่ไม่ตรง"


@dataclass
class Record:
    """การฉีดหนึ่งแถว ก่อนรวม (หนึ่งแถวของ visitepi หรือหนึ่งรายการในแอป)"""
    source: str  # "jhcis" | "app"
    date: date
    code: str
    name: str = ""
    lot: str = ""
    place: str = ""  # ข้อความสถานที่
    app: dict | None = None  # แถวของ vaccinations
    series: list[str] = field(default_factory=list)  # ชุดวัคซีนที่นับให้ ([] = ไม่อยู่ใน mapping)
    mapped: bool = False


def shots_by_series(records: list[Record], vmap: dict) -> dict[str, list[schedule.Shot]]:
    merged: dict[tuple[str, date], schedule.Shot] = {}
    for rec in records:
        for series, product in vmap.get(rec.code, []):
            if not series:
                continue
            shot = merged.setdefault((series, rec.date), schedule.Shot(rec.date, rec.code, product))
            shot.sources.add(rec.source)
            shot.product = shot.product or product
            if rec.app:
                shot.record_id, shot.place = rec.app["id"], rec.app["place"]
    out: dict[str, list[schedule.Shot]] = {}
    for (series, _), shot in merged.items():
        out.setdefault(series, []).append(shot)
    return out


def _records(epi_rows: list[dict], app_rows: list[dict], vmap: dict, names: dict, hospitals: dict) -> list[Record]:
    recs = []
    for r in epi_rows:
        hos = r.get("hosservice") or ""
        recs.append(Record("jhcis", r["dateepi"], r["vaccinecode"], r["vaccinename"], r.get("lotno") or "",
                           hospitals.get(hos, hos)))
    for r in app_rows:
        place = store.PLACES.get(r["place"], r["place"])
        if r.get("place_note"):
            place += f" ({r['place_note']})"
        recs.append(Record("app", r["date_given"], r["vaccine_code"], names.get(r["vaccine_code"], r["vaccine_code"]),
                           r["lot_no"], place, app=r))
    for rec in recs:
        rows = vmap.get(rec.code)
        rec.mapped = rows is not None
        rec.series = [s for s, _ in rows or [] if s]
    recs.sort(key=lambda r: (r.date, r.source != "jhcis"))
    return recs


@dataclass
class Card:
    """สมุดวัคซีนของเด็กหนึ่งคน"""
    person: dict
    today: date
    records: list[Record]
    results: list[schedule.SeriesResult]

    @property
    def pending(self) -> list[tuple[schedule.SeriesResult, schedule.Slot]]:
        return [(r, r.next_slot) for r in self.results if r.next_slot and r.next_slot.status in schedule.PENDING]

    @property
    def worst(self) -> str:
        statuses = {s.status for _, s in self.pending}
        return "overdue" if "overdue" in statuses else "due" if "due" in statuses else "ok"


def card(person: dict, today: date, rules: schedule.Rules | None = None, vmap: dict | None = None,
         names: dict | None = None) -> Card:
    rules = rules or store.rules()
    vmap = vmap if vmap is not None else store.vaccine_map()
    if names is None:
        names = {v["code"]: v["name"] for v in jhcis.vaccines()}
    key = (person["pcucodeperson"], person["pid"])
    epi = jhcis.epi_history([key])
    app = store.vaccinations_for(*key)
    hospitals = jhcis.hospital_names(r.get("hosservice") for r in epi)
    records = _records(epi, app, vmap, names, hospitals)
    shots = shots_by_series(records, vmap)
    results = schedule.evaluate_all(rules, shots, person["birth"], person["sex"], today)
    return Card(person, today, records, results)


def roster(children: list[dict], today: date) -> list[Card]:
    """สถานะวัคซีนของเด็กหลายคน (อ่านฐานข้อมูลครั้งเดียวต่อตาราง)"""
    rules, vmap = store.rules(), store.vaccine_map()
    keys: list[Key] = [(c["pcucodeperson"], c["pid"]) for c in children]
    epi, app = {}, {}
    for r in jhcis.epi_history(keys):
        epi.setdefault((r["pcucodeperson"], r["pid"]), []).append(r)
    for r in store.vaccinations_for_many(keys):
        app.setdefault((r["pcucodeperson"], r["pid"]), []).append(r)
    cards = []
    for child, key in zip(children, keys):
        records = _records(epi.get(key, []), app.get(key, []), vmap, {}, {})
        results = schedule.evaluate_all(rules, shots_by_series(records, vmap), child["birth"], child["sex"], today)
        cards.append(Card(child, today, records, results))
    return cards


@dataclass
class PendingItem:
    """รายการฉีดที่นี่ในแอป เทียบกับ JHCIS"""
    record: dict
    person: dict | None
    series: list[str]
    status: str  # "missing" | "mismatch"
    jhcis_dates: list[date]


def pending_jhcis(start: date, end: date) -> list[PendingItem]:
    """รายการฉีดที่นี่ที่ยังไม่พบใน visitepi (ลง JHCIS แล้วจะไม่อยู่ในรายการนี้)"""
    rows = store.here_vaccinations(start, end)
    if not rows:
        return []
    vmap = store.vaccine_map()
    keys = list({(r["pcucodeperson"], r["pid"]) for r in rows})
    people = jhcis.persons(keys)
    epi: dict[Key, list[tuple[str, date]]] = {}
    for e in jhcis.epi_history(keys):
        # รหัสที่ไม่นับเป็นชุดวัคซีนใด เทียบด้วยรหัสตรง ๆ แทน
        groups = [s for s, _ in vmap.get(e["vaccinecode"], []) if s] or [f"code:{e['vaccinecode']}"]
        for series in groups:
            epi.setdefault((e["pcucodeperson"], e["pid"]), []).append((series, e["dateepi"]))
    items = []
    for r in rows:
        key = (r["pcucodeperson"], r["pid"])
        series = [s for s, _ in vmap.get(r["vaccine_code"], []) if s]
        groups = series or [f"code:{r['vaccine_code']}"]
        given = epi.get(key, [])
        if all((s, r["date_given"]) in given for s in groups):
            continue  # ลง JHCIS แล้ว
        else:
            near = sorted({d for s, d in given if s in groups and d != r["date_given"]
                           and abs((d - r["date_given"]).days) <= MISMATCH_DAYS})
            status = "mismatch" if near else "missing"
        items.append(PendingItem(r, people.get(key), series, status, near))
    return items


@dataclass
class GrowthRow:
    """การชั่งวัดหนึ่งครั้ง จาก JHCIS หรือแอป"""
    source: str  # "jhcis" | "app"
    date: date
    values: dict  # weight, height, head, teeth, teeth_decayed (None = ไม่ได้วัด)
    app: dict | None = None
    in_jhcis: bool = False  # รายการในแอปที่พบใน JHCIS วันเดียวกันแล้ว


def growth(pcucodeperson: str, pid: int) -> list[GrowthRow]:
    """ประวัติการชั่งวัด ใหม่สุดก่อน รายการในแอปที่ JHCIS มีวันเดียวกันแล้วแสดงคู่กัน ไม่รวมเป็นแถวเดียว
    (ค่าอาจต่างกันได้ ให้คนเห็นทั้งสองแหล่ง)"""
    jrows = jhcis.growth_history([(pcucodeperson, pid)])
    jdates = {r["visitdate"] for r in jrows}
    rows = [GrowthRow("jhcis", r["visitdate"], {k: r[k] for k in store.MEASURES}) for r in jrows]
    rows += [GrowthRow("app", r["date_checked"], {k: r[k] for k in store.MEASURES}, app=r,
                       in_jhcis=r["date_checked"] in jdates) for r in store.growth_for(pcucodeperson, pid)]
    return sorted(rows, key=lambda g: (g.date, g.source == "app"), reverse=True)


@dataclass
class PendingGrowth:
    """การชั่งวัดที่นี่ในแอป ที่ยังไม่พบใน visitnutrition วันเดียวกัน"""
    record: dict
    person: dict | None


def pending_growth(start: date, end: date) -> list[PendingGrowth]:
    rows = store.here_growth(start, end)
    if not rows:
        return []
    keys = list({(r["pcucodeperson"], r["pid"]) for r in rows})
    people = jhcis.persons(keys)
    done = {(r["pcucodeperson"], r["pid"], r["visitdate"]) for r in jhcis.growth_history(keys)}
    return [PendingGrowth(r, people.get((r["pcucodeperson"], r["pid"]))) for r in rows
            if (r["pcucodeperson"], r["pid"], r["date_checked"]) not in done]


def default_pending_range(today: date) -> tuple[date, date]:
    return today - timedelta(days=90), today
