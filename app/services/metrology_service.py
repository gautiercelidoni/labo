"""Parc d'équipements, plans de métrologie et réalisations."""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import sqlalchemy as sa
from flask_login import current_user
from werkzeug.datastructures import FileStorage as UploadedFile

from app.domain.scheduling import add_period, next_due
from app.extensions import db
from app.models.base import utcnow
from app.models.equipment import (
    EQUIPMENT_STATUSES,
    EVENT_TYPES,
    OUTCOMES,
    PERIOD_UNITS,
    Equipment,
    EquipmentCategory,
    MaintenanceEvent,
    MaintenancePlan,
)
from app.repositories.base import repo
from app.services import audit_service
from app.services.file_storage import store_upload
from app.services.membership_service import is_member


class MetrologyError(ValueError):
    pass


EQUIPMENT_FIELDS = ("name", "category_id", "manufacturer", "model", "serial_number", "internal_id", "location",
                    "commissioned_on", "status", "criticality", "responsible_id", "notes")


def _check_refs(data: dict) -> None:
    if data.get("category_id") and repo(EquipmentCategory).get(data["category_id"]) is None:
        raise MetrologyError("Catégorie inconnue.")
    if data.get("responsible_id") and not is_member(data["responsible_id"]):
        raise MetrologyError("Responsable inconnu.")


def save_equipment(equipment: Equipment | None, data: dict) -> Equipment:
    _check_refs(data)
    if data["status"] not in EQUIPMENT_STATUSES:
        raise MetrologyError("Statut invalide.")
    internal_id = data["internal_id"].strip()
    duplicate = repo(Equipment).first(Equipment.internal_id == internal_id)
    if duplicate is not None and (equipment is None or duplicate.id != equipment.id):
        raise MetrologyError("Cet identifiant interne est déjà utilisé.")
    values = {k: (v.strip() if isinstance(v, str) else v) for k, v in data.items() if k in EQUIPMENT_FIELDS}
    values = {k: (v if v != "" else None) for k, v in values.items()}
    values["internal_id"] = internal_id
    if equipment is None:
        equipment = Equipment(**values, created_by_id=current_user.id)
        db.session.add(equipment)
        db.session.flush()
        audit_service.record("equipment.created", equipment, after=audit_service.snapshot(equipment))
    else:
        before = audit_service.snapshot(equipment)
        for key, value in values.items():
            setattr(equipment, key, value)
        action = "equipment.status_changed" if before.get("status") != equipment.status else "equipment.updated"
        audit_service.record_change(action, equipment, before)
    db.session.commit()
    return equipment


def set_status(equipment: Equipment, status: str, reason: str) -> None:
    if status not in EQUIPMENT_STATUSES:
        raise MetrologyError("Statut invalide.")
    if not reason.strip():
        raise MetrologyError("Le motif du changement de statut est obligatoire.")
    before = audit_service.snapshot(equipment)
    equipment.status = status
    audit_service.record_change("equipment.status_changed", equipment, before, reason=reason.strip())
    db.session.commit()


def archive_equipment(equipment: Equipment, reason: str) -> None:
    if equipment.archived_at is not None:
        raise MetrologyError("Équipement déjà archivé.")
    if not reason.strip():
        raise MetrologyError("Le motif d'archivage est obligatoire.")
    before = audit_service.snapshot(equipment)
    equipment.archived_at = utcnow()
    equipment.status = "retired"
    for plan in repo(MaintenancePlan).all(MaintenancePlan.equipment_id == equipment.id, MaintenancePlan.is_active.is_(True)):
        plan.is_active = False
    audit_service.record_change("equipment.archived", equipment, before, reason=reason.strip())
    db.session.commit()


def equipment_query(search: str | None = None, status: str | None = None, category_id: uuid.UUID | None = None,
                    include_archived: bool = False) -> sa.Select:
    stmt = sa.select(Equipment).order_by(Equipment.name)
    if not include_archived:
        stmt = stmt.where(Equipment.archived_at.is_(None))
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(sa.or_(Equipment.name.ilike(like), Equipment.internal_id.ilike(like),
                                 Equipment.serial_number.ilike(like), Equipment.location.ilike(like)))
    if status:
        stmt = stmt.where(Equipment.status == status)
    if category_id:
        stmt = stmt.where(Equipment.category_id == category_id)
    return stmt


