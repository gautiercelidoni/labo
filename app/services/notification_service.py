"""Notifications : génération par le cron (idempotente), consultation et emails récapitulatifs."""
from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from flask import current_app
from flask_login import current_user
from sqlalchemy.dialects.postgresql import insert

from app.domain.scheduling import alert_threshold
from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.base import utcnow
from app.models.ciq import CIQParameter, CIQRun
from app.models.equipment import Equipment, MaintenancePlan
from app.models.notifications import Notification
from app.models.tenant import Laboratory, Membership
from app.models.user import User
from app.repositories.base import repo
from app.security.tenancy import current_tenant_id, system_context, tenant_context
from app.services.email_service import EmailError, external_url, send_email


def unread_count() -> int:
    try:
        return repo(Notification).count(Notification.user_id == current_user.id, Notification.read_at.is_(None))
    except Exception:  # l'en-tête ne doit jamais casser une page
        db.session.rollback()
        return 0


def list_query(unread_only: bool = False) -> sa.Select:
    stmt = sa.select(Notification).where(Notification.user_id == current_user.id).order_by(Notification.created_at.desc())
    if unread_only:
        stmt = stmt.where(Notification.read_at.is_(None))
    return stmt


def mark_read(notification: Notification) -> None:
    if notification.user_id != current_user.id:
        return
    if notification.read_at is None:
        notification.read_at = utcnow()
        db.session.commit()


def mark_all_read() -> int:
    result = db.session.execute(
        sa.update(Notification)
        .where(Notification.user_id == current_user.id, Notification.read_at.is_(None))
        .values(read_at=utcnow())
    )
    db.session.commit()
    return result.rowcount or 0


def _members_by_role() -> tuple[set[uuid.UUID], set[uuid.UUID]]:
    rows = db.session.execute(
        sa.select(Membership.user_id, Membership.role).where(Membership.is_active.is_(True))
    ).all()
    managers = {uid for uid, role in rows if role in ("admin", "quality")}
    members = {uid for uid, _ in rows}
    return managers, members


def _push(user_id: uuid.UUID, *, kind: str, dedup: str, message: str, level: str, link: str,
          object_type: str, object_id: uuid.UUID) -> bool:
    stmt = (
        insert(Notification)
        .values(id=uuid.uuid4(), tenant_id=current_tenant_id(), user_id=user_id, kind=kind,
                object_type=object_type, object_id=object_id, level=level, message=message, link=link,
                dedup_key=f"{dedup}:{user_id}", created_at=utcnow(), updated_at=utcnow())
        .on_conflict_do_nothing(index_elements=["tenant_id", "dedup_key"])
        .returning(Notification.id)
    )
    return db.session.execute(stmt).first() is not None


THRESHOLD_TEXT = {"J-30": "dans moins de 30 jours", "J-7": "dans moins de 7 jours", "J0": "aujourd'hui",
                  "retard": "en retard"}


def generate_for_current_lab(today: date) -> int:
    """Crée les notifications du laboratoire courant. Idempotent : clé de déduplication unique."""
    managers, members = _members_by_role()
    created = 0
    plans = db.session.execute(
        sa.select(MaintenancePlan, Equipment)
        .join(Equipment, Equipment.id == MaintenancePlan.equipment_id)
        .where(MaintenancePlan.is_active.is_(True), Equipment.archived_at.is_(None),
               MaintenancePlan.next_due_on.is_not(None))
    ).all()
    for plan, equipment in plans:
        threshold = alert_threshold(plan.next_due_on, today)
        if threshold is None:
            continue
        recipients = set(managers)
        if plan.responsible_id in members:
            recipients.add(plan.responsible_id)
        level = "danger" if threshold == "retard" else ("warning" if threshold in ("J0", "J-7") else "info")
        message = (f"{plan.event_type_label} de « {equipment.name} » ({equipment.internal_id}) "
                   f"{THRESHOLD_TEXT[threshold]} : échéance le {plan.next_due_on.strftime('%d/%m/%Y')}.")
        for uid in recipients:
            created += _push(uid, kind="metrology_due", dedup=f"plan:{plan.id}:{plan.next_due_on}:{threshold}",
                             message=message, level=level, link=f"/metrologie/equipements/{equipment.id}",
                             object_type="maintenance_plan", object_id=plan.id)
    actions = repo(CorrectiveAction).all(CorrectiveAction.status == "open", CorrectiveAction.due_on < today)
    for action in actions:
        recipients = set(managers)
        if action.responsible_id in members:
            recipients.add(action.responsible_id)
        for uid in recipients:
            created += _push(uid, kind="action_overdue", dedup=f"ca:{action.id}:{action.due_on}:retard",
                             message=f"Action corrective en retard (échéance {action.due_on.strftime('%d/%m/%Y')}) : "
                                     f"{action.description[:120]}",
                             level="danger", link=f"/actions-correctives/{action.id}",
                             object_type="corrective_action", object_id=action.id)
    runs = db.session.execute(
        sa.select(CIQRun, CIQParameter.name).join(CIQParameter, CIQParameter.id == CIQRun.parameter_id)
        .where(CIQRun.status == "rejected")
    ).all()
    for run, name in runs:
        for uid in managers:
            created += _push(uid, kind="ciq_rejected", dedup=f"run:{run.id}:rejete",
                             message=f"Série CIQ rejetée non traitée : {name}.", level="danger",
                             link=f"/ciq/series/{run.id}", object_type="ciq_run", object_id=run.id)
    db.session.flush()
    return created


def send_pending_emails(lab: Laboratory) -> int:
    """Un email récapitulatif par utilisateur pour les notifications non encore envoyées."""
    pending = repo(Notification).all(Notification.emailed_at.is_(None), Notification.read_at.is_(None),
                                     order_by=Notification.created_at)
    by_user: dict[uuid.UUID, list[Notification]] = defaultdict(list)
    for n in pending:
        if n.user_id:
            by_user[n.user_id].append(n)
    sent = 0
    for uid, items in by_user.items():
        user = db.session.scalar(sa.select(User).where(User.id == uid))
        if user is None or not user.is_active:
            continue
        if lab.notify_by_email:
            try:
                send_email(user.email, f"[{lab.name}] {len(items)} alerte(s) qualité", "notifications",
                           user=user, lab=lab, items=items, link=external_url("/notifications/"))
            except EmailError:
                continue
            sent += 1
        for n in items:
            n.emailed_at = utcnow()
    return sent


def run_all(today_override: date | None = None) -> dict:
    """Commande cron : parcourt tous les laboratoires (contexte système pour la liste seulement)."""
    from app.security.rate_limit import purge_old_attempts

    stats = {"laboratoires": 0, "notifications": 0, "emails": 0}
    with system_context():
        labs = db.session.scalars(sa.select(Laboratory).where(Laboratory.status != "suspended")).all()
        purged = purge_old_attempts()
        db.session.commit()
    stats["tentatives_purgees"] = purged
    for lab in labs:
        with tenant_context(lab.id):
            try:
                today = today_override or datetime.now(ZoneInfo(lab.timezone)).date()
            except Exception:
                today = today_override or date.today()
            try:
                stats["notifications"] += generate_for_current_lab(today)
                stats["emails"] += send_pending_emails(lab)
                db.session.commit()
            except Exception:
                db.session.rollback()
                current_app.logger.exception("Échec de génération des notifications pour %s", lab.id)
                continue
        stats["laboratoires"] += 1
    return stats
