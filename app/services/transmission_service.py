"""Cahier de transmission (V1.1) : envoi, accusés de lecture, suivi, archivage (jamais de suppression)."""
from __future__ import annotations

import uuid
from datetime import date

import sqlalchemy as sa
from flask import g
from flask_login import current_user
from werkzeug.datastructures import FileStorage as UploadedFile

from app.extensions import db
from app.models.base import utcnow
from app.models.tenant import Membership, Team
from app.models.transmissions import (
    CATEGORIES,
    PRIORITIES,
    TRANSMISSION_STATUSES,
    Transmission,
    TransmissionComment,
    TransmissionReadReceipt,
    TransmissionRecipient,
)
from app.repositories.base import repo
from app.security.permissions import P, can
from app.services import audit_service
from app.services.file_storage import store_upload
from app.services.membership_service import is_member

# Transitions de statut autorisées manuellement (« lu » est posé automatiquement à la lecture).
TRANSITIONS = {
    "new": {"in_progress", "done", "archived"},
    "read": {"in_progress", "done", "archived"},
    "in_progress": {"done", "archived"},
    "done": {"in_progress", "archived"},
    "archived": set(),
}


class TransmissionError(ValueError):
    pass


def _my_team_id() -> uuid.UUID | None:
    membership = getattr(g, "membership", None)
    return membership.team_id if membership else None


def _addressed_to_me():
    """Condition SQL : transmission adressée à l'utilisateur, directement ou via son équipe."""
    team_id = _my_team_id()
    conditions = [TransmissionRecipient.user_id == current_user.id]
    if team_id is not None:
        conditions.append(TransmissionRecipient.team_id == team_id)
    return sa.exists(
        sa.select(TransmissionRecipient.id).where(TransmissionRecipient.transmission_id == Transmission.id,
                                                  sa.or_(*conditions))
    )


def visible_condition():
    if can(P.TRANSMISSION_VIEW_ALL):
        return sa.true()
    return sa.or_(Transmission.author_id == current_user.id, _addressed_to_me())


def can_view(t: Transmission) -> bool:
    if can(P.TRANSMISSION_VIEW_ALL) or t.author_id == current_user.id:
        return True
    return db.session.scalar(sa.select(sa.func.count()).select_from(Transmission)
                             .where(Transmission.id == t.id, _addressed_to_me())) > 0


def is_recipient(t: Transmission) -> bool:
    return db.session.scalar(sa.select(sa.func.count()).select_from(Transmission)
                             .where(Transmission.id == t.id, _addressed_to_me())) > 0


def _read_by_me():
    return sa.exists(
        sa.select(TransmissionReadReceipt.id).where(TransmissionReadReceipt.transmission_id == Transmission.id,
                                                    TransmissionReadReceipt.user_id == current_user.id)
    )


def list_query(box: str, *, status: str | None = None, priority: str | None = None, category: str | None = None,
               unread_only: bool = False) -> sa.Select:
    stmt = sa.select(Transmission)
    if box == "envoyees":
        stmt = stmt.where(Transmission.author_id == current_user.id)
    elif box == "toutes" and can(P.TRANSMISSION_VIEW_ALL):
        pass
    else:
        stmt = stmt.where(_addressed_to_me())
    if status in TRANSMISSION_STATUSES:
        stmt = stmt.where(Transmission.status == status)
    elif status != "archived":
        stmt = stmt.where(Transmission.status != "archived")
    if priority in PRIORITIES:
        stmt = stmt.where(Transmission.priority == priority)
    if category in CATEGORIES:
        stmt = stmt.where(Transmission.category == category)
    if unread_only:
        stmt = stmt.where(_addressed_to_me(), ~_read_by_me())
    priority_rank = sa.case({"urgent": 0, "important": 1}, value=Transmission.priority, else_=2)
    return stmt.order_by(priority_rank, Transmission.created_at.desc())


def unread_count() -> int:
    stmt = sa.select(sa.func.count()).select_from(Transmission).where(
        Transmission.status != "archived", _addressed_to_me(), ~_read_by_me())
    return db.session.scalar(stmt) or 0


def urgent_open_count() -> int:
    stmt = sa.select(sa.func.count()).select_from(Transmission).where(
        Transmission.priority == "urgent", Transmission.status.in_(("new", "read", "in_progress")),
        visible_condition())
    return db.session.scalar(stmt) or 0


def read_ids(ids: list[uuid.UUID]) -> set[uuid.UUID]:
    if not ids:
        return set()
    return set(db.session.scalars(sa.select(TransmissionReadReceipt.transmission_id).where(
        TransmissionReadReceipt.transmission_id.in_(ids), TransmissionReadReceipt.user_id == current_user.id)).all())


