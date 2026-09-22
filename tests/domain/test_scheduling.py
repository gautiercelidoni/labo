from datetime import date

import pytest

from app.domain.scheduling import add_months, add_period, alert_threshold, due_state, next_due


def test_add_months_clamps_end_of_month():
    assert add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)


@pytest.mark.parametrize("unit,value,expected", [
    ("day", 10, date(2026, 3, 11)),
    ("week", 2, date(2026, 3, 15)),
    ("month", 6, date(2026, 9, 1)),
    ("year", 1, date(2027, 3, 1)),
])
def test_add_period(unit, value, expected):
    assert add_period(date(2026, 3, 1), value, unit) == expected


def test_add_period_invalid():
    with pytest.raises(ValueError):
        add_period(date(2026, 3, 1), 0, "month")
    with pytest.raises(ValueError):
        add_period(date(2026, 3, 1), 1, "siècle")


def test_next_due_from_effective_date():
    # La prochaine échéance part de la date EFFECTIVE de réalisation (réalisée en retard le 15/04).
    assert next_due(date(2026, 4, 15), 1, "year") == date(2027, 4, 15)
    assert next_due(None, 1, "year") is None


@pytest.mark.parametrize("delta,state,threshold", [
    (-1, "overdue", "retard"),
    (0, "due_today", "J0"),
    (1, "due_7", "J-7"),
    (7, "due_7", "J-7"),
    (8, "due_30", "J-30"),
    (30, "due_30", "J-30"),
    (31, "ok", None),
])
def test_due_state_thresholds(delta, state, threshold):
    today = date(2026, 5, 10)
    due = date.fromordinal(today.toordinal() + delta)
    assert due_state(due, today) == state
    assert alert_threshold(due, today) == threshold


def test_due_state_without_date():
    assert due_state(None, date(2026, 1, 1)) == "none"
