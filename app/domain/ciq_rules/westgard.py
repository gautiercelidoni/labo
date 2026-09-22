"""Règles de Westgard (usage biologie).

Chaque fonction reçoit le contexte (séquences déjà construites) et renvoie un Outcome :
points impliqués si la règle est déclenchée, motif si elle n'a pas pu être évaluée.
Une règle n'est « non évaluée » que si davantage d'historique aurait pu la déclencher.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from app.domain.ciq_rules.types import PASSED, Outcome, Point, insufficient, side


@dataclass(frozen=True)
class Context:
    point: Point
    level_seq: list[Point]  # même niveau, dernier élément = point évalué
    cross_seq: list[Point]  # tous niveaux, dernier élément = point évalué
    peers: list[Point]  # autres niveaux de la même série


def rule_1_2s(ctx: Context) -> Outcome:
    return Outcome((ctx.point.key,)) if side(ctx.point.z, 2) else PASSED


def rule_1_3s(ctx: Context) -> Outcome:
    return Outcome((ctx.point.key,)) if side(ctx.point.z, 3) else PASSED


def rule_2_2s(ctx: Context) -> Outcome:
    s = side(ctx.point.z, 2)
    if not s:
        return PASSED
    # Inter-séries : point précédent du même niveau.
    if len(ctx.level_seq) >= 2:
        previous = ctx.level_seq[-2]
        if side(previous.z, 2) == s:
            return Outcome((previous.key, ctx.point.key))
    # Intra-série : un autre niveau de la même série.
    for peer in ctx.peers:
        if side(peer.z, 2) == s:
            return Outcome((peer.key, ctx.point.key))
    if len(ctx.level_seq) < 2 and not ctx.peers:
        return insufficient("aucun point précédent ni autre niveau dans la série")
    return PASSED


def rule_r_4s(ctx: Context) -> Outcome:
    s = side(ctx.point.z, 2)
    if not ctx.peers:
        return insufficient("un seul niveau dans la série") if s else PASSED
    if not s:
        return PASSED
    for peer in ctx.peers:
        if side(peer.z, 2) == -s:
            return Outcome((peer.key, ctx.point.key))
    return PASSED


def _consecutive(seq: list[Point], n: int, threshold: int) -> tuple[str, ...] | None:
    """Les n derniers points de la séquence sont-ils tous au-delà du seuil, du même côté ?"""
    if len(seq) < n:
        return None
    window = seq[-n:]
    s = side(window[-1].z, threshold)
    if s and all(side(p.z, threshold) == s for p in window):
        return tuple(p.key for p in window)
    return None


def _run_length(seq: list[Point], threshold: int) -> int:
    """Longueur de la série terminale de points du même côté que le dernier."""
    s = side(seq[-1].z, threshold)
    if not s:
        return 0
    n = 0
    for p in reversed(seq):
        if side(p.z, threshold) != s:
            break
        n += 1
    return n


def _sequence_rule(ctx: Context, n: int, threshold: int) -> Outcome:
    if not side(ctx.point.z, threshold):
        return PASSED
    for seq in (ctx.level_seq, ctx.cross_seq):
        keys = _consecutive(seq, n, threshold)
        if keys:
            return Outcome(keys)
    # Non déclenchée : insuffisant seulement si la série en cours couvre tout l'historique disponible.
    if any(_run_length(seq, threshold) == len(seq) for seq in (ctx.level_seq, ctx.cross_seq)):
        return insufficient(f"moins de {n} points disponibles")
    return PASSED


def rule_4_1s(ctx: Context) -> Outcome:
    return _sequence_rule(ctx, 4, 1)


def rule_10x(ctx: Context) -> Outcome:
    return _sequence_rule(ctx, 10, 0)


CHECKS: dict[str, Callable[[Context], Outcome]] = {
    "1-2s": rule_1_2s,
    "1-3s": rule_1_3s,
    "2-2s": rule_2_2s,
    "R-4s": rule_r_4s,
    "4-1s": rule_4_1s,
    "10x": rule_10x,
}
