"""Contrôles qualité internes : configuration, limites, saisie évaluée, annulation, rejets."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from flask_login import current_user

from app.domain.ciq_rules.engine import evaluate
from app.domain.ciq_rules.shewhart import ComputedLimits, compute_limits
from app.domain.ciq_rules.types import DEFAULT_SEVERITIES, Evaluation, Point, rules_for_mode
from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.base import utcnow
from app.models.ciq import (
    MODES,
    CIQParameter,
    CIQResult,
    CIQRuleConfig,
    CIQRun,
    ControlLevel,
    ControlLimitSet,
    ControlLot,
)
from app.models.equipment import Equipment
from app.repositories.base import repo
from app.services import audit_service
from app.services.membership_service import is_member

# Nombre de résultats antérieurs examinés pour évaluer une série (couvre largement 10x et 10/11).
HISTORY_DEPTH = 60


class CIQError(ValueError):
    pass


def _uid():
    return current_user.id if current_user and current_user.is_authenticated else None


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def get_config(parameter: CIQParameter) -> CIQRuleConfig:
    config = repo(CIQRuleConfig).first(CIQRuleConfig.parameter_id == parameter.id)
    if config is None:
        config = CIQRuleConfig(parameter_id=parameter.id, mode="westgard", rules={},
                               comment_required_on=["warning", "reject"], min_reference_points=20)
        db.session.add(config)
        db.session.flush()
    return config


def effective_mode(level: ControlLevel, config: CIQRuleConfig) -> str:
    return level.mode or config.mode


def list_parameters(active_only: bool = False) -> list[CIQParameter]:
    stmt = (
        sa.select(CIQParameter)
        .join(Equipment, Equipment.id == CIQParameter.equipment_id)
        .order_by(Equipment.name, CIQParameter.name)
    )
    if active_only:
        stmt = stmt.where(CIQParameter.is_active.is_(True))
    return list(db.session.scalars(stmt).all())


def save_parameter(parameter: CIQParameter | None, *, equipment_id: uuid.UUID, name: str, unit: str | None,
                   decimals: int, is_active: bool, mode: str) -> CIQParameter:
    if repo(Equipment).get(equipment_id) is None:
        raise CIQError("Équipement inconnu.")
    if mode not in MODES:
        raise CIQError("Mode d'évaluation invalide.")
    name = name.strip()
    duplicate = repo(CIQParameter).first(CIQParameter.equipment_id == equipment_id, CIQParameter.name == name)
    if duplicate is not None and (parameter is None or duplicate.id != parameter.id):
        raise CIQError("Ce paramètre existe déjà pour cet équipement.")
    if parameter is None:
        parameter = CIQParameter(equipment_id=equipment_id, name=name, unit=unit, decimals=decimals,
                                 is_active=is_active)
        db.session.add(parameter)
        db.session.flush()
        config = CIQRuleConfig(parameter_id=parameter.id, mode=mode, rules={},
                               comment_required_on=["warning", "reject"], min_reference_points=20)
        db.session.add(config)
        db.session.flush()
        audit_service.record("ciq.parameter.created", parameter, after=audit_service.snapshot(parameter) | {"mode": mode})
    else:
        before = audit_service.snapshot(parameter)
        parameter.equipment_id, parameter.name, parameter.unit = equipment_id, name, unit
        parameter.decimals, parameter.is_active = decimals, is_active
        audit_service.record_change("ciq.parameter.updated", parameter, before)
    db.session.commit()
    return parameter


def update_rule_config(parameter: CIQParameter, *, mode: str, rules: dict[str, str],
                       comment_required_on: list[str], min_reference_points: int, chain_lots: bool) -> CIQRuleConfig:
    if mode not in MODES:
        raise CIQError("Mode d'évaluation invalide.")
    config = get_config(parameter)
    before = audit_service.snapshot(config)
    config.mode = mode
    config.rules = {r: rules.get(r, DEFAULT_SEVERITIES[r]) for m in MODES for r in rules_for_mode(m)}  # type: ignore[arg-type]
    config.comment_required_on = [s for s in comment_required_on if s in ("warning", "reject")]
    config.min_reference_points = min_reference_points
    config.chain_lots = chain_lots
    audit_service.record_change("ciq.config.updated", config, before)
    db.session.commit()
    return config


def list_levels(parameter: CIQParameter, active_only: bool = False) -> list[ControlLevel]:
    criteria = [ControlLevel.parameter_id == parameter.id]
    if active_only:
        criteria.append(ControlLevel.is_active.is_(True))
    return list(repo(ControlLevel).all(*criteria, order_by=[ControlLevel.sort_order, ControlLevel.label]))


def save_level(parameter: CIQParameter, level: ControlLevel | None, *, label: str, sort_order: int,
               mode: str | None, is_active: bool = True) -> ControlLevel:
    label = label.strip()
    if mode is not None and mode not in MODES:
        raise CIQError("Mode d'évaluation invalide.")
    duplicate = repo(ControlLevel).first(ControlLevel.parameter_id == parameter.id, ControlLevel.label == label)
    if duplicate is not None and (level is None or duplicate.id != level.id):
        raise CIQError("Ce niveau existe déjà.")
    if level is None:
        level = ControlLevel(parameter_id=parameter.id, label=label, sort_order=sort_order, mode=mode,
                             is_active=is_active)
        db.session.add(level)
        db.session.flush()
        audit_service.record("ciq.level.created", level, after=audit_service.snapshot(level))
    else:
        before = audit_service.snapshot(level)
        level.label, level.sort_order, level.mode, level.is_active = label, sort_order, mode, is_active
        audit_service.record_change("ciq.level.updated", level, before)
    db.session.commit()
    return level


def list_lots(level: ControlLevel) -> list[ControlLot]:
    return list(repo(ControlLot).all(ControlLot.level_id == level.id,
                                     order_by=[ControlLot.in_use_from.desc().nulls_last(), ControlLot.lot_number]))


def save_lot(level: ControlLevel, lot: ControlLot | None, *, lot_number: str, manufacturer: str | None,
             expires_on: date | None, in_use_from: date | None, in_use_to: date | None) -> ControlLot:
    lot_number = lot_number.strip()
    if in_use_from and in_use_to and in_use_to < in_use_from:
        raise CIQError("La fin d'utilisation précède le début.")
    duplicate = repo(ControlLot).first(ControlLot.level_id == level.id, ControlLot.lot_number == lot_number)
    if duplicate is not None and (lot is None or duplicate.id != lot.id):
        raise CIQError("Ce numéro de lot existe déjà pour ce niveau.")
    if lot is None:
        lot = ControlLot(level_id=level.id, lot_number=lot_number, manufacturer=manufacturer, expires_on=expires_on,
                         in_use_from=in_use_from, in_use_to=in_use_to)
        db.session.add(lot)
        db.session.flush()
        audit_service.record("ciq.lot.created", lot, after=audit_service.snapshot(lot))
    else:
        before = audit_service.snapshot(lot)
        lot.lot_number, lot.manufacturer, lot.expires_on = lot_number, manufacturer, expires_on
        lot.in_use_from, lot.in_use_to = in_use_from, in_use_to
        audit_service.record_change("ciq.lot.updated", lot, before)
    db.session.commit()
    return lot


def current_lot(level: ControlLevel, on: date) -> ControlLot | None:
    """Lot en usage à une date donnée (le plus récent si plusieurs chevauchent)."""
    stmt = (
        sa.select(ControlLot)
        .where(
            ControlLot.level_id == level.id,
            ControlLot.archived_at.is_(None),
            sa.or_(ControlLot.in_use_from.is_(None), ControlLot.in_use_from <= on),
            sa.or_(ControlLot.in_use_to.is_(None), ControlLot.in_use_to >= on),
        )
        .order_by(ControlLot.in_use_from.desc().nulls_last(), ControlLot.created_at.desc())
        .limit(1)
    )
    return db.session.scalars(stmt).first()


def active_limit_set(lot: ControlLot) -> ControlLimitSet | None:
    return repo(ControlLimitSet).first(ControlLimitSet.lot_id == lot.id, ControlLimitSet.valid_to.is_(None))


def limit_history(lot: ControlLot) -> list[ControlLimitSet]:
    return list(repo(ControlLimitSet).all(ControlLimitSet.lot_id == lot.id, order_by=ControlLimitSet.valid_from.desc()))


def _replace_limits(lot: ControlLot, new: ControlLimitSet, reason: str | None) -> ControlLimitSet:
    previous = active_limit_set(lot)
    if previous is not None:
        if not reason or not reason.strip():
            raise CIQError("Un motif est obligatoire pour remplacer un jeu de limites.")
        previous.valid_to = utcnow()
        db.session.flush()  # libère l'index unique partiel avant l'insertion
        audit_service.record("ciq.limits.closed", previous, after={"valid_to": previous.valid_to}, reason=reason)
    db.session.add(new)
    db.session.flush()
    audit_service.record("ciq.limits.created", new, after=audit_service.snapshot(new), reason=reason)
    return new


def set_limits(lot: ControlLot, *, mode: str, mean: Decimal, sd: Decimal, source: str, reason: str | None) -> ControlLimitSet:
    """Cible et écart-type saisis (fournisseur ou laboratoire). Jamais de recalcul automatique."""
    if sd is None or sd <= 0:
        raise CIQError("L'écart-type doit être strictement positif.")
    if mode not in MODES:
        raise CIQError("Mode invalide.")
    new = ControlLimitSet(lot_id=lot.id, mode=mode, mean=mean, sd=sd, source=source, valid_from=utcnow(),
                          reason=(reason or "").strip() or None, created_by_id=_uid())
    _replace_limits(lot, new, reason)
    db.session.commit()
    return new


def reference_values(lot: ControlLot, ref_from: datetime, ref_to: datetime) -> list[Decimal]:
    stmt = sa.select(CIQResult.value).where(
        CIQResult.lot_id == lot.id,
        CIQResult.voided_at.is_(None),
        CIQResult.run_at >= ref_from,
        CIQResult.run_at <= ref_to,
    )
    return list(db.session.scalars(stmt).all())


def preview_reference(lot: ControlLot, ref_from: datetime, ref_to: datetime, min_points: int) -> ComputedLimits:
    try:
        return compute_limits(reference_values(lot, ref_from, ref_to), min_points)
    except ValueError as e:
        raise CIQError(str(e))


def compute_limits_from_reference(lot: ControlLot, *, mode: str, ref_from: datetime, ref_to: datetime,
                                  reason: str, min_points: int, accept_insufficient: bool = False) -> ControlLimitSet:
    """Recalcul MANUEL des limites sur une période de référence choisie, avec motif obligatoire."""
    if not reason or not reason.strip():
        raise CIQError("Le motif du calcul des limites est obligatoire.")
    computed = preview_reference(lot, ref_from, ref_to, min_points)
    if not computed.sufficient and not accept_insufficient:
        raise CIQError(
            f"La période de référence ne contient que {computed.n} valeurs (minimum {min_points}). "
            "Confirmez explicitement pour continuer."
        )
    new = ControlLimitSet(lot_id=lot.id, mode=mode, mean=computed.mean, sd=computed.sd, source="computed",
                          reference_from=ref_from, reference_to=ref_to, n_reference=computed.n,
                          valid_from=utcnow(), reason=reason.strip(), created_by_id=_uid())
    _replace_limits(lot, new, reason)
    db.session.commit()
    return new


# ---------------------------------------------------------------------------
# Évaluation et saisie
# ---------------------------------------------------------------------------

@dataclass
class EntryInput:
    level: ControlLevel
    lot: ControlLot | None
    value: Decimal | None
    comment: str | None = None


@dataclass
class EntryPreview:
    entry: EntryInput
    limit_set: ControlLimitSet | None
    mode: str
    point: Point | None = None
    evaluation: Evaluation | None = None
    errors: list[str] = field(default_factory=list)

    @property
    def needs_comment(self) -> bool:
        return self.evaluation is not None and self.evaluation.status != "accepted"


def _history_points(parameter_id: uuid.UUID, until: datetime, exclude_run_id: uuid.UUID | None = None,
                    levels: dict | None = None) -> list[Point]:
    stmt = (
        sa.select(CIQResult, ControlLimitSet.mean, ControlLimitSet.sd, ControlLevel.sort_order)
        .join(ControlLimitSet, ControlLimitSet.id == CIQResult.limit_set_id)
        .join(ControlLevel, ControlLevel.id == CIQResult.level_id)
        .where(CIQResult.parameter_id == parameter_id, CIQResult.voided_at.is_(None), CIQResult.run_at <= until)
        .order_by(CIQResult.run_at.desc(), CIQResult.created_at.desc())
        .limit(HISTORY_DEPTH)
    )
    if exclude_run_id is not None:
        stmt = stmt.where(CIQResult.run_id != exclude_run_id)
    return [result_point(r, mean, sd, order) for r, mean, sd, order in db.session.execute(stmt).all()]


def result_point(r: CIQResult, mean: Decimal, sd: Decimal, level_order: int) -> Point:
    return Point(
        key=str(r.id), run_key=str(r.run_id), level_key=str(r.level_id), level_order=level_order,
        lot_key=str(r.lot_id), limit_key=str(r.limit_set_id), at=r.run_at, value=r.value, mean=mean, sd=sd,
        mode=r.mode, voided=r.voided_at is not None,
        seq=int(r.created_at.timestamp() * 1_000_000) if r.created_at else 0,
    )


def _run_peer_points(run: CIQRun) -> list[Point]:
    stmt = (
        sa.select(CIQResult, ControlLimitSet.mean, ControlLimitSet.sd, ControlLevel.sort_order)
        .join(ControlLimitSet, ControlLimitSet.id == CIQResult.limit_set_id)
        .join(ControlLevel, ControlLevel.id == CIQResult.level_id)
        .where(CIQResult.run_id == run.id, CIQResult.voided_at.is_(None))
    )
    return [result_point(r, mean, sd, order) for r, mean, sd, order in db.session.execute(stmt).all()]


def preview_entries(parameter: CIQParameter, run_at: datetime, entries: list[EntryInput],
                    run: CIQRun | None = None) -> list[EntryPreview]:
    """Évalue des saisies sans rien enregistrer (même logique que l'enregistrement)."""
    config = get_config(parameter)
    previews: list[EntryPreview] = []
    run_key = str(run.id) if run else "nouvelle-serie"
    for i, entry in enumerate(entries):
        mode = effective_mode(entry.level, config)
        preview = EntryPreview(entry=entry, limit_set=None, mode=mode)
        if entry.lot is None:
            preview.errors.append(f"Aucun lot en usage pour le niveau {entry.level.label}.")
        elif entry.lot.level_id != entry.level.id:
            preview.errors.append("Le lot ne correspond pas au niveau.")
        else:
            preview.limit_set = active_limit_set(entry.lot)
            if preview.limit_set is None:
                preview.errors.append(f"Aucune limite définie pour le lot {entry.lot.lot_number}.")
            elif preview.limit_set.mode != mode:
                preview.errors.append(
                    f"Les limites du lot {entry.lot.lot_number} sont définies pour un autre mode d'évaluation."
                )
        if entry.value is None:
            preview.errors.append(f"Valeur manquante pour le niveau {entry.level.label}.")
        if not preview.errors:
            preview.point = Point(
                key=f"nouveau-{entry.level.id}", run_key=run_key, level_key=str(entry.level.id),
                level_order=entry.level.sort_order, lot_key=str(entry.lot.id), limit_key=str(preview.limit_set.id),
                at=run_at, value=entry.value, mean=preview.limit_set.mean, sd=preview.limit_set.sd, mode=mode,
                seq=10**18 + i,
            )
        previews.append(preview)
    history = _history_points(parameter.id, run_at, exclude_run_id=run.id if run else None)
    if run is not None:
        history += _run_peer_points(run)
    new_points = [p.point for p in previews if p.point is not None]
    for preview in previews:
        if preview.point is None:
            continue
        context = history + [p for p in new_points if p.key != preview.point.key]
        preview.evaluation = evaluate(preview.point, context, config.rules, config.chain_lots)
        severity = {"warning": "warning", "rejected": "reject"}.get(preview.evaluation.status)
        if severity in (config.comment_required_on or []) and not (preview.entry.comment or "").strip():
            preview.errors.append(
                f"Commentaire obligatoire pour le niveau {preview.entry.level.label} "
                f"({'rejet' if severity == 'reject' else 'alerte'})."
            )
    return previews


def _store_result(run: CIQRun, parameter: CIQParameter, preview: EntryPreview) -> CIQResult:
    evaluation = preview.evaluation
    point = preview.point
    assert evaluation is not None and point is not None and preview.limit_set is not None
    result = CIQResult(
        run_id=run.id, level_id=preview.entry.level.id, lot_id=preview.entry.lot.id,
        limit_set_id=preview.limit_set.id, parameter_id=parameter.id, run_at=run.run_at, mode=preview.mode,
        value=preview.entry.value, z_score=point.z.quantize(Decimal("0.000001")),
        deviation=point.deviation.quantize(Decimal("0.000001")), status=evaluation.status,
        rules_triggered=[{"rule": h.rule, "severity": h.severity, "message": h.message} for h in evaluation.hits],
        rules_not_evaluated=[{"rule": n.rule, "reason": n.reason} for n in evaluation.not_evaluated],
        comment=(preview.entry.comment or "").strip() or None, entered_by_id=_uid(),
    )
    db.session.add(result)
    db.session.flush()
    return result


def refresh_run_status(run: CIQRun) -> None:
    statuses = db.session.scalars(
        sa.select(CIQResult.status).where(CIQResult.run_id == run.id, CIQResult.voided_at.is_(None))
    ).all()
    if run.status == "justified" and "rejected" in statuses:
        return
    if not statuses:
        run.status = "open"
    elif "rejected" in statuses:
        run.status = "rejected"
    else:
        run.status = "accepted"


def create_run(parameter: CIQParameter, run_at: datetime, entries: list[EntryInput],
               operator_id: uuid.UUID | None = None) -> tuple[CIQRun | None, list[EntryPreview]]:
    """Enregistre une série si toutes les saisies sont valides ; sinon renvoie les erreurs."""
    if not parameter.is_active:
        raise CIQError("Ce paramètre est désactivé.")
    if not entries:
        raise CIQError("Aucune valeur saisie.")
    if operator_id is not None and not is_member(operator_id):
        raise CIQError("Opérateur inconnu.")
    previews = preview_entries(parameter, run_at, entries)
    if any(p.errors for p in previews):
        return None, previews
    run = CIQRun(equipment_id=parameter.equipment_id, parameter_id=parameter.id, run_at=run_at,
                 operator_id=operator_id or _uid(), status="open", created_by_id=_uid())
    db.session.add(run)
    db.session.flush()
    results = [_store_result(run, parameter, p) for p in previews]
    refresh_run_status(run)
    audit_service.record(
        "ciq.run.created", run,
        after={
            "parameter": parameter.name, "run_at": run_at, "status": run.status,
            "results": [{"level": p.entry.level.label, "lot": p.entry.lot.lot_number, "value": r.value,
                         "z": r.z_score, "status": r.status, "rules": [h["rule"] for h in r.rules_triggered],
                         "comment": r.comment} for p, r in zip(previews, results)],
        },
    )
    db.session.commit()
    return run, previews


def add_result(run: CIQRun, entry: EntryInput) -> tuple[CIQResult | None, EntryPreview]:
    """Ressaisie d'un niveau dans une série existante (après annulation d'un résultat erroné)."""
    parameter = repo(CIQParameter).get_or_404(run.parameter_id)
    existing = repo(CIQResult).first(CIQResult.run_id == run.id, CIQResult.level_id == entry.level.id,
                                     CIQResult.voided_at.is_(None))
    if existing is not None:
        raise CIQError("Ce niveau a déjà un résultat valide dans cette série : annulez-le d'abord.")
    preview = preview_entries(parameter, run.run_at, [entry], run=run)[0]
    if preview.errors:
        return None, preview
    result = _store_result(run, parameter, preview)
    refresh_run_status(run)
    audit_service.record("ciq.result.created", result, after=audit_service.snapshot(result))
    db.session.commit()
    return result, preview


def void_result(result: CIQResult, reason: str) -> None:
    if result.voided_at is not None:
        raise CIQError("Ce résultat est déjà annulé.")
    if not reason or not reason.strip():
        raise CIQError("Le motif d'annulation est obligatoire.")
    before = audit_service.snapshot(result, ["status", "value", "voided_at"])
    result.voided_at = utcnow()
    result.void_reason = reason.strip()
    result.voided_by_id = _uid()
    db.session.flush()
    run = repo(CIQRun).get_or_404(result.run_id)
    refresh_run_status(run)
    audit_service.record("ciq.result.voided", result, before=before,
                         after={"voided_at": result.voided_at, "run_status": run.status}, reason=reason.strip())
    db.session.commit()


def justify_run(run: CIQRun, *, justification: str, action_description: str | None,
                responsible_id: uuid.UUID | None, due_on: date | None,
                existing_action: CorrectiveAction | None = None) -> CorrectiveAction:
    """Traitement d'un rejet : justification + action corrective (créée ou existante)."""
    if run.status != "rejected":
        raise CIQError("Seule une série rejetée peut être traitée.")
    if not justification or not justification.strip():
        raise CIQError("La justification est obligatoire.")
    if existing_action is not None:
        if existing_action.source_type != "ciq_run" or existing_action.source_id != run.id:
            raise CIQError("Cette action corrective n'est pas liée à cette série.")
        action = existing_action
    else:
        if not action_description or not action_description.strip():
            raise CIQError("Décrivez l'action corrective.")
        if responsible_id is not None and not is_member(responsible_id):
            raise CIQError("Responsable inconnu.")
        action = CorrectiveAction(source_type="ciq_run", source_id=run.id, description=action_description.strip(),
                                  responsible_id=responsible_id, due_on=due_on, status="open", created_by_id=_uid())
        db.session.add(action)
        db.session.flush()
        audit_service.record("corrective_action.created", action, after=audit_service.snapshot(action))
    before = audit_service.snapshot(run, ["status", "justification"])
    run.status = "justified"
    run.justification = justification.strip()
    run.justified_by_id = _uid()
    run.justified_at = utcnow()
    audit_service.record("ciq.run.justified", run, before=before,
                         after={"status": run.status, "justification": run.justification,
                                "corrective_action_id": action.id},
                         reason=run.justification)
    db.session.commit()
    return action


def run_actions(run: CIQRun) -> list[CorrectiveAction]:
    return list(repo(CorrectiveAction).all(CorrectiveAction.source_type == "ciq_run",
                                           CorrectiveAction.source_id == run.id,
                                           order_by=CorrectiveAction.created_at))


# ---------------------------------------------------------------------------
# Consultation
# ---------------------------------------------------------------------------

@dataclass
class ResultFilters:
    start: datetime | None = None
    end: datetime | None = None
    equipment_id: uuid.UUID | None = None
    parameter_id: uuid.UUID | None = None
    level_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    status: str | None = None
    mode: str | None = None
    run_status: str | None = None
    include_voided: bool = False

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in (None, False)}


