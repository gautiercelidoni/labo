"""Métrologie : parc d'équipements, plans, réalisations, échéances, calendrier, exports."""
from __future__ import annotations

import calendar
from datetime import date, timedelta

import sqlalchemy as sa
from flask import Response, abort, flash, redirect, render_template, request, url_for

from app.blueprints.auth.forms import EmptyForm
from app.blueprints.metrologie import bp
from app.blueprints.metrologie.forms import ArchiveForm, EquipmentForm, EventForm, PlanForm, StatusForm, UploadForm
from app.domain.scheduling import DUE_STATE_LABELS, due_state
from app.extensions import db
from app.forms import lab_today, user_choices
from app.models.ciq import CIQParameter
from app.models.equipment import (
    EQUIPMENT_STATUS_LABELS,
    Equipment,
    MaintenanceEvent,
    MaintenancePlan,
)
from app.models.files import Attachment
from app.repositories.base import paginate, parse_uuid, repo
from app.security.permissions import P, require
from app.services import export_pdf, lab_service, metrology_service
from app.services.export_csv import csv_response
from app.services.file_storage import UploadError, store_upload
from app.services.membership_service import member_users
from app.services.metrology_service import MetrologyError


def _category_choices():
    return [("", "— Aucune —")] + [(str(c.id), c.name) for c in lab_service.list_categories()]


def _equipment_form_data(form: EquipmentForm) -> dict:
    return {k: getattr(form, k).data for k in metrology_service.EQUIPMENT_FIELDS}


@bp.route("/")
@require(P.METROLOGY_VIEW)
def index():
    args = request.args
    status = args.get("statut") if args.get("statut") in EQUIPMENT_STATUS_LABELS else None
    stmt = metrology_service.equipment_query(args.get("q"), status, parse_uuid(args.get("categorie")),
                                             include_archived=args.get("archives") == "1")
    page = paginate(stmt, args.get("page", 1, type=int), 25)
    ids = [e.id for e in page.items]
    next_due = {}
    if ids:
        rows = db.session.execute(
            sa.select(MaintenancePlan.equipment_id, sa.func.min(MaintenancePlan.next_due_on))
            .where(MaintenancePlan.equipment_id.in_(ids), MaintenancePlan.is_active.is_(True))
            .group_by(MaintenancePlan.equipment_id)
        ).all()
        next_due = dict(rows)
    today = lab_today()
    return render_template("metrologie/index.html", page=page, args=args, next_due=next_due, today=today,
                           due_state=due_state, due_labels=DUE_STATE_LABELS, status_labels=EQUIPMENT_STATUS_LABELS,
                           categories=lab_service.list_categories())


@bp.route("/equipements/export.csv")
@require(P.METROLOGY_VIEW, P.EXPORT_RUN)
def equipment_csv():
    items = db.session.scalars(metrology_service.equipment_query(include_archived=True)).all()
    rows = [[e.internal_id, e.name, e.category.name if e.category else "", e.manufacturer, e.model, e.serial_number,
             e.location, e.commissioned_on, e.status_label, e.criticality_label,
             e.responsible.full_name if e.responsible else "", e.archived_at] for e in items]
    return csv_response("equipements.csv", ["Identifiant", "Nom", "Catégorie", "Fabricant", "Modèle", "N° de série",
                                            "Localisation", "Mise en service", "Statut", "Criticité", "Responsable",
                                            "Archivé le"], rows)


@bp.route("/equipements/nouveau", methods=["GET", "POST"])
@require(P.METROLOGY_EQUIPMENT_EDIT)
def new_equipment():
    form = EquipmentForm()
    form.category_id.choices = _category_choices()
    form.responsible_id.choices = user_choices(member_users())
    if form.validate_on_submit():
        try:
            equipment = metrology_service.save_equipment(None, _equipment_form_data(form))
        except MetrologyError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Équipement créé.", "success")
            return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))
    return render_template("metrologie/equipment_form.html", form=form, equipment=None)


@bp.route("/equipements/<equipment_id>/modifier", methods=["GET", "POST"])
@require(P.METROLOGY_EQUIPMENT_EDIT)
def edit_equipment(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    form = EquipmentForm(obj=equipment)
    form.category_id.choices = _category_choices()
    form.responsible_id.choices = user_choices(member_users())
    if form.validate_on_submit():
        try:
            metrology_service.save_equipment(equipment, _equipment_form_data(form))
        except MetrologyError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Équipement modifié.", "success")
            return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))
    return render_template("metrologie/equipment_form.html", form=form, equipment=equipment)


