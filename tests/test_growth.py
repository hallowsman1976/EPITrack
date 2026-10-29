from datetime import date, timedelta

import pytest

import store


def form(**kw):
    data = {"date_checked": date(2026, 1, 5), "place": "here", "note": ""}
    data.update(kw)
    return data


def test_converts_numbers_and_keeps_blanks_as_none():
    row = store.clean_growth(form(weight="9.25", height="74", head="", teeth="6", teeth_decayed=""))
    assert row["weight"] == 9.25 and row["height"] == 74.0 and row["teeth"] == 6
    assert row["head"] is None and row["teeth_decayed"] is None


def test_needs_at_least_one_measure():
    assert not store.has_measures(form())
    with pytest.raises(ValueError, match="อย่างน้อย 1 ค่า"):
        store.clean_growth(form())


@pytest.mark.parametrize("field,value", [("weight", "95.0x"), ("weight", "0.1"), ("height", "500"), ("teeth", "40")])
def test_rejects_typos_and_impossible_values(field, value):
    with pytest.raises(ValueError):
        store.clean_growth(form(**{field: value}))


def test_decayed_cannot_exceed_teeth():
    with pytest.raises(ValueError, match="ฟันผุ"):
        store.clean_growth(form(teeth="4", teeth_decayed="5"))


def test_future_date_rejected():
    with pytest.raises(ValueError, match="อนาคต"):
        store.clean_growth(form(weight="10", date_checked=date.today() + timedelta(days=1)))