def results_query(f: ResultFilters) -> sa.Select:
    stmt = (
        sa.select(CIQResult)
        .join(CIQRun, CIQRun.id == CIQResult.run_id)
        .order_by(CIQResult.run_at.desc(), CIQResult.created_at.desc())
    )
    if not f.include_voided:
        stmt = stmt.where(CIQResult.voided_at.is_(None))
    if f.start:
        stmt = stmt.where(CIQResult.run_at >= f.start)
    if f.end:
        stmt = stmt.where(CIQResult.run_at < f.end)
    if f.equipment_id:
        stmt = stmt.where(CIQRun.equipment_id == f.equipment_id)
    if f.parameter_id:
        stmt = stmt.where(CIQResult.parameter_id == f.parameter_id)
    if f.level_id:
        stmt = stmt.where(CIQResult.level_id == f.level_id)
    if f.lot_id:
        stmt = stmt.where(CIQResult.lot_id == f.lot_id)
    if f.status:
        stmt = stmt.where(CIQResult.status == f.status)
    if f.mode:
        stmt = stmt.where(CIQResult.mode == f.mode)
    if f.run_status:
        stmt = stmt.where(CIQRun.status == f.run_status)
    return stmt


def with_display_relations(stmt: sa.Select) -> sa.Select:
    from sqlalchemy.orm import selectinload

    return stmt.options(
        selectinload(CIQResult.level), selectinload(CIQResult.lot), selectinload(CIQResult.entered_by),
        selectinload(CIQResult.run).selectinload(CIQRun.parameter),
    )


