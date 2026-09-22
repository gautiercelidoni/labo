"""Droit d'écriture d'un laboratoire selon son statut et son abonnement.

Calculé à chaque requête : l'expiration d'un essai ou d'une période de grâce prend effet
immédiatement, sans dépendre du cron. Les données ne sont jamais supprimées : le laboratoire
passe simplement en lecture seule.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.models.base import utcnow
from app.models.billing import Subscription
from app.models.tenant import Laboratory


@dataclass(frozen=True)
class AccessState:
    write_allowed: bool
    reason: str | None = None


def compute_access(lab: Laboratory, sub: Subscription | None, now: datetime | None = None) -> AccessState:
    now = now or utcnow()
    if lab.status == "suspended":
        return AccessState(False, "Laboratoire suspendu par l'administrateur de la plateforme.")
    if lab.status == "read_only":
        return AccessState(False, "Laboratoire en lecture seule.")
    if sub is None:
        return AccessState(False, "Aucun abonnement actif.")
    if sub.status == "trialing":
        if sub.trial_ends_at and sub.trial_ends_at > now:
            return AccessState(True)
        return AccessState(False, "La période d'essai est terminée.")
    if sub.status == "active":
        return AccessState(True)
    if sub.status == "past_due":
        if sub.grace_until and sub.grace_until > now:
            return AccessState(True)
        return AccessState(False, "Paiement en retard : période de grâce expirée.")
    if sub.status == "canceled":
        if sub.current_period_end and sub.current_period_end > now:
            return AccessState(True)
        return AccessState(False, "Abonnement résilié.")
    return AccessState(False, "Abonnement suspendu.")
