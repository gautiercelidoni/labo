"""Non-conformités (V1.1)."""
from __future__ import annotations

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import DateField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.blueprints.auth.forms import EmptyForm
from app.blueprints.non_conformites import bp
from app.extensions import db
from app.forms import UUIDSelectField, lab_today, user_choices
from app.models.ciq import CIQRun
from app.models.equipment import Equipment
from app.models.files import Attachment
from app.models.non_conformities import (
    NC_ORIGIN_LABELS,
    NC_SEVERITY_LABELS,
    NC_STATUS_LABELS,
    NonConformity,
)
from app.repositories.base import paginate, repo
from app.security.permissions import P, can, require
from app.services import corrective_action_service as cas
from app.services import export_pdf, nc_service
from app.services.corrective_action_service import ActionError
from app.services.export_csv import csv_response
from app.services.file_storage import UploadError, store_upload
from app.services.membership_service import member_users
from app.services.nc_service import NCError


class NCForm(FlaskForm):
    title = StringField("Titre", validators=[DataRequired(), Length(max=200)])
    description = TextAreaField("Description", validators=[DataRequired(), Length(max=20000)])
    detected_on = DateField("Date de détection", validators=[DataRequired()])
    origin = SelectField("Origine", choices=list(NC_ORIGIN_LABELS.items()))
    severity = SelectField("Gravité", choices=list(NC_SEVERITY_LABELS.items()))
    equipment_id = UUIDSelectField("Équipement concerné")
    impact = TextAreaField("Impact", validators=[Optional(), Length(max=10000)])
    immediate_action = TextAreaField("Mesure immédiate", validators=[Optional(), Length(max=10000)])
    responsible_id = UUIDSelectField("Responsable")
    target_date = DateField("Date cible", validators=[Optional()])


class AnalysisForm(FlaskForm):
    root_cause = TextAreaField("Analyse de cause", validators=[Optional(), Length(max=20000)])
    no_action_justification = TextAreaField("Justification de non-action (si aucune action corrective)",
                                            validators=[Optional(), Length(max=10000)])
    effectiveness_check = TextAreaField("Vérification d'efficacité", validators=[Optional(), Length(max=10000)])
    effectiveness_justification = TextAreaField("Justification d'absence de vérification d'efficacité",
                                                validators=[Optional(), Length(max=10000)])


class StatusForm(FlaskForm):
    status = SelectField("Nouveau statut", choices=[])
    comment = TextAreaField("Commentaire (obligatoire pour une annulation)", validators=[Optional(), Length(max=4000)])


class ActionForm(FlaskForm):
    description = TextAreaField("Action corrective", validators=[DataRequired(), Length(max=4000)])
    responsible_id = UUIDSelectField("Responsable")
    due_on = DateField("Échéance", validators=[Optional()])


class UploadForm(FlaskForm):
    file = FileField("Pièce jointe", validators=[DataRequired()])


def _choices(form):
    if hasattr(form, "equipment_id"):
        form.equipment_id.choices = [("", "— Aucun —")] + [
            (str(e.id), f"{e.name} ({e.internal_id})") for e in repo(Equipment).all(order_by=Equipment.name)]
    form.responsible_id.choices = user_choices(member_users())


def _query():
    args = request.args
    year = args.get("annee", type=int)
    return nc_service.list_query(args.get("statut"), args.get("gravite"), args.get("origine"), year)


@bp.route("/")
@require(P.NC_VIEW)
def index():
    page = paginate(_query(), request.args.get("page", 1, type=int), 25)
    return render_template("non_conformites/index.html", page=page, args=request.args,
                           status_labels=NC_STATUS_LABELS, severity_labels=NC_SEVERITY_LABELS,
                           origin_labels=NC_ORIGIN_LABELS)


@bp.route("/export.csv")
@require(P.NC_VIEW, P.EXPORT_RUN)
def export_csv():
    items = db.session.scalars(_query()).all()
    rows = [[n.number, n.title, n.detected_on, n.origin_label, n.severity_label, n.status_label,
             n.responsible.full_name if n.responsible else "", n.target_date, n.root_cause, n.closed_on]
            for n in items]
    return csv_response("non-conformites.csv", ["Numéro", "Titre", "Détectée le", "Origine", "Gravité", "Statut",
                                                "Responsable", "Date cible", "Analyse de cause", "Clôturée le"],
                        rows, dict(request.args))


@bp.route("/liste.pdf")
@require(P.NC_VIEW, P.EXPORT_RUN)
def export_pdf_list():
    items = db.session.scalars(_query()).all()
    pdf = export_pdf.nc_report(items, period="Toutes périodes", filters=dict(request.args))
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": 'attachment; filename="non-conformites.pdf"'})


@bp.route("/nouvelle", methods=["GET", "POST"])
@require(P.NC_CREATE)
def new():
    form = NCForm(detected_on=lab_today())
    _choices(form)
    if form.validate_on_submit():
        data = {k: v for k, v in form.data.items() if k in nc_service.EDITABLE_FIELDS}
        try:
            nc = nc_service.create(data)
        except NCError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash(f"Non-conformité {nc.number} créée (brouillon).", "success")
            return redirect(url_for("non_conformites.detail", nc_id=nc.id))
    return render_template("non_conformites/new.html", form=form)


