"""Règles de Westgard : chaque règle isolément, limites exactes, multi-niveaux, lots, annulations."""
from decimal import Decimal

import pytest

from app.domain.ciq_rules.engine import cross_level_sequence, evaluate, level_sequence
from app.domain.ciq_rules.types import DEFAULT_SEVERITIES
from tests.domain.helpers import not_evaluated, pt, series

ALL_REJECT = {r: "reject" for r in ("1-2s", "1-3s", "2-2s", "R-4s", "4-1s", "10x")}


def ev(point, history=(), severities=None, chain_lots=False):
    return evaluate(point, list(history), severities, chain_lots)


# --- 1-2s -------------------------------------------------------------------------------------

def test_1_2s_triggers_above_2s():
    e = ev(pt(0, 2.01))
    assert e.triggered == ["1-2s"]
    assert e.status == "warning"  # gravité par défaut : avertissement


def test_1_2s_negative_side():
    assert "1-2s" in ev(pt(0, -2.5)).triggered


def test_exactly_2s_does_not_trigger_1_2s():
    # Limite stricte : |z| > 2. Valeur exactement à cible + 2s.
    e = ev(pt(0, 2))
    assert e.z == Decimal(2)
    assert e.triggered == []
    assert e.status == "accepted"


def test_exactly_2s_with_non_terminating_decimals():
    # s = 0,3 ; valeur = 5,5 + 0,6 : calcul décimal exact, pas d'erreur d'arrondi flottant.
    p = pt(0, 2, mean=Decimal("5.5"), sd=Decimal("0.3"))
    assert p.value == Decimal("6.1")
    assert ev(p).triggered == []


# --- 1-3s -------------------------------------------------------------------------------------

def test_1_3s_rejects():
    e = ev(pt(0, -3.2))
    assert set(e.triggered) == {"1-2s", "1-3s"}
    assert e.status == "rejected"


def test_exactly_3s_is_not_1_3s():
    e = ev(pt(0, 3))
    assert "1-3s" not in e.triggered
    assert "1-2s" in e.triggered


# --- 2-2s -------------------------------------------------------------------------------------

def test_2_2s_across_runs_same_level():
    history = [pt(0, 2.3)]
    e = ev(pt(1, 2.1), history)
    assert "2-2s" in e.triggered
    assert e.status == "rejected"


def test_2_2s_requires_same_side():
    e = ev(pt(1, 2.2), [pt(0, -2.3)])
    assert "2-2s" not in e.triggered


def test_2_2s_within_run_across_levels():
    n2 = pt(0, 2.4, "N2", run="R")
    e = ev(pt(0, 2.2, "N1", run="R"), [n2])
    assert "2-2s" in e.triggered


def test_2_2s_previous_exactly_2s_does_not_count():
    e = ev(pt(1, 2.5), [pt(0, 2)])
    assert "2-2s" not in e.triggered


def test_2_2s_not_evaluated_without_history_or_peers():
    e = ev(pt(0, 2.5))
    assert "2-2s" in not_evaluated(e)


def test_2_2s_evaluated_when_point_within_2s():
    # Le point lui-même ne dépasse pas 2s : la règle ne peut pas être déclenchée, elle est « évaluée ».
    assert "2-2s" not in not_evaluated(ev(pt(0, 1.0)))


# --- R-4s -------------------------------------------------------------------------------------

def test_r_4s_opposite_levels_same_run():
    e = ev(pt(0, 2.2, "N1", run="R"), [pt(0, -2.1, "N2", run="R")])
    assert "R-4s" in e.triggered
    assert e.status == "rejected"


def test_r_4s_only_within_same_run():
    # Même écart mais sur deux séries différentes : pas de R-4s.
    e = ev(pt(1, 2.2, "N1"), [pt(0, -2.1, "N2")])
    assert "R-4s" not in e.triggered


def test_r_4s_not_evaluated_with_single_level():
    assert "R-4s" in not_evaluated(ev(pt(0, 2.5)))


# --- 4-1s -------------------------------------------------------------------------------------

def test_4_1s_same_level():
    history = series([1.2, 1.5, 1.1])
    e = ev(pt(3, 1.3), history)
    assert "4-1s" in e.triggered
    assert e.status == "warning"  # décision validée : avertissement par défaut


def test_4_1s_configurable_as_reject():
    history = series([1.2, 1.5, 1.1])
    e = ev(pt(3, 1.3), history, {"4-1s": "reject"})
    assert e.status == "rejected"


def test_4_1s_exactly_1s_breaks_sequence():
    history = series([1.2, 1.0, 1.1])
    assert "4-1s" not in ev(pt(3, 1.3), history).triggered


def test_4_1s_across_levels():
    # 2 niveaux × 2 séries, tous au-delà de +1s (règle inter-niveaux).
    history = [pt(0, 1.2, "N1", run="A"), pt(0, 1.4, "N2", run="A"), pt(1, 1.1, "N1", run="B")]
    e = ev(pt(1, 1.3, "N2", run="B"), history)
    assert "4-1s" in e.triggered


def test_4_1s_insufficient_history():
    e = ev(pt(2, 1.3), series([1.2, 1.5]))
    assert "4-1s" in not_evaluated(e)
    assert "4-1s" not in e.triggered


# --- 10x --------------------------------------------------------------------------------------

