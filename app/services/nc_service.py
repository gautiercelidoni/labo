"""Non-conformités (V1.1) : numérotation, cycle de vie, clôture contrôlée, lien avec les actions correctives."""
from __future__ import annotations

from datetime import date

import sqlalchemy as sa
from flask_login import current_user

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.base import utcnow
from app.models.ciq import CIQParameter, CIQRun
from app.models.equipment import Equipment
from app.models.non_conformities import (
    NC_ORIGINS,
    NC_SEVERITIES,
    NonConformity,
    NonConformityStatusChange,
)
from app.repositories.base import repo
from app.security.permissions import P, can
from app.security.tenancy import current_tenant_id
from app.services import audit_service
from app.services.membership_service import is_member

# Cycle de vie : l'annulation est possible depuis tout statut non terminal (avec justification).
TRANSITIONS = {
    "draft": {"open"},
    "open": {"analysis"},
    "analysis": {"action", "verification"},
    "action": {"verification", "analysis"},
    "verification": {"closed", "action"},
    "closed": set(),
    "cancelled": set(),
}
TERMINAL = {"closed", "cancelled"}
EDITABLE_FIELDS = ("title", "description", "detected_on", "origin", "severity", "impact", "immediate_action",
                   "root_cause", "no_action_justification", "responsible_id", "target_date", "effectiveness_check",
                   "effectiveness_justification", "equipment_id")


class NCError(ValueError):
    pass


