"""Actions correctives : liste filtrée, fiche, réalisation, validation."""
from __future__ import annotations

from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from wtforms import DateField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.blueprints.actions import bp
from app.extensions import db
from app.forms import UUIDSelectField, lab_today, user_choices
from app.models.actions import CA_STATUS_LABELS, CorrectiveAction
from app.models.ciq import CIQRun
from app.models.non_conformities import NonConformity
from app.repositories.base import paginate, repo
from app.security.permissions import P, can, require
from app.services import corrective_action_service as cas
from app.services.corrective_action_service import ActionError
from app.services.export_csv import csv_response
from app.services.membership_service import member_users


class ActionForm(FlaskForm):
    description = TextAreaField("Description", validators=[DataRequired(), Length(max=4000)])
    responsible_id = UUIDSelectField("Responsable")
    due_on = DateField("Échéance", validators=[Optional()])


class CompleteForm(FlaskForm):
    done_on = DateField("Réalisée le", validators=[DataRequired()])
    comment = TextAreaField("Ce qui a été fait", validators=[DataRequired(), Length(max=4000)])


class ValidateForm(FlaskForm):
    comment = TextAreaField("Commentaire de validation", validators=[Optional(), Length(max=2000)])


class ReopenForm(FlaskForm):
    reason = TextAreaField("Motif de réouverture", validators=[DataRequired(), Length(max=2000)])


def _query_from_args():
    args = request.args
    status = args.get("statut") if args.get("statut") in CA_STATUS_LABELS else None
    overdue = lab_today() if args.get("retard") == "1" else None
    responsible = current_user.id if args.get("mes") == "1" else None
    return cas.list_query(status, overdue, responsible)


@bp.route("/")
@require(P.CA_VIEW)
def index():
    page = paginate(_query_from_args(), request.args.get("page", 1, type=int), 25)
    return render_template("actions/index.html", page=page, args=request.args, status_labels=CA_STATUS_LABELS,
                           today=lab_today())


@bp.route("/export.csv")
@require(P.CA_VIEW, P.EXPORT_RUN)
def export():
    actions = db.session.scalars(_query_from_args()).all()
    rows = [[a.created_at, a.source_type, a.description, a.responsible.full_name if a.responsible else "",
             a.due_on, a.status_label, a.done_on, a.done_comment,
             a.validated_by.full_name if a.validated_by else "", a.validated_at] for a in actions]
    return csv_response("actions-correctives.csv",
                        ["Créée le", "Origine", "Description", "Responsable", "Échéance", "Statut", "Réalisée le",
                         "Réalisation", "Validée par", "Validée le"], rows, dict(request.args))


@bp.route("/nouvelle", methods=["GET", "POST"])
@require(P.CA_CREATE)
def new():
    form = ActionForm()
    form.responsible_id.choices = user_choices(member_users())
    if form.validate_on_submit():
        try:
            action = cas.create(source_type="manual", source_id=None, description=form.description.data,
                                responsible_id=form.responsible_id.data, due_on=form.due_on.data)
        except ActionError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Action corrective créée.", "success")
            return redirect(url_for("actions.detail", action_id=action.id))
    return render_template("actions/new.html", form=form)


@bp.route("/<action_id>")
@require(P.CA_VIEW)
def detail(action_id):
    action = repo(CorrectiveAction).get_or_404(action_id)
    source = None
    if action.source_type == "ciq_run" and action.source_id:
        source = repo(CIQRun).get(action.source_id)
    elif action.source_type == "non_conformity" and action.source_id:
        source = repo(NonConformity).get(action.source_id)
    return render_template("actions/detail.html", action=action, source=source,
                           complete_form=CompleteForm(done_on=lab_today()), validate_form=ValidateForm(),
                           reopen_form=ReopenForm(), can_complete=cas.can_complete(action),
                           can_validate=can(P.CA_VALIDATE))


@bp.route("/<action_id>/realisee", methods=["POST"])
@require(P.CA_COMPLETE)
def complete(action_id):
    action = repo(CorrectiveAction).get_or_404(action_id)
    form = CompleteForm()
    if form.validate_on_submit():
        try:
            cas.complete(action, form.done_on.data, form.comment.data)
        except ActionError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Action déclarée réalisée.", "success")
    else:
        flash("Formulaire incomplet.", "danger")
    return redirect(url_for("actions.detail", action_id=action.id))


@bp.route("/<action_id>/valider", methods=["POST"])
@require(P.CA_VALIDATE)
def validate(action_id):
    action = repo(CorrectiveAction).get_or_404(action_id)
    form = ValidateForm()
    if form.validate_on_submit():
        try:
            cas.validate(action, form.comment.data)
        except ActionError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Action validée.", "success")
    return redirect(url_for("actions.detail", action_id=action.id))


@bp.route("/<action_id>/rouvrir", methods=["POST"])
@require(P.CA_VALIDATE)
def reopen(action_id):
    action = repo(CorrectiveAction).get_or_404(action_id)
    form = ReopenForm()
    if form.validate_on_submit():
        try:
            cas.reopen(action, form.reason.data)
        except ActionError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Action rouverte.", "success")
    else:
        flash("Le motif est obligatoire.", "danger")
    return redirect(url_for("actions.detail", action_id=action.id))
