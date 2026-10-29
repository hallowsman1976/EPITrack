from datetime import date

import history
from history import Record

VMAP = {
    "D21": [("DTP", None)],
    "D51": [("DTP", None), ("POLIO", None)],
    "I11": [("POLIO", None)],
    "R11": [("ROTA", "RV1")],
    "RB1": [("", None)],
}


def test_same_series_same_day_counts_once_and_keeps_both_sources():
    day = date(2025, 3, 15)
    recs = [
        Record("jhcis", day, "D21"),
        Record("app", day, "D21", app={"id": 7, "place": "here"}),
    ]
    shots = history.shots_by_series(recs, VMAP)
    assert len(shots["DTP"]) == 1
    shot = shots["DTP"][0]
    assert shot.sources == {"jhcis", "app"} and shot.record_id == 7


def test_combination_vaccine_counts_for_each_series():
    shots = history.shots_by_series([Record("jhcis", date(2025, 3, 15), "D51")], VMAP)
    assert set(shots) == {"DTP", "POLIO"}


def test_ignored_and_unknown_codes_do_not_count():
    shots = history.shots_by_series([Record("jhcis", date(2025, 3, 15), "RB1"),
                                     Record("jhcis", date(2025, 3, 15), "ZZZ")], VMAP)
    assert shots == {}


def test_product_is_carried_for_rota():
    shots = history.shots_by_series([Record("app", date(2025, 3, 15), "R11", app={"id": 1, "place": "here"})], VMAP)
    assert shots["ROTA"][0].product == "RV1"