@bp.route("/equipements/<equipment_id>")
@require(P.METROLOGY_VIEW)
def equipment(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    plans = repo(MaintenancePlan).all(MaintenancePlan.equipment_id == equipment.id,
                                      order_by=[MaintenancePlan.is_active.desc(), MaintenancePlan.next_due_on])
    events = metrology_service.equipment_history(equipment)
    event_ids = [e.id for e in events]
    attachments = repo(Attachment).all(Attachment.owner_type == "equipment", Attachment.owner_id == equipment.id,
                                       Attachment.archived_at.is_(None), order_by=Attachment.created_at.desc())
    event_files: dict = {}
    if event_ids:
        for att in repo(Attachment).all(Attachment.owner_type == "maintenance_event",
                                        Attachment.owner_id.in_(event_ids)):
            event_files.setdefault(att.owner_id, []).append(att)
    event_form = EventForm(performed_on=lab_today())
    event_form.plan_id.choices = [("", "— Non planifié —")] + [
        (str(p.id), f"{p.event_type_label} ({p.period_label})") for p in plans if p.is_active]
    if request.args.get("plan"):
        plan = next((p for p in plans if str(p.id) == request.args["plan"]), None)
        if plan:
            event_form.plan_id.data = plan.id
            event_form.event_type.data = plan.event_type
            event_form.provider.data = plan.provider
    parameters = repo(CIQParameter).all(CIQParameter.equipment_id == equipment.id, order_by=CIQParameter.name)
    today = lab_today()
    return render_template(
        "metrologie/equipment.html", equipment=equipment, plans=plans, events=events, attachments=attachments,
        event_files=event_files, event_form=event_form, status_form=StatusForm(status=equipment.status),
        archive_form=ArchiveForm(), upload_form=UploadForm(), parameters=parameters, today=today,
        due_state=due_state, due_labels=DUE_STATE_LABELS, suspend_form=EmptyForm(),
        suggest_suspension=request.args.get("suspendre") == "1",
    )


@bp.route("/equipements/<equipment_id>/statut", methods=["POST"])
@require(P.METROLOGY_EQUIPMENT_EDIT)
def change_status(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    form = StatusForm()
    if form.validate_on_submit():
        try:
            metrology_service.set_status(equipment, form.status.data, form.reason.data)
        except MetrologyError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Statut modifié.", "success")
    else:
        flash("Le motif est obligatoire.", "danger")
    return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))


@bp.route("/equipements/<equipment_id>/archiver", methods=["POST"])
@require(P.METROLOGY_EQUIPMENT_EDIT)
def archive(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    form = ArchiveForm()
    if form.validate_on_submit():
        try:
            metrology_service.archive_equipment(equipment, form.reason.data)
        except MetrologyError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Équipement archivé (réformé). Son historique est conservé.", "success")
    else:
        flash("Le motif est obligatoire.", "danger")
    return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))


@bp.route("/equipements/<equipment_id>/pieces-jointes", methods=["POST"])
@require(P.METROLOGY_VIEW, P.FILES_UPLOAD)
def upload(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    form = UploadForm()
    if form.validate_on_submit():
        try:
            store_upload(form.file.data, "equipment", equipment.id)
            db.session.commit()
        except UploadError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Pièce jointe ajoutée.", "success")
    else:
        flash("Aucun fichier sélectionné.", "danger")
    return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))


@bp.route("/equipements/<equipment_id>/plans/nouveau", methods=["GET", "POST"])
@require(P.METROLOGY_PLAN_EDIT)
def new_plan(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    form = PlanForm()
    form.responsible_id.choices = user_choices(member_users())
    if form.validate_on_submit():
        try:
            metrology_service.save_plan(equipment, None, form.data)
        except MetrologyError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Plan créé.", "success")
            return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))
    return render_template("metrologie/plan_form.html", form=form, equipment=equipment, plan=None)


@bp.route("/plans/<plan_id>/modifier", methods=["GET", "POST"])
@require(P.METROLOGY_PLAN_EDIT)
def edit_plan(plan_id):
    plan = repo(MaintenancePlan).get_or_404(plan_id)
    equipment = repo(Equipment).get_or_404(plan.equipment_id)
    form = PlanForm(obj=plan)
    form.responsible_id.choices = user_choices(member_users())
    if form.validate_on_submit():
        try:
            metrology_service.save_plan(equipment, plan, form.data)
        except MetrologyError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Plan modifié.", "success")
            return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))
    return render_template("metrologie/plan_form.html", form=form, equipment=equipment, plan=plan)


@bp.route("/equipements/<equipment_id>/realisations", methods=["POST"])
@require(P.METROLOGY_EVENT_CREATE)
def record_event(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    plans = repo(MaintenancePlan).all(MaintenancePlan.equipment_id == equipment.id, MaintenancePlan.is_active.is_(True))
    form = EventForm()
    form.plan_id.choices = [("", "")] + [(str(p.id), "") for p in plans]
    if form.validate_on_submit():
        plan = repo(MaintenancePlan).get(form.plan_id.data) if form.plan_id.data else None
        try:
            result = metrology_service.record_event(
                equipment, plan, event_type=form.event_type.data, performed_on=form.performed_on.data,
                outcome=form.outcome.data or None, provider=form.provider.data, comment=form.comment.data,
                attachment=form.attachment.data,
            )
        except (MetrologyError, UploadError) as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Réalisation enregistrée. Prochaine échéance recalculée depuis la date effective.", "success")
            if result.suggest_suspension:
                flash("Étalonnage non conforme : il est recommandé de passer l'équipement en « suspendu ».", "warning")
                return redirect(url_for("metrologie.equipment", equipment_id=equipment.id, suspendre=1))
    else:
        flash("Réalisation invalide : " + "; ".join(e for errs in form.errors.values() for e in errs), "danger")
    return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))