def _next_seq(year: int) -> int:
    # Verrou transactionnel par laboratoire et par année : numérotation sans trou ni doublon.
    key = f"nc:{current_tenant_id()}:{year}"
    db.session.execute(sa.text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": key})
    current = db.session.scalar(sa.select(sa.func.max(NonConformity.seq)).where(NonConformity.year == year))
    return (current or 0) + 1


def _log_status(nc: NonConformity, from_status: str | None, to_status: str, comment: str | None) -> None:
    db.session.add(NonConformityStatusChange(non_conformity_id=nc.id, from_status=from_status, to_status=to_status,
                                             changed_by_id=current_user.id, changed_at=utcnow(), comment=comment))


def _validate_fields(data: dict) -> None:
    if data.get("origin") and data["origin"] not in NC_ORIGINS:
        raise NCError("Origine invalide.")
    if data.get("severity") and data["severity"] not in NC_SEVERITIES:
        raise NCError("Gravité invalide.")
    if data.get("responsible_id") and not is_member(data["responsible_id"]):
        raise NCError("Responsable inconnu.")
    if data.get("equipment_id") and repo(Equipment).get(data["equipment_id"]) is None:
        raise NCError("Équipement inconnu.")


def create(data: dict, *, source_run: CIQRun | None = None) -> NonConformity:
    _validate_fields(data)
    detected_on: date = data.get("detected_on") or date.today()
    year = detected_on.year
    nc = NonConformity(
        year=year, seq=_next_seq(year), status="draft", detected_by_id=current_user.id, created_by_id=current_user.id,
        source_ciq_run_id=source_run.id if source_run else None,
        **{k: (v.strip() if isinstance(v, str) else v) for k, v in data.items() if k in EDITABLE_FIELDS},
    )
    nc.detected_on = detected_on
    db.session.add(nc)
    db.session.flush()
    _log_status(nc, None, "draft", "Création")
    audit_service.record("nc.created", nc, after=audit_service.snapshot(nc))
    db.session.commit()
    return nc


def create_from_run(run: CIQRun) -> NonConformity:
    if run.status not in ("rejected", "justified"):
        raise NCError("Seule une série rejetée peut être convertie en non-conformité.")
    existing = repo(NonConformity).first(NonConformity.source_ciq_run_id == run.id)
    if existing is not None:
        return existing
    parameter = repo(CIQParameter).get(run.parameter_id)
    return create({
        "title": f"Rejet CIQ — {parameter.name if parameter else ''}",
        "description": f"Série du {run.run_at.strftime('%d/%m/%Y %H:%M')} UTC rejetée."
                       + (f"\nJustification : {run.justification}" if run.justification else ""),
        "origin": "ciq", "severity": "minor", "equipment_id": run.equipment_id,
        "detected_on": run.run_at.date(),
    }, source_run=run)


def update(nc: NonConformity, data: dict) -> None:
    if nc.status in TERMINAL:
        raise NCError("Une non-conformité clôturée ou annulée ne peut plus être modifiée.")
    _validate_fields(data)
    before = audit_service.snapshot(nc)
    for key in EDITABLE_FIELDS:
        if key in data:
            value = data[key]
            setattr(nc, key, value.strip() if isinstance(value, str) else value)
    audit_service.record_change("nc.updated", nc, before)
    db.session.commit()


def linked_actions(nc: NonConformity) -> list[CorrectiveAction]:
    return list(repo(CorrectiveAction).all(CorrectiveAction.source_type == "non_conformity",
                                           CorrectiveAction.source_id == nc.id, order_by=CorrectiveAction.created_at))


def closure_problems(nc: NonConformity) -> list[str]:
    problems = []
    if not (nc.root_cause or "").strip():
        problems.append("L'analyse de cause est obligatoire.")
    if not linked_actions(nc) and not (nc.no_action_justification or "").strip():
        problems.append("Une action corrective ou une justification de non-action est obligatoire.")
    if not (nc.effectiveness_check or "").strip() and not (nc.effectiveness_justification or "").strip():
        problems.append("La vérification d'efficacité (ou sa justification) est obligatoire.")
    return problems


def change_status(nc: NonConformity, to_status: str, comment: str | None) -> None:
    comment = (comment or "").strip() or None
    if to_status == "cancelled":
        if nc.status in TERMINAL:
            raise NCError("Cette non-conformité est déjà terminée.")
        if not comment:
            raise NCError("L'annulation doit être justifiée.")
        if not can(P.NC_CLOSE):
            raise NCError("Seul un responsable qualité peut annuler une non-conformité.")
    elif to_status not in TRANSITIONS.get(nc.status, set()):
        raise NCError("Transition de statut non autorisée.")
    if to_status == "closed":
        if not can(P.NC_CLOSE):
            raise NCError("Seul un responsable qualité peut clôturer une non-conformité.")
        problems = closure_problems(nc)
        if problems:
            raise NCError(" ".join(problems))
    if nc.status != "draft" and not can(P.NC_EDIT) and to_status != "cancelled":
        raise NCError("Seul un responsable qualité fait avancer une non-conformité après son ouverture.")
    before = audit_service.snapshot(nc, ["status", "closed_on", "validated_by_id", "cancel_reason"])
    previous = nc.status
    nc.status = to_status
    if to_status == "closed":
        nc.closed_on = date.today()
        nc.validated_by_id = current_user.id
    if to_status == "cancelled":
        nc.cancel_reason = comment
    _log_status(nc, previous, to_status, comment)
    audit_service.record("nc.status_changed", nc, before=before,
                         after=audit_service.snapshot(nc, ["status", "closed_on", "validated_by_id", "cancel_reason"]),
                         reason=comment)
    db.session.commit()


def history(nc: NonConformity) -> list[NonConformityStatusChange]:
    return list(repo(NonConformityStatusChange).all(NonConformityStatusChange.non_conformity_id == nc.id,
                                                    order_by=NonConformityStatusChange.changed_at))


def list_query(status_group: str | None = None, severity: str | None = None, origin: str | None = None,
               year: int | None = None) -> sa.Select:
    stmt = sa.select(NonConformity).order_by(NonConformity.year.desc(), NonConformity.seq.desc())
    if status_group == "ouvertes":
        stmt = stmt.where(NonConformity.status.not_in(TERMINAL))
    elif status_group:
        stmt = stmt.where(NonConformity.status == status_group)
    if severity in NC_SEVERITIES:
        stmt = stmt.where(NonConformity.severity == severity)
    if origin in NC_ORIGINS:
        stmt = stmt.where(NonConformity.origin == origin)
    if year:
        stmt = stmt.where(NonConformity.year == year)
    return stmt

