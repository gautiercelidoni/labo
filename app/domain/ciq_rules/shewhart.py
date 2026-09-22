"""Règles des cartes de contrôle de Shewhart (ISO 7870-2, guide Nordtest TR 569).

Limites de surveillance ±2s, limites d'action ±3s. Les séquences sont évaluées par niveau
(une carte par échantillon de contrôle).
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Sequence

from app.domain.ciq_rules.types import PASSED, Outcome, Point, insufficient, side


@dataclass(frozen=True)
class Context:
    point: Point
    level_seq: list[Point]


def rule_a(ctx: Context) -> Outcome:
    """Un point hors des limites d'action."""
    return Outcome((ctx.point.key,)) if side(ctx.point.z, 3) else PASSED


def _k_of_n(ctx: Context, k: int, n: int, threshold: int) -> Outcome:
    """k points sur les n derniers (point évalué inclus) au-delà du seuil, du même côté que lui."""
    s = side(ctx.point.z, threshold)
    if not s:
        return PASSED
    window = ctx.level_seq[-n:]
    matching = [p for p in window if side(p.z, threshold) == s]
    if len(matching) >= k:
        return Outcome(tuple(p.key for p in matching))
    missing = n - len(window)
    if len(matching) + missing >= k:
        return insufficient(f"moins de {n} points disponibles")
    return PASSED


def rule_b(ctx: Context) -> Outcome:
    return _k_of_n(ctx, 2, 3, 2)


def rule_c(ctx: Context) -> Outcome:
    return _k_of_n(ctx, 7, 7, 0)


def rule_d(ctx: Context) -> Outcome:
    return _k_of_n(ctx, 10, 11, 0)


CHECKS: dict[str, Callable[[Context], Outcome]] = {
    "shewhart_a": rule_a,
    "shewhart_b": rule_b,
    "shewhart_c": rule_c,
    "shewhart_d": rule_d,
}


@dataclass(frozen=True)
class ComputedLimits:
    mean: Decimal
    sd: Decimal
    n: int
    sufficient: bool


def compute_limits(values: Sequence[Decimal], min_points: int) -> ComputedLimits:
    """Moyenne et écart-type (n-1) de la période de référence, calculés en Decimal."""
    if len(values) < 2:
        raise ValueError("Au moins deux valeurs sont nécessaires pour calculer un écart-type.")
    vals = [Decimal(v) for v in values]
    mean = sum(vals, Decimal(0)) / len(vals)
    sd = statistics.stdev(vals, xbar=mean)
    if sd <= 0:
        raise ValueError("Écart-type nul : les valeurs de référence sont toutes identiques.")
    q = Decimal("0.000001")
    return ComputedLimits(mean=mean.quantize(q), sd=Decimal(sd).quantize(q), n=len(vals),
                          sufficient=len(vals) >= min_points)
