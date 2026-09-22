"""Règles des cartes de Shewhart et calcul des limites sur période de référence."""
from decimal import Decimal

import pytest

from app.domain.ciq_rules.engine import evaluate
from app.domain.ciq_rules.shewhart import compute_limits
from tests.domain.helpers import not_evaluated, pt, series


def sh(zs, **kw):
    return series(zs, mode="shewhart", **kw)


def ev(point, history=(), severities=None, chain_lots=False):
    return evaluate(point, list(history), severities, chain_lots)


def spt(day, z, **kw):
    return pt(day, z, mode="shewhart", **kw)


def test_rule_a_outside_action_limits():
    e = ev(spt(0, -3.1))
    assert e.triggered == ["shewhart_a"]
    assert e.status == "rejected"


def test_rule_a_exactly_on_action_limit():
    assert ev(spt(0, 3)).triggered == []


def test_rule_b_two_of_three_beyond_warning_limit():
    e = ev(spt(2, 2.3), sh([2.1, 0.5]))
    assert "shewhart_b" in e.triggered
    assert e.status == "rejected"


def test_rule_b_requires_same_side():
    assert "shewhart_b" not in ev(spt(2, 2.3), sh([-2.1, 0.5])).triggered


def test_rule_b_single_point_beyond_2s_is_accepted():
    e = ev(spt(2, 2.3), sh([0.1, 0.5]))
    assert e.triggered == []
    assert e.status == "accepted"


def test_rule_b_exactly_2s_does_not_count():
    assert "shewhart_b" not in ev(spt(2, 2.3), sh([2.0, 0.5])).triggered


def test_rule_b_not_evaluated_on_first_point():
    assert "shewhart_b" in not_evaluated(ev(spt(0, 2.5)))


def test_rule_c_seven_on_same_side():
    e = ev(spt(6, 0.2), sh([0.1, 0.5, 0.3, 0.9, 0.2, 0.4]))
    assert e.triggered == ["shewhart_c"]
    assert e.status == "warning"


def test_rule_c_six_is_not_enough():
    e = ev(spt(6, 0.2), sh([-0.1, 0.5, 0.3, 0.9, 0.2, 0.4]))
    assert "shewhart_c" not in e.triggered


def test_rule_c_point_on_mean_breaks():
    assert "shewhart_c" not in ev(spt(6, 0.2), sh([0.1, 0.5, 0.0, 0.9, 0.2, 0.4])).triggered


def test_rule_d_ten_of_eleven():
    history = sh([-0.2, -0.5, -0.1, -0.3, -0.8, 0.3, -0.4, -0.6, -0.2, -0.9])
    e = ev(spt(10, -0.1), history)
    assert "shewhart_d" in e.triggered
    assert "shewhart_c" not in e.triggered  # jamais 7 consécutifs


def test_rule_d_nine_of_eleven_is_not_enough():
    history = sh([-0.2, 0.5, -0.1, 0.3, -0.8, -0.3, -0.4, -0.6, -0.2, -0.9])
    assert "shewhart_d" not in ev(spt(10, -0.1), history).triggered


def test_rule_d_insufficient_history_reported():
    e = ev(spt(5, -0.1), sh([-0.2, -0.5, -0.1, -0.3, -0.8]))
    assert "shewhart_d" in not_evaluated(e)


def test_shewhart_lot_change_resets():
    history = sh([0.1, 0.5, 0.3, 0.9, 0.2, 0.4], lot="L1")
    assert "shewhart_c" not in ev(spt(6, 0.2, lot="L2"), history).triggered


def test_shewhart_voided_ignored():
    history = sh([2.1]) + [spt(1, -1.0, voided=True)]
    assert "shewhart_b" in ev(spt(2, 2.4), history).triggered


def test_shewhart_rules_ignore_other_levels():
    history = [spt(0, 2.1, level="N2", run="X")]
    assert "shewhart_b" not in ev(spt(0, 2.2, run="X"), history).triggered


@pytest.mark.parametrize("rule", ["shewhart_a", "shewhart_b", "shewhart_c", "shewhart_d"])
def test_each_shewhart_rule_individually(rule):
    only = {r: ("reject" if r == rule else "off") for r in ("shewhart_a", "shewhart_b", "shewhart_c", "shewhart_d")}
    cases = {
        "shewhart_a": (spt(0, 3.4), []),
        "shewhart_b": (spt(2, 2.5), sh([2.5, 0.0])),
        "shewhart_c": (spt(6, 0.5), sh([0.5] * 6)),
        "shewhart_d": (spt(10, 0.5), sh([0.5] * 5 + [-0.5] + [0.5] * 4)),
    }
    point, history = cases[rule]
    e = ev(point, history, only)
    assert e.triggered == [rule]


# --- Période de référence ---------------------------------------------------------------------

def test_compute_limits_mean_and_sample_sd():
    values = [Decimal(v) for v in ("9.8", "10.1", "10.0", "10.3", "9.9")]
    limits = compute_limits(values, min_points=20)
    assert limits.mean == Decimal("10.020000")
    assert limits.sd == Decimal("0.192354")  # écart-type d'échantillon (n-1)
    assert limits.n == 5
    assert limits.sufficient is False  # période de référence insuffisante signalée


def test_compute_limits_sufficient_period():
    values = [Decimal(10) + Decimal(i % 5) / 10 for i in range(20)]
    assert compute_limits(values, min_points=20).sufficient is True


def test_compute_limits_rejects_degenerate_input():
    with pytest.raises(ValueError):
        compute_limits([Decimal(1)], 20)
    with pytest.raises(ValueError):
        compute_limits([Decimal(1)] * 5, 20)