def chart_series(parameter: CIQParameter, level: ControlLevel, start: datetime | None,
                 end: datetime | None) -> dict:
    """Données du graphique Levey-Jennings / carte de contrôle d'un niveau."""
    stmt = (
        sa.select(CIQResult, ControlLimitSet, ControlLot.lot_number)
        .join(ControlLimitSet, ControlLimitSet.id == CIQResult.limit_set_id)
        .join(ControlLot, ControlLot.id == CIQResult.lot_id)
        .where(CIQResult.parameter_id == parameter.id, CIQResult.level_id == level.id,
               CIQResult.voided_at.is_(None))
        .order_by(CIQResult.run_at, CIQResult.created_at)
    )
    if start:
        stmt = stmt.where(CIQResult.run_at >= start)
    if end:
        stmt = stmt.where(CIQResult.run_at < end)
    rows = db.session.execute(stmt.limit(1000)).all()
    points = []
    previous_lot, previous_limits = None, None
    from app import format_datetime

    for result, limits, lot_number in rows:
        points.append({
            "id": str(result.id),
            "run_id": str(result.run_id),
            "date": format_datetime(result.run_at),
            "value": float(result.value),
            "z": float(result.z_score),
            "mean": float(limits.mean),
            "sd": float(limits.sd),
            "status": result.status,
            "rules": [h["rule"] for h in result.rules_triggered],
            "lot": lot_number,
            "lot_change": previous_lot is not None and previous_lot != result.lot_id,
            "limits_change": previous_limits is not None and previous_limits != result.limit_set_id,
            "comment": result.comment or "",
        })
        previous_lot, previous_limits = result.lot_id, result.limit_set_id
    return {"parameter": parameter.name, "unit": parameter.unit or "", "level": level.label,
            "decimals": parameter.decimals, "points": points}
