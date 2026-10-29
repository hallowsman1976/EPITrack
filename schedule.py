"""ตารางวัคซีนและการประเมินสถานะของแต่ละเข็ม (ไม่แตะฐานข้อมูล ทดสอบได้ด้วย pytest)

ตารางวัคซีน (defaults/schedule.json หรือฉบับที่ผู้ดูแลแก้ใน epitrack.settings)
  series[]: id, name, sex?, birth_dose?, doses[] หรือ variants[] (PCV เลือกชุดกฎตามอายุตอนเริ่ม)
  dose: label, due, min_age, min_interval?, max_age?, overdue_age?, skip_products?

นับเข็มที่จากลำดับวันที่ของการฉีดที่ผ่านเกณฑ์ ไม่ได้อ่านจากเลขท้ายรหัส (docs/adr/0003)
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from paths import RESOURCE_DIR

DEFAULTS_DIR = RESOURCE_DIR / "defaults"

# สถานะของแต่ละเข็ม: key -> (ข้อความ, คลาส badge)
STATUS = {
    "given": ("ได้แล้ว", "ok"),
    "not_yet": ("ยังไม่ถึง", ""),
    "due": ("ถึงกำหนด", "warn"),
    "overdue": ("เลยกำหนด", "danger"),
    "out_of_age": ("พ้นช่วงอายุ", ""),
    "no_data": ("ไม่มีข้อมูล (อาจได้ที่ รพ.)", ""),
}
PENDING = ("due", "overdue")  # ค้างฉีด

_AGE = re.compile(r"(\d+)([ymwd])")


@dataclass(frozen=True)
class Age:
    years: int = 0
    months: int = 0
    days: int = 0

    @classmethod
    def parse(cls, text: str) -> "Age":
        text = (text or "").strip()
        parts = _AGE.findall(text)
        if not text or "".join(n + u for n, u in parts) != text:
            raise ValueError(f"อายุ \"{text}\" ไม่ถูกต้อง (ตัวอย่างที่ถูก: 2m, 6w, 1y6m, 0d)")
        y = m = d = 0
        for n, unit in parts:
            n = int(n)
            if unit == "y":
                y += n
            elif unit == "m":
                m += n
            elif unit == "w":
                d += 7 * n
            else:
                d += n
        return cls(y, m, d)

    def after(self, start: date) -> date:
        """วันที่เมื่อครบอายุนี้นับจาก start (ปี/เดือนตามปฏิทิน ถ้าวันเกินสิ้นเดือนใช้วันสุดท้ายของเดือน)"""
        total = start.year * 12 + start.month - 1 + self.years * 12 + self.months
        y, m = divmod(total, 12)
        last_day = (date(y + (m + 1) // 12, (m + 1) % 12 + 1, 1) - timedelta(days=1)).day
        return date(y, m + 1, min(start.day, last_day)) + timedelta(days=self.days)

    def before(self, end: date) -> date:
        """วันที่ย้อนหลังจาก end ไปเท่ากับอายุนี้ (ใช้หาวันเกิดจากอายุ)"""
        return Age(-self.years, -self.months, -self.days).after(end)


ZERO = Age()
ONE_MONTH = Age(months=1)


@dataclass
class DoseRule:
    label: str
    due: Age
    min_age: Age
    min_interval: Age | None = None
    max_age: Age | None = None
    overdue_age: Age | None = None
    skip_products: tuple[str, ...] = ()


@dataclass
class Variant:
    label: str
    first_dose_before: Age | None
    doses: list[DoseRule]


@dataclass
class Series:
    id: str
    name: str
    variants: list[Variant]
    sex: str | None = None
    birth_dose: bool = False


@dataclass
class Rules:
    name: str
    series: list[Series]

    def get(self, series_id: str) -> Series | None:
        return next((s for s in self.series if s.id == series_id), None)


def _age(raw: dict, key: str, required: bool = False) -> Age | None:
    if key not in raw or raw[key] in (None, ""):
        if required:
            raise ValueError(f"ขาดค่า {key}")
        return None
    return Age.parse(raw[key])


def _dose(raw: dict) -> DoseRule:
    return DoseRule(
        label=str(raw.get("label") or "").strip() or "-",
        due=_age(raw, "due", True),
        min_age=_age(raw, "min_age") or ZERO,
        min_interval=_age(raw, "min_interval"),
        max_age=_age(raw, "max_age"),
        overdue_age=_age(raw, "overdue_age"),
        skip_products=tuple(raw.get("skip_products") or ()),
    )


def parse(data: dict) -> Rules:
    """แปลง JSON เป็นกฎ ถ้าผิดรูปแบบ throw ValueError พร้อมข้อความภาษาไทยว่าผิดตรงไหน"""
    if not isinstance(data, dict) or not isinstance(data.get("series"), list):
        raise ValueError("ต้องมี \"series\" เป็นรายการ")
    series, seen = [], set()
    for i, raw in enumerate(data["series"], 1):
        sid = str(raw.get("id") or "").strip()
        where = f"ชุดวัคซีนที่ {i} ({sid or 'ไม่มี id'})"
        try:
            if not sid or sid in seen:
                raise ValueError("id ว่างหรือซ้ำ")
            seen.add(sid)
            if raw.get("variants"):
                variants = [Variant(str(v.get("label") or ""), _age(v, "first_dose_before"),
                                    [_dose(d) for d in v["doses"]]) for v in raw["variants"]]
            else:
                variants = [Variant("", None, [_dose(d) for d in raw["doses"]])]
            if any(not v.doses for v in variants):
                raise ValueError("ต้องมีอย่างน้อย 1 เข็ม")
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{where}: {exc}") from exc
        series.append(Series(sid, str(raw.get("name") or sid), variants,
                             sex=raw.get("sex") or None, birth_dose=bool(raw.get("birth_dose"))))
    return Rules(str(data.get("name") or ""), series)


def default_json() -> str:
    return (DEFAULTS_DIR / "schedule.json").read_text(encoding="utf-8")


def load_default() -> Rules:
    return parse(json.loads(default_json()))


def default_vaccine_map() -> list[dict]:
    data = json.loads((DEFAULTS_DIR / "vaccine_map.json").read_text(encoding="utf-8"))
    return data["map"]


# ---------- การประเมิน ----------
@dataclass
class Shot:
    """การฉีดหนึ่งครั้งในประวัติวัคซีน (รวมจาก JHCIS และแอปแล้ว)"""
    date: date
    code: str
    product: str | None = None
    sources: set[str] = field(default_factory=set)  # {"jhcis", "app"}
    record_id: int | None = None  # รหัสรายการฉีดในแอป
    place: str | None = None


@dataclass
class Slot:
    no: int
    label: str
    status: str
    shot: Shot | None = None
    earliest: date | None = None  # ฉีดได้ตั้งแต่
    due: date | None = None  # ควรได้
    overdue_from: date | None = None  # นับเลยกำหนดเมื่อเลยวันนี้
    latest: date | None = None  # อายุสูงสุดที่ยังฉีดได้

    @property
    def status_text(self) -> str:
        return STATUS[self.status][0]

    @property
    def badge(self) -> str:
        return STATUS[self.status][1]


@dataclass
class SeriesResult:
    series: Series
    variant: Variant
    slots: list[Slot]
    invalid: list[Shot]  # ไม่นับ (ฉีดก่อนกำหนด)
    extra: list[Shot]  # เกินจำนวนเข็มของตาราง

    @property
    def next_slot(self) -> Slot | None:
        return next((s for s in self.slots if s.status != "given"), None)

    @property
    def complete(self) -> bool:
        return all(s.status == "given" for s in self.slots)


def applies(series: Series, sex: str | None) -> bool:
    return not series.sex or series.sex == str(sex or "")


def _pick_variant(series: Series, birth: date, ref: date) -> Variant:
    for v in series.variants:
        if v.first_dose_before is None or ref < v.first_dose_before.after(birth):
            return v
    return series.variants[-1]


def evaluate(series: Series, shots: list[Shot], birth: date, today: date) -> SeriesResult:
    shots = sorted(shots, key=lambda s: s.date)
    variant = _pick_variant(series, birth, shots[0].date if shots else today)
    products = {s.product for s in shots if s.product}
    rules = [r for r in variant.doses if not products.intersection(r.skip_products)]

    slots: list[Slot] = []
    invalid: list[Shot] = []
    extra: list[Shot] = []
    prev: Shot | None = None
    for shot in shots:
        i = len(slots)
        if i >= len(rules):
            extra.append(shot)
            continue
        rule = rules[i]
        if shot.date < rule.min_age.after(birth) or (
            prev and rule.min_interval and shot.date < rule.min_interval.after(prev.date)
        ):
            invalid.append(shot)
            continue
        slots.append(Slot(i + 1, rule.label, "given", shot=shot, due=rule.due.after(birth)))
        prev = shot

    earliest_prev: date | None = prev.date if prev else None
    prev_status = "given"
    for i in range(len(slots), len(rules)):
        rule = rules[i]
        earliest = rule.min_age.after(birth)
        if earliest_prev and rule.min_interval:
            earliest = max(earliest, rule.min_interval.after(earliest_prev))
        elif earliest_prev:
            earliest = max(earliest, earliest_prev)
        due = max(rule.due.after(birth), earliest)
        overdue_from = rule.overdue_age.after(birth) if rule.overdue_age else ONE_MONTH.after(due)
        latest = rule.max_age.after(birth) if rule.max_age else None

        if series.birth_dose:
            status = "no_data"
        elif (latest and today > latest) or prev_status == "out_of_age":
            status = "out_of_age"
        elif prev_status != "given" or today < earliest:
            status = "not_yet"
        elif today <= overdue_from:
            status = "due"
        else:
            status = "overdue"
        slots.append(Slot(i + 1, rule.label, status, earliest=earliest, due=due,
                          overdue_from=overdue_from, latest=latest))
        earliest_prev, prev_status = due, status
    return SeriesResult(series, variant, slots, invalid, extra)


def evaluate_all(rules: Rules, shots_by_series: dict[str, list[Shot]], birth: date,
                 sex: str | None, today: date) -> list[SeriesResult]:
    return [evaluate(s, shots_by_series.get(s.id, []), birth, today)
            for s in rules.series if applies(s, sex)]


def suggest_appointment(results: list[SeriesResult], today: date) -> tuple[date | None, list[tuple[SeriesResult, Slot]]]:
    """วันนัดที่แนะนำ = วันแรกที่มีเข็มถัดไปฉีดได้ (ไม่ก่อนพรุ่งนี้) และเข็มที่ฉีดได้ในวันนั้น"""
    candidates = []
    for r in results:
        slot = r.next_slot
        if slot and slot.status in ("not_yet", "due", "overdue") and slot.earliest:
            candidates.append((r, slot, max(slot.due or slot.earliest, today + timedelta(days=1))))
    if not candidates:
        return None, []
    when = min(c[2] for c in candidates)
    return when, [(r, s) for r, s, _ in candidates if s.earliest <= when]


def age_parts(birth: date, on: date) -> tuple[int, int, int]:
    """อายุเป็น (ปี, เดือน, วัน)"""
    months = (on.year - birth.year) * 12 + on.month - birth.month
    if on.day < birth.day:
        months -= 1
    anchor = Age(months=months).after(birth)
    return months // 12, months % 12, (on - anchor).days


# ช่วงวัย: key -> (ข้อความ, อายุต่ำสุด, อายุที่ไม่ถึง) เป็นปี
AGE_BANDS = {
    "0": ("0–<1 ปี", 0, 1),
    "1": ("1–<2 ปี", 1, 2),
    "2": ("2–<3 ปี", 2, 3),
    "3": ("3–<6 ปี", 3, 6),
    "6": ("6–<10 ปี", 6, 10),
    "10": ("10–<13 ปี", 10, 13),
    "13": ("13–18 ปี", 13, 19),
}


def birth_range(band: str, today: date) -> tuple[date, date]:
    """ช่วงวันเกิด (ตั้งแต่, ถึง) ของเด็กในช่วงวัยนี้ ณ วันนี้"""
    _, lo, hi = AGE_BANDS[band]
    return Age(years=hi).before(today) + timedelta(days=1), Age(years=lo).before(today)

