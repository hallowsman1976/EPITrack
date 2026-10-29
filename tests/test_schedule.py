from datetime import date

import pytest

import schedule
from schedule import Age, Shot, evaluate

RULES = schedule.load_default()
BIRTH = date(2025, 1, 15)


def shots(*dates, product=None):
    return [Shot(d, "X", product=product) for d in dates]


def statuses(result):
    return [s.status for s in result.slots]


def test_age_parse_and_calendar_months():
    assert Age.parse("1y6m") == Age(1, 6, 0)
    assert Age.parse("6w") == Age(0, 0, 42)
    assert Age.parse("2m").after(date(2025, 12, 31)) == date(2026, 2, 28)
    assert Age(years=1).before(date(2026, 3, 1)) == date(2025, 3, 1)
    with pytest.raises(ValueError):
        Age.parse("2 months")


def test_default_tables_are_consistent():
    series_ids = {s.id for s in RULES.series}
    for row in schedule.default_vaccine_map():
        assert row["series"] in series_ids or row["series"] == ""


def test_dtp_on_time_then_next_due():
    r = evaluate(RULES.get("DTP"), shots(date(2025, 3, 15), date(2025, 5, 15)), BIRTH, date(2025, 7, 20))
    assert statuses(r)[:3] == ["given", "given", "due"]
    assert r.next_slot.due == date(2025, 7, 15)
    assert r.next_slot.overdue_from == date(2025, 8, 15)


def test_overdue_after_one_month():
    r = evaluate(RULES.get("DTP"), [], BIRTH, date(2025, 4, 16))
    assert r.slots[0].status == "overdue"
    assert r.slots[1].status == "not_yet"  # รอเข็มก่อน


def test_too_early_dose_is_not_counted():
    # เข็มแรกอายุ 4 สัปดาห์ (ต่ำกว่า 6 สัปดาห์) ไม่นับ
    r = evaluate(RULES.get("DTP"), shots(date(2025, 2, 12), date(2025, 3, 20)), BIRTH, date(2025, 3, 21))
    assert [s.date for s in r.invalid] == [date(2025, 2, 12)]
    assert r.slots[0].shot.date == date(2025, 3, 20)
    assert r.slots[1].status == "not_yet"
    assert r.slots[1].earliest == date(2025, 4, 17)  # ห่าง 4 สัปดาห์


def test_interval_too_short_is_not_counted():
    r = evaluate(RULES.get("DTP"), shots(date(2025, 3, 15), date(2025, 4, 1)), BIRTH, date(2025, 4, 2))
    assert len(r.invalid) == 1 and r.slots[1].status == "not_yet"


def test_rota_rv1_needs_two_doses_and_has_age_limit():
    r = evaluate(RULES.get("ROTA"), shots(date(2025, 3, 15), date(2025, 5, 15), product="RV1"), BIRTH, date(2025, 6, 1))
    assert r.complete and len(r.slots) == 2
    late = evaluate(RULES.get("ROTA"), [], BIRTH, date(2025, 6, 1))  # อายุเกิน 15 สัปดาห์ ยังไม่เคยได้
    assert statuses(late) == ["out_of_age"] * 3


def test_birth_dose_missing_is_no_data():
    r = evaluate(RULES.get("BCG"), [], BIRTH, date(2026, 1, 1))
    assert statuses(r) == ["no_data"]


def test_pcv_variant_by_age_at_first_dose():
    pcv = RULES.get("PCV")
    on_time = evaluate(pcv, [], BIRTH, date(2025, 3, 20))
    assert len(on_time.slots) == 3 and on_time.variant.first_dose_before == Age(months=7)
    start_at_9m = evaluate(pcv, shots(date(2025, 10, 15)), BIRTH, date(2025, 11, 20))
    assert start_at_9m.variant.label.startswith("เริ่มอายุ 7")
    assert start_at_9m.slots[1].status == "due"
    # เข็มกระตุ้นต้องอายุ 12 เดือน และห่างเข็ม 2 อย่างน้อย 8 สัปดาห์
    assert start_at_9m.slots[2].status == "not_yet"
    start_at_18m = evaluate(pcv, shots(date(2026, 7, 15)), BIRTH, date(2026, 8, 1))
    assert len(start_at_18m.slots) == 2  # ไม่ต้องให้เข็มกระตุ้น
    assert start_at_18m.slots[1].earliest == date(2026, 9, 9)
    too_old = evaluate(pcv, [], date(2022, 1, 1), date(2026, 1, 1))
    assert too_old.slots[0].status == "out_of_age"


def test_school_vaccine_due_until_13():
    hpv = RULES.get("HPV")
    girl = date(2014, 6, 1)
    assert evaluate(hpv, [], girl, date(2025, 6, 2)).slots[0].status == "due"
    assert evaluate(hpv, [], girl, date(2027, 1, 1)).slots[0].status == "due"
    assert evaluate(hpv, [], girl, date(2027, 6, 2)).slots[0].status == "overdue"
    assert not schedule.applies(hpv, "1")


def test_extra_doses_are_kept_apart():
    r = evaluate(RULES.get("MMR"), shots(date(2025, 10, 15), date(2026, 7, 15), date(2027, 1, 1)), BIRTH, date(2027, 2, 1))
    assert r.complete and len(r.extra) == 1


def test_suggest_appointment_groups_vaccines_due_same_day():
    today = date(2025, 3, 1)
    results = schedule.evaluate_all(RULES, {}, BIRTH, "1", today)
    when, items = schedule.suggest_appointment(results, today)
    assert when == date(2025, 3, 15)
    assert {r.series.id for r, _ in items} == {"DTP", "POLIO", "ROTA", "PCV"}


def test_parse_reports_where_the_error_is():
    with pytest.raises(ValueError, match="DTP"):
        schedule.parse({"series": [{"id": "DTP", "doses": [{"label": "x", "due": "2 เดือน"}]}]})


def test_birth_range_for_age_band():
    lo, hi = schedule.birth_range("0", date(2026, 10, 29))
    assert lo == date(2025, 10, 30) and hi == date(2026, 10, 29)
    assert schedule.age_parts(date(2025, 1, 31), date(2025, 3, 1)) == (0, 1, 1)
