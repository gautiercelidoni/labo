"""Actions correctives : création, réalisation, validation."""
from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from flask_login import current_user

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.base import utcnow
from app.security.permissions import P, can
from app.services import audit_service
from app.services.membership_service import is_member


class ActionError(ValueError):
    pass


def create(*, source_type: str, source_id: uuid.UUID | None, description: str,
           responsible_id: uuid.UUID | None, due_on: date | None) -> CorrectiveAction:
    if not description.strip():
        raise ActionError("La description est obligatoire.")
    if responsible_id is not None and not is_member(responsible_id):
        raise ActionError("Responsable inconnu.")
    action = CorrectiveAction(source_type=source_type, source_id=source_id, description=description.strip(),
                              responsible_id=responsible_id, due_on=due_on, status="open",
                              created_by_id=current_user.id)
    db.session.add(action)
    db.session.flush()
    audit_service.record("corrective_action.created", action, after=audit_service.snapshot(action))
    db.session.commit()
    return action


def can_complete(action: CorrectiveAction) -> bool:
    """Le responsable désigné, ou un rôle autorisé à valider, peut déclarer l'action réalisée."""
    if action.status != "open":
        return False
    if can(P.CA_VALIDATE):
        return True
    return can(P.CA_COMPLETE) and action.responsible_id == current_user.id


def complete(action: CorrectiveAction, done_on: date, comment: str) -> None:
    if not can_complete(action):
        raise ActionError("Vous ne pouvez pas déclarer cette action réalisée.")
    if not comment.strip():
        raise ActionError("Décrivez ce qui a été réalisé.")
    before = audit_service.snapshot(action)
    action.status = "done"
    action.done_on = done_on
    action.done_comment = comment.strip()
    audit_service.record_change("corrective_action.completed", action, before)
    db.session.commit()


def validate(action: CorrectiveAction, comment: str | None = None) -> None:
    if not can(P.CA_VALIDATE):
        raise ActionError("Seul un responsable qualité ou un administrateur peut valider une action.")
    if action.status != "done":
        raise ActionError("L'action doit être réalisée avant d'être validée.")
    before = audit_service.snapshot(action)
    action.status = "validated"
    action.validated_by_id = current_user.id
    action.validated_at = utcnow()
    audit_service.record_change("corrective_action.validated", action, before, reason=(comment or "").strip() or None)
    db.session.commit()


def reopen(action: CorrectiveAction, reason: str) -> None:
    if not can(P.CA_VALIDATE):
        raise ActionError("Action non autorisée.")
    if action.status == "open":
        raise ActionError("L'action est déjà ouverte.")
    if not reason.strip():
        raise ActionError("Le motif est obligatoire.")
    before = audit_service.snapshot(action)
    action.status = "open"
    action.validated_at = None
    action.validated_by_id = None
    audit_service.record_change("corrective_action.reopened", action, before, reason=reason.strip())
    db.session.commit()


def list_query(status: str | None = None, overdue_on: date | None = None, responsible_id: uuid.UUID | None = None):
    stmt = sa.select(CorrectiveAction).order_by(CorrectiveAction.due_on.asc().nulls_last(),
                                                CorrectiveAction.created_at.desc())
    if status:
        stmt = stmt.where(CorrectiveAction.status == status)
    if overdue_on:
        stmt = stmt.where(CorrectiveAction.status == "open", CorrectiveAction.due_on < overdue_on)
    if responsible_id:
        stmt = stmt.where(CorrectiveAction.responsible_id == responsible_id)
    return stmt