def test_10x_same_level():
    history = series([0.2, 0.5, 0.1, 0.8, 0.3, 0.4, 0.6, 0.2, 0.9])
    assert "10x" in ev(pt(9, 0.1), history).triggered


def test_10x_value_exactly_on_mean_breaks_sequence():
    history = series([0.2, 0.5, 0.1, 0.8, 0.0, 0.4, 0.6, 0.2, 0.9])
    assert "10x" not in ev(pt(9, 0.1), history).triggered


def test_10x_across_levels():
    history = []
    for day in range(5):
        history += [pt(day, 0.3, "N1", run=f"r{day}"), pt(day, 0.4, "N2", run=f"r{day}")]
    point = history.pop()  # le 10e point est évalué
    assert "10x" in ev(point, history).triggered


def test_10x_insufficient_history_reported():
    e = ev(pt(5, 0.3), series([0.2, 0.3, 0.4, 0.5, 0.1]))
    assert "10x" in not_evaluated(e)


# --- Changement de lot, de limites, valeurs annulées -----------------------------------------

def test_lot_change_resets_level_sequence():
    history = series([2.3], lot="L1")
    e = ev(pt(1, 2.2, lot="L2"), history)
    assert "2-2s" not in e.triggered
    assert "2-2s" in not_evaluated(e)


def test_lot_change_resets_4_1s():
    history = series([1.2, 1.5], lot="L1") + series([1.1], start=2, lot="L2")
    assert "4-1s" not in ev(pt(3, 1.3, lot="L2"), history).triggered


def test_lot_change_on_other_level_resets_cross_level_sequence():
    history = [pt(0, 1.2, "N1", run="A", lot="A1"), pt(0, 1.4, "N2", run="A", lot="B1"),
               pt(1, 1.1, "N1", run="B", lot="A2")]
    point = pt(1, 1.3, "N2", run="B", lot="B1")
    seq = cross_level_sequence(point, history)
    assert [p.run_key for p in seq] == ["B", "B"]
    assert "4-1s" not in ev(point, history).triggered


def test_chain_lots_option_keeps_sequences_via_z_score():
    history = series([2.3], lot="L1")
    e = ev(pt(1, 2.2, lot="L2"), history, chain_lots=True)
    assert "2-2s" in e.triggered


def test_limit_set_change_does_not_reset_sequence():
    # Chaque point est évalué avec son propre jeu de limites ; la séquence continue.
    history = [pt(0, 2.3, limit="ANCIEN", mean=Decimal(100), sd=Decimal(2))]
    point = pt(1, 2.2, limit="NOUVEAU", mean=Decimal(101), sd=Decimal("1.5"))
    assert "2-2s" in ev(point, history).triggered


def test_voided_points_are_ignored_without_breaking_sequence():
    history = [pt(0, 2.3), pt(1, -3.0, voided=True)]
    e = ev(pt(2, 2.4), history)
    assert "2-2s" in e.triggered  # l'annulé est ignoré : les deux points > 2s restent consécutifs


def test_missing_level_in_run_is_not_an_error():
    # Série avec un seul niveau passé : R-4s non évaluée, les autres règles fonctionnent.
    e = ev(pt(0, 3.5))
    assert "1-3s" in e.triggered
    assert "R-4s" in not_evaluated(e)


def test_other_mode_points_are_ignored():
    history = [pt(0, 2.3, mode="shewhart")]
    e = ev(pt(1, 2.2), history)
    assert "2-2s" not in e.triggered


def test_level_sequence_is_chronological_and_ends_with_point():
    history = [pt(3, 0.1), pt(1, 0.2), pt(2, 0.3), pt(5, 0.4)]  # le point du jour 5 est postérieur
    seq = level_sequence(pt(4, 0.5), history)
    assert [p.at.day for p in seq] == [2, 3, 4, 5]


# --- Configuration des gravités ---------------------------------------------------------------

def test_rule_disabled_is_not_evaluated_or_reported():
    e = ev(pt(0, 2.5), severities={"1-2s": "off"})
    assert "1-2s" not in e.triggered
    assert "1-2s" not in not_evaluated(e)


def test_warning_and_reject_distinction():
    assert ev(pt(0, 2.5)).status == "warning"
    assert ev(pt(0, 3.5)).status == "rejected"
    assert ev(pt(0, 2.5), severities={"1-2s": "reject"}).status == "rejected"


def test_invalid_severity_falls_back_to_default():
    e = ev(pt(0, 2.5), severities={"1-2s": "n'importe quoi"})
    assert e.status == DEFAULT_SEVERITIES["1-2s"]


@pytest.mark.parametrize("rule", ["1-2s", "1-3s", "2-2s", "R-4s", "4-1s", "10x"])
def test_each_rule_individually(rule):
    """Chaque règle déclenchée seule, les autres étant désactivées."""
    only = {r: ("reject" if r == rule else "off") for r in ALL_REJECT}
    cases = {
        "1-2s": (pt(0, 2.5), []),
        "1-3s": (pt(0, 3.5), []),
        "2-2s": (pt(1, 2.5), [pt(0, 2.1)]),
        "R-4s": (pt(0, 2.5, "N1", run="R"), [pt(0, -2.5, "N2", run="R")]),
        "4-1s": (pt(3, 1.5), series([1.5, 1.5, 1.5])),
        "10x": (pt(9, 0.5), series([0.5] * 9)),
    }
    point, history = cases[rule]
    e = ev(point, history, only)
    assert e.triggered == [rule]
    assert e.status == "rejected"