def create(*, title: str, body: str, category: str, priority: str, due_on: date | None,
           user_ids: list[uuid.UUID], team_ids: list[uuid.UUID], attachments: list[UploadedFile]) -> Transmission:
    if category not in CATEGORIES or priority not in PRIORITIES:
        raise TransmissionError("Catégorie ou priorité invalide.")
    if not user_ids and not team_ids:
        raise TransmissionError("Choisissez au moins un destinataire (personne ou équipe / poste).")
    for uid in user_ids:
        if not is_member(uid):
            raise TransmissionError("Destinataire inconnu.")
    for tid in team_ids:
        if repo(Team).get(tid) is None:
            raise TransmissionError("Équipe inconnue.")
    t = Transmission(author_id=current_user.id, title=title.strip(), body=body.strip(), category=category,
                     priority=priority, due_on=due_on, status="new")
    db.session.add(t)
    db.session.flush()
    for uid in dict.fromkeys(user_ids):
        db.session.add(TransmissionRecipient(transmission_id=t.id, user_id=uid))
    for tid in dict.fromkeys(team_ids):
        db.session.add(TransmissionRecipient(transmission_id=t.id, team_id=tid))
    for f in attachments:
        if f and f.filename:
            store_upload(f, "transmission", t.id)
    audit_service.record("transmission.created", t,
                         after=audit_service.snapshot(t) | {"users": [str(u) for u in user_ids],
                                                            "teams": [str(x) for x in team_ids]})
    db.session.commit()
    return t


def mark_read(t: Transmission) -> bool:
    """Accusé de lecture horodaté, créé une seule fois, uniquement pour un destinataire."""
    if not getattr(g, "write_allowed", False) or not is_recipient(t):
        return False
    exists = repo(TransmissionReadReceipt).first(TransmissionReadReceipt.transmission_id == t.id,
                                                 TransmissionReadReceipt.user_id == current_user.id)
    if exists is not None:
        return False
    receipt = TransmissionReadReceipt(transmission_id=t.id, user_id=current_user.id, read_at=utcnow())
    db.session.add(receipt)
    if t.status == "new":
        t.status = "read"
    db.session.flush()
    audit_service.record("transmission.read", t, after={"read_at": receipt.read_at})
    db.session.commit()
    return True


def change_status(t: Transmission, status: str, comment: str | None = None) -> None:
    if status not in TRANSITIONS.get(t.status, set()):
        raise TransmissionError("Changement de statut non autorisé.")
    if not (t.author_id == current_user.id or is_recipient(t) or can(P.TRANSMISSION_VIEW_ALL)):
        raise TransmissionError("Vous n'êtes ni l'auteur ni un destinataire.")
    before = audit_service.snapshot(t, ["status", "archived_at"])
    t.status = status
    if status == "archived":
        t.archived_at = utcnow()
    if comment and comment.strip():
        db.session.add(TransmissionComment(transmission_id=t.id, author_id=current_user.id, body=comment.strip()))
    audit_service.record("transmission.status_changed", t, before=before,
                         after={"status": status, "archived_at": t.archived_at}, reason=comment)
    db.session.commit()


def add_comment(t: Transmission, body: str) -> TransmissionComment:
    if not body.strip():
        raise TransmissionError("Le commentaire est vide.")
    comment = TransmissionComment(transmission_id=t.id, author_id=current_user.id, body=body.strip())
    db.session.add(comment)
    db.session.flush()
    audit_service.record("transmission.commented", t, after={"comment": comment.body[:500]})
    db.session.commit()
    return comment


def receipts(t: Transmission) -> list[TransmissionReadReceipt]:
    return list(repo(TransmissionReadReceipt).all(TransmissionReadReceipt.transmission_id == t.id,
                                                  order_by=TransmissionReadReceipt.read_at))


def comments(t: Transmission) -> list[TransmissionComment]:
    return list(repo(TransmissionComment).all(TransmissionComment.transmission_id == t.id,
                                              order_by=TransmissionComment.created_at))


def expected_readers(t: Transmission) -> list[uuid.UUID]:
    """Utilisateurs destinataires (directs et membres actifs des équipes)."""
    recipients = repo(TransmissionRecipient).all(TransmissionRecipient.transmission_id == t.id)
    users = {r.user_id for r in recipients if r.user_id}
    team_ids = [r.team_id for r in recipients if r.team_id]
    if team_ids:
        users |= set(db.session.scalars(sa.select(Membership.user_id).where(
            Membership.team_id.in_(team_ids), Membership.is_active.is_(True))).all())
    return list(users)