@bp.route("/depuis-serie/<run_id>", methods=["POST"])
@require(P.NC_CREATE)
def from_run(run_id):
    if not EmptyForm().validate_on_submit():
        abort(400)
    run = repo(CIQRun).get_or_404(run_id)
    try:
        nc = nc_service.create_from_run(run)
    except NCError as e:
        db.session.rollback()
        flash(str(e), "danger")
        return redirect(url_for("ciq.run", run_id=run.id))
    flash(f"Non-conformité {nc.number} liée à la série.", "success")
    return redirect(url_for("non_conformites.detail", nc_id=nc.id))


@bp.route("/<nc_id>")
@require(P.NC_VIEW)
def detail(nc_id):
    nc = repo(NonConformity).get_or_404(nc_id)
    form = NCForm(obj=nc)
    _choices(form)
    analysis = AnalysisForm(obj=nc)
    status_form = StatusForm()
    targets = sorted(nc_service.TRANSITIONS.get(nc.status, set()))
    if nc.status not in nc_service.TERMINAL:
        targets.append("cancelled")
    status_form.status.choices = [(s, NC_STATUS_LABELS[s]) for s in targets]
    action_form = ActionForm()
    _choices(action_form)
    attachments = repo(Attachment).all(Attachment.owner_type == "non_conformity", Attachment.owner_id == nc.id,
                                       order_by=Attachment.created_at)
    return render_template(
        "non_conformites/detail.html", nc=nc, form=form, analysis=analysis, status_form=status_form,
        action_form=action_form, upload_form=UploadForm(), actions=nc_service.linked_actions(nc),
        history=nc_service.history(nc), attachments=attachments, status_labels=NC_STATUS_LABELS,
        closure_problems=nc_service.closure_problems(nc), editable=nc.status not in nc_service.TERMINAL,
        source_run=repo(CIQRun).get(nc.source_ciq_run_id) if nc.source_ciq_run_id else None,
    )


def _can_edit(nc: NonConformity) -> bool:
    return can(P.NC_EDIT) or (nc.status == "draft" and nc.created_by_id == current_user.id)


@bp.route("/<nc_id>/modifier", methods=["POST"])
@require(P.NC_CREATE)
def edit(nc_id):
    nc = repo(NonConformity).get_or_404(nc_id)
    if not _can_edit(nc):
        abort(403)
    form = NCForm()
    _choices(form)
    if form.validate_on_submit():
        try:
            nc_service.update(nc, {k: v for k, v in form.data.items() if k in nc_service.EDITABLE_FIELDS})
        except NCError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Non-conformité mise à jour.", "success")
    else:
        flash("Formulaire invalide.", "danger")
    return redirect(url_for("non_conformites.detail", nc_id=nc.id))


@bp.route("/<nc_id>/analyse", methods=["POST"])
@require(P.NC_EDIT)
def edit_analysis(nc_id):
    nc = repo(NonConformity).get_or_404(nc_id)
    form = AnalysisForm()
    if form.validate_on_submit():
        try:
            nc_service.update(nc, form.data)
        except NCError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Analyse enregistrée.", "success")
    return redirect(url_for("non_conformites.detail", nc_id=nc.id))


@bp.route("/<nc_id>/statut", methods=["POST"])
@require(P.NC_CREATE)
def change_status(nc_id):
    nc = repo(NonConformity).get_or_404(nc_id)
    form = StatusForm()
    form.status.choices = [(s, s) for s in list(nc_service.TRANSITIONS.get(nc.status, set())) + ["cancelled"]]
    if form.validate_on_submit():
        if nc.status == "draft" and not _can_edit(nc):
            abort(403)
        try:
            nc_service.change_status(nc, form.status.data, form.comment.data)
        except NCError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash(f"Statut : {NC_STATUS_LABELS[nc.status]}.", "success")
    else:
        flash("Statut invalide.", "danger")
    return redirect(url_for("non_conformites.detail", nc_id=nc.id))


@bp.route("/<nc_id>/actions", methods=["POST"])
@require(P.NC_EDIT, P.CA_CREATE)
def add_action(nc_id):
    nc = repo(NonConformity).get_or_404(nc_id)
    if nc.status in nc_service.TERMINAL:
        abort(403)
    form = ActionForm()
    _choices(form)
    if form.validate_on_submit():
        try:
            cas.create(source_type="non_conformity", source_id=nc.id, description=form.description.data,
                       responsible_id=form.responsible_id.data, due_on=form.due_on.data)
        except ActionError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Action corrective ajoutée.", "success")
    return redirect(url_for("non_conformites.detail", nc_id=nc.id))


@bp.route("/<nc_id>/pieces-jointes", methods=["POST"])
@require(P.NC_CREATE, P.FILES_UPLOAD)
def upload(nc_id):
    nc = repo(NonConformity).get_or_404(nc_id)
    form = UploadForm()
    if form.validate_on_submit():
        try:
            store_upload(form.file.data, "non_conformity", nc.id)
            db.session.commit()
        except UploadError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Pièce jointe ajoutée.", "success")
    return redirect(url_for("non_conformites.detail", nc_id=nc.id))


@bp.route("/<nc_id>/fiche.pdf")
@require(P.NC_VIEW, P.EXPORT_RUN)
def sheet_pdf(nc_id):
    nc = repo(NonConformity).get_or_404(nc_id)
    pdf = export_pdf.nc_report([nc], single=True, period=f"Détectée le {nc.detected_on.strftime('%d/%m/%Y')}",
                               filters={"numéro": nc.number}, actions=nc_service.linked_actions(nc),
                               history=nc_service.history(nc), status_labels=NC_STATUS_LABELS)
    return Response(pdf, mimetype="application/pdf",
                    headers={"Content-Disposition": f'attachment; filename="{nc.number}.pdf"'})

