"""Indicateurs du tableau de bord (requêtes agrégées, sans N+1)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import sqlalchemy as sa
from flask_login import current_user

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.audit import AuditEvent
from app.models.base import utcnow
from app.models.ciq import CIQResult, CIQRun
from app.models.equipment import Equipment, MaintenancePlan
from app.models.non_conformities import NonConformity
from app.security.permissions import P, can


@dataclass
class Card:
    key: str
    label: str
    value: int
    link: str
    level: str  # ok | info | warning | danger


def _count(stmt) -> int:
    return db.session.scalar(sa.select(sa.func.count()).select_from(stmt.subquery())) or 0


def cards(today: date) -> list[Card]:
    result: list[Card] = []
    if can(P.CIQ_VIEW):
        since = utcnow() - timedelta(days=7)
        warnings = _count(sa.select(CIQResult.id).where(CIQResult.status == "warning", CIQResult.voided_at.is_(None),
                                                        CIQResult.run_at >= since))
        rejected = _count(sa.select(CIQRun.id).where(CIQRun.status == "rejected"))
        result.append(Card("ciq_warning", "CIQ en alerte (7 derniers jours)", warnings,
                           f"/ciq/historique?statut=warning&du={(today - timedelta(days=7)).isoformat()}",
                           "warning" if warnings else "ok"))
        result.append(Card("ciq_rejected", "Séries CIQ rejetées non traitées", rejected,
                           "/ciq/historique?statut_serie=rejected", "danger" if rejected else "ok"))
    if can(P.METROLOGY_VIEW):
        base = (sa.select(MaintenancePlan.id).join(Equipment, Equipment.id == MaintenancePlan.equipment_id)
                .where(MaintenancePlan.is_active.is_(True), Equipment.archived_at.is_(None)))
        upcoming = _count(base.where(MaintenancePlan.next_due_on >= today,
                                     MaintenancePlan.next_due_on <= today + timedelta(days=30)))
        overdue = _count(base.where(MaintenancePlan.next_due_on < today))
        result.append(Card("metrology_upcoming", "Échéances métrologiques sous 30 jours", upcoming,
                           "/metrologie/echeances?filtre=30", "info" if upcoming else "ok"))
        result.append(Card("metrology_overdue", "Échéances métrologiques dépassées", overdue,
                           "/metrologie/echeances?filtre=retard", "danger" if overdue else "ok"))
    if can(P.CA_VIEW):
        overdue_actions = _count(sa.select(CorrectiveAction.id).where(CorrectiveAction.status == "open",
                                                                      CorrectiveAction.due_on < today))
        result.append(Card("actions_overdue", "Actions correctives en retard", overdue_actions,
                           "/actions-correctives/?retard=1", "danger" if overdue_actions else "ok"))
        mine = _count(sa.select(CorrectiveAction.id).where(CorrectiveAction.status == "open",
                                                           CorrectiveAction.responsible_id == current_user.id))
        result.append(Card("actions_mine", "Mes actions correctives ouvertes", mine,
                           "/actions-correctives/?mes=1&statut=open", "info" if mine else "ok"))
        if can(P.CA_VALIDATE):
            to_validate = _count(sa.select(CorrectiveAction.id).where(CorrectiveAction.status == "done"))
            result.append(Card("actions_to_validate", "Actions réalisées à valider", to_validate,
                               "/actions-correctives/?statut=done", "warning" if to_validate else "ok"))
    if can(P.TRANSMISSION_VIEW):
        from app.services.transmission_service import unread_count, urgent_open_count

        unread = unread_count()
        urgent = urgent_open_count()
        result.append(Card("transmissions_unread", "Transmissions non lues", unread, "/transmissions/?non_lues=1",
                           "warning" if unread else "ok"))
        result.append(Card("transmissions_urgent", "Transmissions urgentes en cours", urgent,
                           "/transmissions/?priorite=urgent", "danger" if urgent else "ok"))
    if can(P.NC_VIEW):
        open_nc = _count(sa.select(NonConformity.id).where(NonConformity.status.not_in(("closed", "cancelled"))))
        result.append(Card("nc_open", "Non-conformités ouvertes", open_nc, "/non-conformites/?statut=ouvertes",
                           "warning" if open_nc else "ok"))
    return result


def recent_activity(limit: int = 12) -> list[AuditEvent]:
    stmt = sa.select(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit)
    if not can(P.AUDIT_VIEW):
        stmt = stmt.where(AuditEvent.user_id == current_user.id)
    return list(db.session.scalars(stmt).all())

