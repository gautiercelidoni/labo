"""Construction de points de test pour le moteur de règles (sans base de données)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.domain.ciq_rules.types import Point

T0 = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
MEAN = Decimal("100")
SD = Decimal("2")


def pt(day: int, z, level: str = "N1", *, run: str | None = None, lot: str = "L1", limit: str = "LIM1",
       mode: str = "westgard", voided: bool = False, mean: Decimal = MEAN, sd: Decimal = SD) -> Point:
    """Point du jour `day` (une série par jour), de z-score exact `z` (valeur = cible + z × s)."""
    value = mean + Decimal(str(z)) * sd
    return Point(
        key=f"{level}-{day}-{lot}-{limit}",
        run_key=run or f"run-{day}",
        level_key=level,
        level_order={"N1": 1, "N2": 2, "N3": 3}.get(level, 1),
        lot_key=lot,
        limit_key=limit,
        at=T0 + timedelta(days=day),
        value=value,
        mean=mean,
        sd=sd,
        mode=mode,  # type: ignore[arg-type]
        voided=voided,
    )


def series(zs, level: str = "N1", start: int = 0, **kw) -> list[Point]:
    return [pt(start + i, z, level, **kw) for i, z in enumerate(zs)]


def not_evaluated(evaluation) -> set[str]:
    return {n.rule for n in evaluation.not_evaluated}