@bp.route("/equipements/<equipment_id>/suspendre", methods=["POST"])
@require(P.METROLOGY_EVENT_CREATE)
def suspend(equipment_id):
    """Suspension proposée après un étalonnage non conforme (accessible au technicien)."""
    equipment = repo(Equipment).get_or_404(equipment_id)
    if not EmptyForm().validate_on_submit():
        abort(400)
    last = repo(MaintenanceEvent).first(MaintenanceEvent.equipment_id == equipment.id,
                                        MaintenanceEvent.event_type == "calibration",
                                        MaintenanceEvent.outcome == "non_conform")
    if last is None:
        abort(403)
    metrology_service.set_status(equipment, "suspended", "Étalonnage non conforme")
    flash("Équipement suspendu.", "success")
    return redirect(url_for("metrologie.equipment", equipment_id=equipment.id))


def _due_filter():
    today = lab_today()
    window = request.args.get("filtre", "30")
    if window == "retard":
        return metrology_service.plans_query(overdue_before=today), window, today
    if window == "tous":
        return metrology_service.plans_query(), window, today
    days = {"7": 7, "30": 30, "90": 90}.get(window, 30)
    return metrology_service.plans_query(until=today + timedelta(days=days)), str(days), today


@bp.route("/echeances")
@require(P.METROLOGY_VIEW)
def due():
    stmt, window, today = _due_filter()
    page = paginate(stmt, request.args.get("page", 1, type=int), 50)
    return render_template("metrologie/due.html", page=page, window=window, today=today, due_state=due_state,
                           due_labels=DUE_STATE_LABELS)


@bp.route("/echeances/export.csv")
@require(P.METROLOGY_VIEW, P.EXPORT_RUN)
def due_csv():
    stmt, window, today = _due_filter()
    plans = db.session.scalars(stmt).all()
    rows = [[p.equipment.internal_id, p.equipment.name, p.event_type_label, p.period_label, p.provider,
             p.responsible.full_name if p.responsible else "", p.last_done_on, p.next_due_on,
             DUE_STATE_LABELS[due_state(p.next_due_on, today)]] for p in plans]
    return csv_response("echeances-metrologie.csv", ["Identifiant", "Équipement", "Type", "Périodicité", "Prestataire",
                                                      "Responsable", "Dernière réalisation", "Prochaine échéance",
                                                      "État"], rows, {"filtre": window})


@bp.route("/echeances/planning.pdf")
@require(P.METROLOGY_VIEW, P.EXPORT_RUN)
def planning_pdf():
    stmt, window, today = _due_filter()
    plans = db.session.scalars(stmt).all()
    pdf = export_pdf.metrology_planning(plans, window)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="planning-metrologie.pdf"'})


@bp.route("/calendrier")
@require(P.METROLOGY_VIEW)
def calendar_view():
    today = lab_today()
    try:
        year, month = (int(x) for x in request.args.get("mois", today.strftime("%Y-%m")).split("-"))
        first = date(year, month, 1)
    except ValueError:
        abort(400)
    last = date(year, month, calendar.monthrange(year, month)[1])
    plans = db.session.scalars(
        metrology_service.plans_query().where(MaintenancePlan.next_due_on >= first, MaintenancePlan.next_due_on <= last)
    ).all()
    by_day: dict[date, list] = {}
    for plan in plans:
        by_day.setdefault(plan.next_due_on, []).append(plan)
    weeks = calendar.Calendar(firstweekday=0).monthdatescalendar(year, month)
    prev_month = (first - timedelta(days=1)).strftime("%Y-%m")
    next_month = (last + timedelta(days=1)).strftime("%Y-%m")
    return render_template("metrologie/calendar.html", weeks=weeks, by_day=by_day, first=first, today=today,
                           prev_month=prev_month, next_month=next_month, due_state=due_state)


@bp.route("/equipements/<equipment_id>/historique.pdf")
@require(P.METROLOGY_VIEW, P.EXPORT_RUN)
def equipment_pdf(equipment_id):
    equipment = repo(Equipment).get_or_404(equipment_id)
    pdf = export_pdf.equipment_report(equipment)
    return Response(pdf, mimetype="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="historique-{equipment.internal_id}.pdf"'.replace(" ", "_")})

