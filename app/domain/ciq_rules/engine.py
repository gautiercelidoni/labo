"""Moteur d'évaluation CIQ : construit les séquences puis applique les règles du mode.

Spécification détaillée : docs/regles-ciq.md.
"""
from __future__ import annotations

from typing import Iterable, Mapping, Sequence

from app.domain.ciq_rules import shewhart, westgard
from app.domain.ciq_rules.types import (
    DEFAULT_SEVERITIES,
    RULE_LABELS,
    Evaluation,
    NotEvaluated,
    Point,
    RuleHit,
    Severity,
    rules_for_mode,
)


def _usable(points: Iterable[Point], mode: str) -> list[Point]:
    return [p for p in points if not p.voided and p.mode == mode]


def level_sequence(point: Point, history: Sequence[Point], chain_lots: bool = False) -> list[Point]:
    """Points antérieurs du même niveau, puis le point évalué (dernier élément).

    Sans enchaînement des lots, la séquence commence au dernier changement de lot du niveau.
    """
    previous = sorted(
        (p for p in _usable(history, point.mode)
         if p.level_key == point.level_key and p.key != point.key and p.sort_key < point.sort_key),
        key=lambda p: p.sort_key,
    )
    if not chain_lots:
        kept: list[Point] = []
        for p in reversed(previous):
            if p.lot_key != point.lot_key:
                break
            kept.append(p)
        previous = list(reversed(kept))
    return previous + [point]


def cross_level_sequence(point: Point, history: Sequence[Point], chain_lots: bool = False) -> list[Point]:
    """Points antérieurs de tous les niveaux du même mode (ordre chronologique puis ordre des niveaux).

    Sans enchaînement des lots, la séquence s'arrête au premier changement de lot rencontré
    (en remontant le temps) sur n'importe quel niveau.
    """
    previous = sorted(
        (p for p in _usable(history, point.mode) if p.key != point.key and p.sort_key < point.sort_key),
        key=lambda p: p.sort_key,
    )
    points = previous + [point]
    if chain_lots:
        return points
    lots: dict[str, str] = {}
    kept: list[Point] = []
    for p in reversed(points):
        expected = lots.setdefault(p.level_key, p.lot_key)
        if expected != p.lot_key:
            # La série où l'ancien lot apparaît est entièrement exclue (frontière = changement de lot).
            kept = [k for k in kept if k.run_key != p.run_key or k.key == point.key]
            break
        kept.append(p)
    return list(reversed(kept))


def run_peers(point: Point, history: Sequence[Point]) -> list[Point]:
    """Autres niveaux de la même série analytique (quel que soit leur ordre de saisie)."""
    return [
        p for p in _usable(history, point.mode)
        if p.run_key == point.run_key and p.level_key != point.level_key and p.key != point.key
    ]


def resolve_severities(mode: str, configured: Mapping[str, str] | None) -> dict[str, Severity]:
    configured = configured or {}
    result: dict[str, Severity] = {}
    for rule in rules_for_mode(mode):  # type: ignore[arg-type]
        value = configured.get(rule, DEFAULT_SEVERITIES[rule])
        result[rule] = value if value in ("off", "warning", "reject") else DEFAULT_SEVERITIES[rule]  # type: ignore[assignment]
    return result


def evaluate(
    point: Point,
    history: Sequence[Point],
    severities: Mapping[str, str] | None = None,
    chain_lots: bool = False,
) -> Evaluation:
    """Évalue un point.

    `history` contient les points antérieurs du paramètre (tous niveaux) ET les autres points de
    la même série ; les points annulés et ceux d'un autre mode sont ignorés.
    """
    resolved = resolve_severities(point.mode, severities)
    evaluation = Evaluation(point_key=point.key, z=point.z)
    level_seq = level_sequence(point, history, chain_lots)
    if point.mode == "westgard":
        context = westgard.Context(
            point=point,
            level_seq=level_seq,
            cross_seq=cross_level_sequence(point, history, chain_lots),
            peers=run_peers(point, history),
        )
        checks = westgard.CHECKS
    else:
        context = shewhart.Context(point=point, level_seq=level_seq)
        checks = shewhart.CHECKS
    for rule, check in checks.items():
        severity = resolved[rule]
        if severity == "off":
            continue
        outcome = check(context)
        if outcome.hit_keys:
            evaluation.hits.append(RuleHit(rule, severity, RULE_LABELS[rule], tuple(outcome.hit_keys)))
        elif outcome.not_evaluated:
            evaluation.not_evaluated.append(NotEvaluated(rule, outcome.not_evaluated))
    return evaluation
