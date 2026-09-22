"""Types du moteur de règles CIQ (aucune dépendance à Flask ni à la base)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

Severity = Literal["off", "warning", "reject"]
Mode = Literal["westgard", "shewhart"]

WESTGARD_RULES = ("1-2s", "1-3s", "2-2s", "R-4s", "4-1s", "10x")
SHEWHART_RULES = ("shewhart_a", "shewhart_b", "shewhart_c", "shewhart_d")

RULE_LABELS = {
    "1-2s": "1-2s : un point au-delà de ±2s",
    "1-3s": "1-3s : un point au-delà de ±3s",
    "2-2s": "2-2s : deux points consécutifs au-delà de 2s du même côté",
    "R-4s": "R-4s : un niveau au-delà de +2s et un autre au-delà de −2s dans la même série",
    "4-1s": "4-1s : quatre points consécutifs au-delà de 1s du même côté",
    "10x": "10x : dix points consécutifs du même côté de la moyenne",
    "shewhart_a": "Shewhart A : un point hors des limites d'action (±3s)",
    "shewhart_b": "Shewhart B : 2 points sur 3 consécutifs hors des limites de surveillance (±2s) du même côté",
    "shewhart_c": "Shewhart C : 7 points consécutifs du même côté de la moyenne",
    "shewhart_d": "Shewhart D : 10 points sur 11 consécutifs du même côté de la moyenne",
}

DEFAULT_SEVERITIES: dict[str, Severity] = {
    "1-2s": "warning",
    "1-3s": "reject",
    "2-2s": "reject",
    "R-4s": "reject",
    # Décision validée : avertissement par défaut (pratique courante), configurable en rejet.
    "4-1s": "warning",
    "10x": "warning",
    "shewhart_a": "reject",
    "shewhart_b": "reject",
    "shewhart_c": "warning",
    "shewhart_d": "warning",
}

SEVERITY_RANK = {"off": 0, "warning": 1, "reject": 2}


def rules_for_mode(mode: Mode) -> tuple[str, ...]:
    return WESTGARD_RULES if mode == "westgard" else SHEWHART_RULES


def side(z: Decimal, threshold: Decimal | int) -> int:
    """+1 si z > seuil, -1 si z < -seuil, 0 sinon. Inégalités strictes : z == seuil ne compte pas."""
    if z > threshold:
        return 1
    if z < -threshold:
        return -1
    return 0


@dataclass(frozen=True)
class Point:
    """Un résultat de contrôle, avec la cible et l'écart-type de SON jeu de limites."""

    key: str
    run_key: str
    level_key: str
    level_order: int
    lot_key: str
    limit_key: str
    at: datetime
    value: Decimal
    mean: Decimal
    sd: Decimal
    mode: Mode = "westgard"
    voided: bool = False
    seq: int = 0  # ordre de saisie, départage les points de même horodatage

    @property
    def z(self) -> Decimal:
        # Calcul exact en Decimal : une valeur exactement à 2s donne z == 2 (pas d'arrondi flottant).
        return (self.value - self.mean) / self.sd

    @property
    def deviation(self) -> Decimal:
        return self.value - self.mean

    @property
    def sort_key(self) -> tuple:
        return (self.at, self.run_key, self.level_order, self.seq)


@dataclass(frozen=True)
class RuleHit:
    rule: str
    severity: Severity
    message: str
    point_keys: tuple[str, ...]


@dataclass(frozen=True)
class NotEvaluated:
    rule: str
    reason: str


@dataclass
class Evaluation:
    point_key: str
    z: Decimal
    hits: list[RuleHit] = field(default_factory=list)
    not_evaluated: list[NotEvaluated] = field(default_factory=list)

    @property
    def status(self) -> str:
        worst = max((SEVERITY_RANK[h.severity] for h in self.hits), default=0)
        return {0: "accepted", 1: "warning", 2: "rejected"}[worst]

    @property
    def triggered(self) -> list[str]:
        return [h.rule for h in self.hits]


@dataclass(frozen=True)
class Outcome:
    """Résultat d'une règle : points impliqués si déclenchée, ou motif de non-évaluation."""

    hit_keys: tuple[str, ...] = ()
    not_evaluated: str | None = None


PASSED = Outcome()


def insufficient(reason: str = "historique insuffisant") -> Outcome:
    return Outcome(not_evaluated=reason)
