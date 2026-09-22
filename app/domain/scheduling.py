"""Calcul des échéances métrologiques (fonctions pures)."""
from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Literal

DueState = Literal["overdue", "due_today", "due_7", "due_30", "ok", "none"]

DUE_STATE_LABELS = {
    "overdue": "En retard",
    "due_today": "Aujourd'hui",
    "due_7": "Sous 7 jours",
    "due_30": "Sous 30 jours",
    "ok": "À jour",
    "none": "Non planifié",
}

# Seuils d'alerte : J-30, J-7, J0 et retard.
ALERT_THRESHOLDS = (30, 7, 0)


def add_months(start: date, months: int) -> date:
    """Ajoute des mois en ramenant au dernier jour du mois si nécessaire (31/01 + 1 mois = 28 ou 29/02)."""
    month_index = start.month - 1 + months
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def add_period(start: date, value: int, unit: str) -> date:
    if value <= 0:
        raise ValueError("La périodicité doit être strictement positive.")
    if unit == "day":
        return start + timedelta(days=value)
    if unit == "week":
        return start + timedelta(weeks=value)
    if unit == "month":
        return add_months(start, value)
    if unit == "year":
        return add_months(start, 12 * value)
    raise ValueError(f"Unité de périodicité inconnue : {unit}")


def next_due(last_done: date | None, value: int, unit: str) -> date | None:
    """Prochaine échéance calculée depuis la date EFFECTIVE de la dernière réalisation."""
    if last_done is None:
        return None
    return add_period(last_done, value, unit)


def due_state(next_due_on: date | None, today: date) -> DueState:
    if next_due_on is None:
        return "none"
    delta = (next_due_on - today).days
    if delta < 0:
        return "overdue"
    if delta == 0:
        return "due_today"
    if delta <= 7:
        return "due_7"
    if delta <= 30:
        return "due_30"
    return "ok"


def alert_threshold(next_due_on: date | None, today: date) -> str | None:
    """Seuil d'alerte atteint (clé de déduplication des notifications) : 'J-30', 'J-7', 'J0', 'retard'."""
    state = due_state(next_due_on, today)
    return {"overdue": "retard", "due_today": "J0", "due_7": "J-7", "due_30": "J-30"}.get(state)