def save_plan(equipment: Equipment, plan: MaintenancePlan | None, data: dict) -> MaintenancePlan:
    if data["event_type"] not in EVENT_TYPES or data["period_unit"] not in PERIOD_UNITS:
        raise MetrologyError("Type ou périodicité invalide.")
    if not data.get("period_value") or data["period_value"] <= 0:
        raise MetrologyError("La périodicité doit être strictement positive.")
    if data.get("responsible_id") and not is_member(data["responsible_id"]):
        raise MetrologyError("Responsable inconnu.")
    last_done = data.get("last_done_on")
    due = data.get("next_due_on") or next_due(last_done, data["period_value"], data["period_unit"])
    fields = dict(event_type=data["event_type"], period_value=data["period_value"], period_unit=data["period_unit"],
                  provider=(data.get("provider") or "").strip() or None, responsible_id=data.get("responsible_id"),
                  last_done_on=last_done, next_due_on=due, comment=(data.get("comment") or "").strip() or None,
                  is_active=data.get("is_active", True))
    if plan is None:
        plan = MaintenancePlan(equipment_id=equipment.id, created_by_id=current_user.id, **fields)
        db.session.add(plan)
        db.session.flush()
        audit_service.record("maintenance_plan.created", plan, after=audit_service.snapshot(plan))
    else:
        before = audit_service.snapshot(plan)
        for key, value in fields.items():
            setattr(plan, key, value)
        audit_service.record_change("maintenance_plan.updated", plan, before)
    db.session.commit()
    return plan


@dataclass
class EventResult:
    event: MaintenanceEvent
    suggest_suspension: bool


def record_event(equipment: Equipment, plan: MaintenancePlan | None, *, event_type: str, performed_on: date,
                 outcome: str | None, provider: str | None, comment: str | None,
                 attachment: UploadedFile | None) -> EventResult:
    if event_type not in EVENT_TYPES:
        raise MetrologyError("Type d'événement invalide.")
    if outcome is not None and outcome not in OUTCOMES:
        raise MetrologyError("Résultat invalide.")
    if plan is not None and plan.equipment_id != equipment.id:
        raise MetrologyError("Ce plan ne concerne pas cet équipement.")
    if performed_on > date.today().replace(year=date.today().year + 1):
        raise MetrologyError("Date de réalisation invalide.")
    event = MaintenanceEvent(plan_id=plan.id if plan else None, equipment_id=equipment.id, event_type=event_type,
                             performed_on=performed_on, outcome=outcome, provider=(provider or "").strip() or None,
                             comment=(comment or "").strip() or None, performed_by_id=current_user.id)
    db.session.add(event)
    db.session.flush()
    audit_service.record("maintenance_event.created", event, after=audit_service.snapshot(event))
    if plan is not None:
        before = audit_service.snapshot(plan, ["last_done_on", "next_due_on"])
        # Échéance recalculée depuis la date EFFECTIVE (décision validée à l'étape 1).
        if plan.last_done_on is None or performed_on >= plan.last_done_on:
            plan.last_done_on = performed_on
            plan.next_due_on = add_period(performed_on, plan.period_value, plan.period_unit)
        audit_service.record_change("maintenance_plan.rescheduled", plan, before)
    if attachment is not None and attachment.filename:
        store_upload(attachment, "maintenance_event", event.id)
    db.session.commit()
    suggest = event_type == "calibration" and outcome == "non_conform" and equipment.status != "suspended"
    return EventResult(event, suggest)


def recompute_due_dates() -> int:
    """Recalcule la prochaine échéance de tous les plans actifs du laboratoire courant."""
    changed = 0
    for plan in repo(MaintenancePlan).all(MaintenancePlan.is_active.is_(True)):
        if plan.last_done_on is None:
            continue
        due = add_period(plan.last_done_on, plan.period_value, plan.period_unit)
        if due != plan.next_due_on:
            before = audit_service.snapshot(plan, ["next_due_on"])
            plan.next_due_on = due
            audit_service.record_change("maintenance_plan.rescheduled", plan, before, reason="recalcul des échéances")
            changed += 1
    db.session.commit()
    return changed


def plans_query(until: date | None = None, overdue_before: date | None = None) -> sa.Select:
    stmt = (
        sa.select(MaintenancePlan)
        .join(Equipment, Equipment.id == MaintenancePlan.equipment_id)
        .where(MaintenancePlan.is_active.is_(True), Equipment.archived_at.is_(None))
        .order_by(MaintenancePlan.next_due_on.asc().nulls_last(), Equipment.name)
    )
    if until is not None:
        stmt = stmt.where(MaintenancePlan.next_due_on <= until)
    if overdue_before is not None:
        stmt = stmt.where(MaintenancePlan.next_due_on < overdue_before)
    return stmt


def equipment_history(equipment: Equipment) -> list[MaintenanceEvent]:
    return list(repo(MaintenanceEvent).all(MaintenanceEvent.equipment_id == equipment.id,
                                           order_by=[MaintenanceEvent.performed_on.desc(),
                                                     MaintenanceEvent.created_at.desc()]))
