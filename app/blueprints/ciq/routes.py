"""Contrôles qualité internes : tableau, configuration, saisie, séries, historique, graphiques, rapports."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

import sqlalchemy as sa
from flask import Response, abort, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.auth.forms import EmptyForm
from app.blueprints.ciq import bp
from app.blueprints.ciq.forms import (
    JustifyForm,
    LevelForm,
    LimitsForm,
    LotForm,
    ParameterForm,
    ReentryForm,
    ReferenceForm,
    RuleConfigForm,
    RunEntryForm,
    VoidForm,
)
from app.domain.ciq_rules.types import DEFAULT_SEVERITIES, RULE_LABELS, SHEWHART_RULES, WESTGARD_RULES
from app.extensions import db
from app.forms import lab_today, lab_zone, parse_decimal, user_choices
from app.models.actions import CorrectiveAction
from app.models.ciq import (
    MODE_LABELS,
    RESULT_STATUS_LABELS,
    RUN_STATUS_LABELS,
    CIQParameter,
    CIQResult,
    CIQRun,
    ControlLevel,
    ControlLot,
)
from app.models.equipment import Equipment
from app.repositories.base import paginate, parse_uuid, repo
from app.security.permissions import P, require
from app.services import ciq_service, export_pdf
from app.services.ciq_service import CIQError, EntryInput, ResultFilters
from app.services.export_csv import csv_response
from app.services.membership_service import member_users


def _equipment_choices():
    items = repo(Equipment).all(Equipment.archived_at.is_(None), order_by=Equipment.name)
    return [(str(e.id), f"{e.name} ({e.internal_id})") for e in items]


def _parse_decimal(raw: str | None):
    try:
        return parse_decimal(raw), None
    except ValueError:
        return None, "Nombre invalide."


# ---------------------------------------------------------------------------
# Vue d'ensemble
# ---------------------------------------------------------------------------

@bp.route("/")
@require(P.CIQ_VIEW)
def index():
    parameters = ciq_service.list_parameters()
    last_runs = {}
    if parameters:
        sub = (
            sa.select(CIQRun.parameter_id, sa.func.max(CIQRun.run_at).label("last"))
            .group_by(CIQRun.parameter_id).subquery()
        )
        stmt = sa.select(CIQRun).join(sub, sa.and_(CIQRun.parameter_id == sub.c.parameter_id,
                                                   CIQRun.run_at == sub.c.last))
        for run in db.session.scalars(stmt).all():
            last_runs[run.parameter_id] = run
    pending = repo(CIQRun).count(CIQRun.status == "rejected")
    return render_template("ciq/index.html", parameters=parameters, last_runs=last_runs, pending=pending,
                           mode_labels=MODE_LABELS)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@bp.route("/parametres/nouveau", methods=["GET", "POST"])
@require(P.CIQ_CONFIG_EDIT)
def new_parameter():
    form = ParameterForm()
    form.equipment_id.choices = _equipment_choices()
    if form.validate_on_submit():
        try:
            parameter = ciq_service.save_parameter(
                None, equipment_id=form.equipment_id.data, name=form.name.data, unit=form.unit.data,
                decimals=form.decimals.data, is_active=form.is_active.data, mode=form.mode.data,
            )
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Paramètre créé. Ajoutez maintenant ses niveaux de contrôle.", "success")
            return redirect(url_for("ciq.parameter", parameter_id=parameter.id))
    return render_template("ciq/parameter_form.html", form=form, parameter=None)


@bp.route("/parametres/<parameter_id>", methods=["GET", "POST"])
@require(P.CIQ_VIEW)
def parameter(parameter_id):
    parameter = repo(CIQParameter).get_or_404(parameter_id)
    config = ciq_service.get_config(parameter)
    form = ParameterForm(obj=parameter, mode=config.mode)
    form.equipment_id.choices = _equipment_choices()
    rules_form = RuleConfigForm(prefix="rules", obj=config)
    if request.method == "GET":
        for rule in WESTGARD_RULES + SHEWHART_RULES:
            getattr(rules_form, f"rule_{rule}").data = (config.rules or {}).get(rule, DEFAULT_SEVERITIES[rule])
        rules_form.comment_required_on.data = list(config.comment_required_on or [])
    level_form = LevelForm(prefix="level")
    levels = ciq_service.list_levels(parameter)
    lots = {lvl.id: ciq_service.list_lots(lvl) for lvl in levels}
    active_limits = {lot.id: ciq_service.active_limit_set(lot) for lst in lots.values() for lot in lst}
    return render_template(
        "ciq/parameter.html", parameter=parameter, config=config, form=form, rules_form=rules_form,
        level_form=level_form, levels=levels, lots=lots, active_limits=active_limits,
        rule_labels=RULE_LABELS, westgard_rules=WESTGARD_RULES, shewhart_rules=SHEWHART_RULES,
        mode_labels=MODE_LABELS,
    )


@bp.route("/parametres/<parameter_id>/modifier", methods=["POST"])
@require(P.CIQ_CONFIG_EDIT)
def edit_parameter(parameter_id):
    parameter = repo(CIQParameter).get_or_404(parameter_id)
    form = ParameterForm()
    form.equipment_id.choices = _equipment_choices()
    if form.validate_on_submit():
        try:
            ciq_service.save_parameter(parameter, equipment_id=form.equipment_id.data, name=form.name.data,
                                       unit=form.unit.data, decimals=form.decimals.data,
                                       is_active=form.is_active.data, mode=ciq_service.get_config(parameter).mode)
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Paramètre modifié.", "success")
    else:
        flash("Formulaire invalide.", "danger")
    return redirect(url_for("ciq.parameter", parameter_id=parameter.id))


@bp.route("/parametres/<parameter_id>/regles", methods=["POST"])
@require(P.CIQ_CONFIG_EDIT)
def edit_rules(parameter_id):
    parameter = repo(CIQParameter).get_or_404(parameter_id)
    form = RuleConfigForm(prefix="rules")
    if form.validate_on_submit():
        rules = {r: getattr(form, f"rule_{r}").data for r in WESTGARD_RULES + SHEWHART_RULES}
        try:
            ciq_service.update_rule_config(parameter, mode=form.mode.data, rules=rules,
                                           comment_required_on=form.comment_required_on.data or [],
                                           min_reference_points=form.min_reference_points.data,
                                           chain_lots=form.chain_lots.data)
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Configuration des règles enregistrée.", "success")
    else:
        flash("Configuration invalide.", "danger")
    return redirect(url_for("ciq.parameter", parameter_id=parameter.id))


@bp.route("/parametres/<parameter_id>/niveaux", methods=["POST"])
@require(P.CIQ_CONFIG_EDIT)
def add_level(parameter_id):
    parameter = repo(CIQParameter).get_or_404(parameter_id)
    form = LevelForm(prefix="level")
    if form.validate_on_submit():
        try:
            ciq_service.save_level(parameter, None, label=form.label.data, sort_order=form.sort_order.data,
                                   mode=form.mode.data or None, is_active=form.is_active.data)
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Niveau ajouté.", "success")
    else:
        flash("Niveau invalide.", "danger")
    return redirect(url_for("ciq.parameter", parameter_id=parameter.id))


@bp.route("/niveaux/<level_id>", methods=["GET", "POST"])
@require(P.CIQ_VIEW)
def level(level_id):
    level = repo(ControlLevel).get_or_404(level_id)
    parameter = repo(CIQParameter).get_or_404(level.parameter_id)
    form = LevelForm(obj=level)
    if request.method == "GET":
        form.mode.data = level.mode or ""
    lot_form = LotForm(prefix="lot")
    if request.method == "POST":
        from app.security.permissions import can

        if not can(P.CIQ_CONFIG_EDIT):
            abort(403)
        if form.validate_on_submit():
            try:
                ciq_service.save_level(parameter, level, label=form.label.data, sort_order=form.sort_order.data,
                                       mode=form.mode.data or None, is_active=form.is_active.data)
            except CIQError as e:
                db.session.rollback()
                flash(str(e), "danger")
            else:
                flash("Niveau modifié.", "success")
                return redirect(url_for("ciq.level", level_id=level.id))
    lots = ciq_service.list_lots(level)
    return render_template("ciq/level.html", level=level, parameter=parameter, form=form, lot_form=lot_form,
                           lots=lots, active_limits={lot.id: ciq_service.active_limit_set(lot) for lot in lots},
                           mode_labels=MODE_LABELS)


@bp.route("/niveaux/<level_id>/lots", methods=["POST"])
@require(P.CIQ_CONFIG_EDIT)
def add_lot(level_id):
    level = repo(ControlLevel).get_or_404(level_id)
    form = LotForm(prefix="lot")
    if form.validate_on_submit():
        try:
            lot = ciq_service.save_lot(level, None, lot_number=form.lot_number.data,
                                       manufacturer=form.manufacturer.data or None, expires_on=form.expires_on.data,
                                       in_use_from=form.in_use_from.data, in_use_to=form.in_use_to.data)
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Lot ajouté. Définissez maintenant ses limites.", "success")
            return redirect(url_for("ciq.lot", lot_id=lot.id))
    else:
        flash("Lot invalide.", "danger")
    return redirect(url_for("ciq.level", level_id=level.id))


@bp.route("/lots/<lot_id>", methods=["GET", "POST"])
@require(P.CIQ_VIEW)
def lot(lot_id):
    lot = repo(ControlLot).get_or_404(lot_id)
    level = repo(ControlLevel).get_or_404(lot.level_id)
    parameter = repo(CIQParameter).get_or_404(level.parameter_id)
    config = ciq_service.get_config(parameter)
    mode = ciq_service.effective_mode(level, config)
    form = LotForm(obj=lot, prefix="lot")
    if request.method == "POST":
        from app.security.permissions import can

        if not can(P.CIQ_CONFIG_EDIT):
            abort(403)
        if form.validate_on_submit():
            try:
                ciq_service.save_lot(level, lot, lot_number=form.lot_number.data,
                                     manufacturer=form.manufacturer.data or None, expires_on=form.expires_on.data,
                                     in_use_from=form.in_use_from.data, in_use_to=form.in_use_to.data)
            except CIQError as e:
                db.session.rollback()
                flash(str(e), "danger")
            else:
                flash("Lot modifié.", "success")
                return redirect(url_for("ciq.lot", lot_id=lot.id))
    limits_form = LimitsForm(prefix="limits", mode=mode)
    reference_form = ReferenceForm(prefix="ref", mode=mode)
    preview = None
    if request.args.get("apercu_du") and request.args.get("apercu_au"):
        try:
            start = datetime.fromisoformat(request.args["apercu_du"]).replace(tzinfo=lab_zone())
            end = datetime.fromisoformat(request.args["apercu_au"]).replace(tzinfo=lab_zone())
            preview = ciq_service.preview_reference(lot, start, end, config.min_reference_points)
        except (ValueError, CIQError) as e:
            flash(f"Aperçu impossible : {e}", "warning")
    return render_template("ciq/lot.html", lot=lot, level=level, parameter=parameter, config=config, mode=mode,
                           form=form, limits_form=limits_form, reference_form=reference_form, preview=preview,
                           active=ciq_service.active_limit_set(lot), history=ciq_service.limit_history(lot),
                           mode_labels=MODE_LABELS)


@bp.route("/lots/<lot_id>/limites", methods=["POST"])
@require(P.CIQ_CONFIG_EDIT)
def set_limits(lot_id):
    lot = repo(ControlLot).get_or_404(lot_id)
    form = LimitsForm(prefix="limits")
    if form.validate_on_submit():
        try:
            ciq_service.set_limits(lot, mode=form.mode.data, mean=form.mean.data, sd=form.sd.data,
                                   source=form.source.data, reason=form.reason.data)
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Jeu de limites enregistré. Les résultats antérieurs conservent leurs limites d'origine.", "success")
    else:
        flash("Limites invalides : " + "; ".join(e for errs in form.errors.values() for e in errs), "danger")
    return redirect(url_for("ciq.lot", lot_id=lot.id))


@bp.route("/lots/<lot_id>/limites-calculees", methods=["POST"])
@require(P.CIQ_CONFIG_EDIT)
def compute_limits(lot_id):
    lot = repo(ControlLot).get_or_404(lot_id)
    level = repo(ControlLevel).get_or_404(lot.level_id)
    parameter = repo(CIQParameter).get_or_404(level.parameter_id)
    config = ciq_service.get_config(parameter)
    form = ReferenceForm(prefix="ref")
    if form.validate_on_submit():
        try:
            new = ciq_service.compute_limits_from_reference(
                lot, mode=form.mode.data, ref_from=form.ref_from.data, ref_to=form.ref_to.data,
                reason=form.reason.data, min_points=config.min_reference_points,
                accept_insufficient=form.accept_insufficient.data,
            )
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            if new.n_reference is not None and new.n_reference < config.min_reference_points:
                flash(f"Attention : période de référence de {new.n_reference} valeurs, inférieure au minimum "
                      f"de {config.min_reference_points}.", "warning")
            flash("Limites recalculées et enregistrées.", "success")
    else:
        flash("Période de référence invalide.", "danger")
    return redirect(url_for("ciq.lot", lot_id=lot.id))


# ---------------------------------------------------------------------------
# Saisie
# ---------------------------------------------------------------------------

@bp.route("/saisie", methods=["GET", "POST"])
@require(P.CIQ_RESULT_CREATE)
def entry():
    parameters = ciq_service.list_parameters(active_only=True)
    parameter = repo(CIQParameter).get(request.values.get("parametre")) if request.values.get("parametre") else None
    if parameter is not None and not parameter.is_active:
        parameter = None
    form = RunEntryForm()
    members = member_users()
    form.operator_id.choices = user_choices(members, blank=None)
    if request.method == "GET":
        form.run_at.data = datetime.now(timezone.utc).replace(second=0, microsecond=0)
        form.operator_id.data = current_user.id
    rows, previews = [], None
    if parameter is not None:
        config = ciq_service.get_config(parameter)
        run_date = (form.run_at.data or datetime.now(timezone.utc)).astimezone(lab_zone()).date() \
            if request.method == "POST" and form.run_at.data else lab_today()
        for lvl in ciq_service.list_levels(parameter, active_only=True):
            rows.append({
                "level": lvl,
                "lots": ciq_service.list_lots(lvl),
                "current_lot": ciq_service.current_lot(lvl, run_date),
                "mode": ciq_service.effective_mode(lvl, config),
                "value": request.form.get(f"value_{lvl.id}", ""),
                "comment": request.form.get(f"comment_{lvl.id}", ""),
                "lot_id": request.form.get(f"lot_{lvl.id}"),
            })
    if request.method == "POST" and parameter is not None and form.validate_on_submit():
        entries, errors = [], []
        for row in rows:
            raw = (row["value"] or "").strip()
            if not raw:
                continue  # niveau non passé dans cette série
            value, err = _parse_decimal(raw)
            if err:
                errors.append(f"{row['level'].label} : {err}")
                continue
            lot = repo(ControlLot).get(row["lot_id"]) if row["lot_id"] else row["current_lot"]
            entries.append(EntryInput(level=row["level"], lot=lot, value=value, comment=row["comment"]))
        if errors:
            for e in errors:
                flash(e, "danger")
        elif not entries:
            flash("Saisissez au moins une valeur.", "danger")
        else:
            try:
                run, previews = ciq_service.create_run(parameter, form.run_at.data, entries, form.operator_id.data)
            except CIQError as e:
                db.session.rollback()
                flash(str(e), "danger")
            else:
                if run is not None:
                    level_msg = {"accepted": ("Série acceptée.", "success"),
                                 "rejected": ("Série REJETÉE : justification et action corrective requises.", "danger")}
                    msg, cat = level_msg.get(run.status, ("Série enregistrée.", "info"))
                    if run.status == "accepted" and any(r.status == "warning" for r in run.results):
                        msg, cat = "Série acceptée avec alerte.", "warning"
                    flash(msg, cat)
                    return redirect(url_for("ciq.run", run_id=run.id))
                db.session.rollback()
    return render_template("ciq/entry.html", form=form, parameters=parameters, parameter=parameter, rows=rows,
                           previews=previews, mode_labels=MODE_LABELS)


# ---------------------------------------------------------------------------
# Séries
# ---------------------------------------------------------------------------

@bp.route("/series/<run_id>")
@require(P.CIQ_VIEW)
def run(run_id):
    run = repo(CIQRun).get_or_404(run_id)
    parameter = repo(CIQParameter).get_or_404(run.parameter_id)
    results = repo(CIQResult).all(CIQResult.run_id == run.id, order_by=CIQResult.created_at)
    actions = ciq_service.run_actions(run)
    levels = ciq_service.list_levels(parameter, active_only=True)
    valid_levels = {r.level_id for r in results if r.voided_at is None}
    missing_levels = [lvl for lvl in levels if lvl.id not in valid_levels]
    reentry = ReentryForm(prefix="re")
    reentry.level_id.choices = [(str(lvl.id), lvl.label) for lvl in missing_levels]
    reentry.lot_id.choices = [(str(lot.id), f"{lvl.label} — {lot.lot_number}") for lvl in missing_levels
                              for lot in ciq_service.list_lots(lvl)]
    justify = JustifyForm(prefix="j")
    justify.existing_action_id.choices = [("", "— Créer une nouvelle action —")] + [
        (str(a.id), a.description[:80]) for a in actions]
    justify.responsible_id.choices = user_choices(member_users())
    return render_template("ciq/run.html", run=run, parameter=parameter, results=results, actions=actions,
                           void_form=VoidForm(prefix="v"), reentry=reentry, missing_levels=missing_levels,
                           justify=justify, status_labels=RESULT_STATUS_LABELS, rule_labels=RULE_LABELS,
                           action_form=EmptyForm())


@bp.route("/resultats/<result_id>/annuler", methods=["POST"])
@require(P.CIQ_RESULT_VOID)
def void_result(result_id):
    result = repo(CIQResult).get_or_404(result_id)
    form = VoidForm(prefix="v")
    if form.validate_on_submit():
        try:
            ciq_service.void_result(result, form.reason.data)
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Résultat annulé. Vous pouvez ressaisir la valeur correcte.", "success")
    else:
        flash("Le motif d'annulation est obligatoire.", "danger")
    return redirect(url_for("ciq.run", run_id=result.run_id))


@bp.route("/series/<run_id>/ressaisie", methods=["POST"])
@require(P.CIQ_RESULT_CREATE)
def reenter(run_id):
    run = repo(CIQRun).get_or_404(run_id)
    form = ReentryForm(prefix="re")
    level_id = parse_uuid(request.form.get("re-level_id"))
    level = repo(ControlLevel).get(level_id) if level_id else None
    if level is None or level.parameter_id != run.parameter_id:
        abort(404)
    lots = ciq_service.list_lots(level)
    form.level_id.choices = [(str(level.id), level.label)]
    form.lot_id.choices = [(str(lot.id), lot.lot_number) for lot in lots]
    if form.validate_on_submit():
        lot = repo(ControlLot).get(form.lot_id.data)
        try:
            result, preview = ciq_service.add_result(run, EntryInput(level=level, lot=lot, value=form.value.data,
                                                                     comment=form.comment.data))
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            if result is None:
                db.session.rollback()
                for err in preview.errors:
                    flash(err, "danger")
            else:
                flash(f"Résultat ressaisi : {result.status_label}.", "success" if result.status == "accepted" else "warning")
    else:
        flash("Ressaisie invalide.", "danger")
    return redirect(url_for("ciq.run", run_id=run.id))


@bp.route("/series/<run_id>/justifier", methods=["POST"])
@require(P.CIQ_RUN_JUSTIFY)
def justify_run(run_id):
    run = repo(CIQRun).get_or_404(run_id)
    form = JustifyForm(prefix="j")
    form.existing_action_id.choices = [("", "")] + [(str(a.id), "") for a in ciq_service.run_actions(run)]
    form.responsible_id.choices = user_choices(member_users())
    if form.validate_on_submit():
        existing = repo(CorrectiveAction).get(form.existing_action_id.data) if form.existing_action_id.data else None
        try:
            ciq_service.justify_run(run, justification=form.justification.data,
                                    action_description=form.action_description.data,
                                    responsible_id=form.responsible_id.data, due_on=form.due_on.data,
                                    existing_action=existing)
        except CIQError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Rejet traité : justification et action corrective enregistrées.", "success")
    else:
        flash("Justification invalide : " + "; ".join(e for errs in form.errors.values() for e in errs), "danger")
    return redirect(url_for("ciq.run", run_id=run.id))


# ---------------------------------------------------------------------------
# Historique, exports, graphiques
# ---------------------------------------------------------------------------

def _filters_from_args() -> ResultFilters:
    args = request.args
    tz = lab_zone()

    def day(name):
        try:
            return date.fromisoformat(args[name]) if args.get(name) else None
        except ValueError:
            return None

    start, end = day("du"), day("au")
    status = args.get("statut") if args.get("statut") in RESULT_STATUS_LABELS else None
    mode = args.get("mode") if args.get("mode") in MODE_LABELS else None
    run_status = args.get("statut_serie") if args.get("statut_serie") in RUN_STATUS_LABELS else None
    return ResultFilters(
        start=datetime.combine(start, time.min, tz).astimezone(timezone.utc) if start else None,
        end=datetime.combine(end + timedelta(days=1), time.min, tz).astimezone(timezone.utc) if end else None,
        equipment_id=parse_uuid(args.get("equipement")),
        parameter_id=parse_uuid(args.get("parametre")),
        level_id=parse_uuid(args.get("niveau")),
        lot_id=parse_uuid(args.get("lot")),
        status=status,
        mode=mode,
        run_status=run_status,
        include_voided=args.get("annules") == "1",
    )


@bp.route("/historique")
@require(P.CIQ_VIEW)
def history():
    filters = _filters_from_args()
    stmt = ciq_service.with_display_relations(ciq_service.results_query(filters))
    page = paginate(stmt, request.args.get("page", 1, type=int), 50)
    parameters = ciq_service.list_parameters()
    levels, lots = [], []
    if filters.parameter_id:
        param = repo(CIQParameter).get(filters.parameter_id)
        if param:
            levels = ciq_service.list_levels(param)
            lots = [lot for lvl in levels for lot in ciq_service.list_lots(lvl)]
    return render_template("ciq/history.html", page=page, parameters=parameters, levels=levels, lots=lots,
                           equipments=repo(Equipment).all(order_by=Equipment.name), args=request.args,
                           status_labels=RESULT_STATUS_LABELS, mode_labels=MODE_LABELS,
                           run_status_labels=RUN_STATUS_LABELS)


@bp.route("/historique/export.csv")
@require(P.CIQ_VIEW, P.EXPORT_RUN)
def history_csv():
    filters = _filters_from_args()
    stmt = ciq_service.with_display_relations(ciq_service.results_query(filters))
    results = db.session.scalars(stmt.limit(200000)).all()
    rows = [[
        r.run_at, r.run.parameter.name, r.level.label, r.lot.lot_number, MODE_LABELS[r.mode], r.value,
        r.z_score, r.deviation, r.status_label, ", ".join(h["rule"] for h in r.rules_triggered),
        r.comment, r.entered_by.full_name if r.entered_by else "", r.voided_at, r.void_reason,
    ] for r in results]
    return csv_response(
        "ciq-historique.csv",
        ["Date", "Paramètre", "Niveau", "Lot", "Mode", "Valeur", "z-score", "Écart à la cible", "Statut",
         "Règles déclenchées", "Commentaire", "Saisi par", "Annulé le", "Motif d'annulation"],
        rows,
        {k: str(v) for k, v in filters.as_dict().items()},
    )


@bp.route("/parametres/<parameter_id>/graphique")
@require(P.CIQ_VIEW)
def chart(parameter_id):
    parameter = repo(CIQParameter).get_or_404(parameter_id)
    config = ciq_service.get_config(parameter)
    levels = ciq_service.list_levels(parameter)
    return render_template("ciq/chart.html", parameter=parameter, levels=levels, config=config,
                           mode_labels=MODE_LABELS, args=request.args,
                           modes={lvl.id: ciq_service.effective_mode(lvl, config) for lvl in levels})


@bp.route("/parametres/<parameter_id>/niveaux/<level_id>/graphique.json")
@require(P.CIQ_VIEW)
def chart_data(parameter_id, level_id):
    parameter = repo(CIQParameter).get_or_404(parameter_id)
    level = repo(ControlLevel).get_or_404(level_id)
    if level.parameter_id != parameter.id:
        abort(404)
    filters = _filters_from_args()
    data = ciq_service.chart_series(parameter, level, filters.start, filters.end)
    data["mode"] = ciq_service.effective_mode(level, ciq_service.get_config(parameter))
    return jsonify(data)


@bp.route("/parametres/<parameter_id>/rapport.pdf")
@require(P.CIQ_VIEW, P.EXPORT_RUN)
def monthly_report(parameter_id):
    parameter = repo(CIQParameter).get_or_404(parameter_id)
    month = request.args.get("mois") or lab_today().strftime("%Y-%m")
    try:
        year, mon = (int(x) for x in month.split("-"))
        first = date(year, mon, 1)
    except ValueError:
        abort(400)
    pdf = export_pdf.ciq_monthly_report(parameter, first)
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="rapport-ciq-{parameter.name}-{month}.pdf"'.replace(" ", "_")
    })
