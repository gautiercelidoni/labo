"""Cahier de transmission (V1.1)."""
from __future__ import annotations

import sqlalchemy as sa
from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user
from flask_wtf import FlaskForm
from flask_wtf.file import MultipleFileField
from wtforms import DateField, SelectField, SelectMultipleField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional

from app.blueprints.transmissions import bp
from app.extensions import db
from app.models.files import Attachment
from app.models.transmissions import (
    CATEGORY_LABELS,
    PRIORITY_LABELS,
    TRANSMISSION_STATUS_LABELS,
    Transmission,
)
from app.models.user import User
from app.repositories.base import paginate, parse_uuid, repo
from app.security.permissions import P, can, require
from app.services import transmission_service as ts
from app.services.file_storage import UploadError, store_upload
from app.services.membership_service import list_teams, member_users
from app.services.transmission_service import TransmissionError


class TransmissionForm(FlaskForm):
    title = StringField("Titre", validators=[DataRequired(), Length(max=200)])
    body = TextAreaField("Contenu", validators=[DataRequired(), Length(max=20000)])
    category = SelectField("Catégorie", choices=list(CATEGORY_LABELS.items()))
    priority = SelectField("Priorité", choices=list(PRIORITY_LABELS.items()))
    due_on = DateField("Échéance (facultative)", validators=[Optional()])
    users = SelectMultipleField("Destinataires (personnes)")
    teams = SelectMultipleField("Destinataires (équipes / postes)")
    files = MultipleFileField("Pièces jointes")


class StatusForm(FlaskForm):
    status = SelectField("Nouveau statut", choices=[(k, v) for k, v in TRANSMISSION_STATUS_LABELS.items()
                                                    if k not in ("new", "read")])
    comment = TextAreaField("Commentaire (facultatif)", validators=[Optional(), Length(max=4000)])


class CommentForm(FlaskForm):
    body = TextAreaField("Commentaire de suivi", validators=[DataRequired(), Length(max=4000)])


class UploadForm(FlaskForm):
    files = MultipleFileField("Ajouter des pièces jointes")


def _get_visible(transmission_id) -> Transmission:
    t = repo(Transmission).get_or_404(transmission_id)
    if not ts.can_view(t):
        abort(404)
    return t


@bp.route("/")
@require(P.TRANSMISSION_VIEW)
def index():
    args = request.args
    box = args.get("boite", "recues")
    if box == "toutes" and not can(P.TRANSMISSION_VIEW_ALL):
        box = "recues"
    stmt = ts.list_query(box, status=args.get("statut"), priority=args.get("priorite"),
                         category=args.get("categorie"), unread_only=args.get("non_lues") == "1")
    page = paginate(stmt, args.get("page", 1, type=int), 25)
    read = ts.read_ids([t.id for t in page.items])
    return render_template("transmissions/index.html", page=page, box=box, args=args, read=read,
                           status_labels=TRANSMISSION_STATUS_LABELS, priority_labels=PRIORITY_LABELS,
                           category_labels=CATEGORY_LABELS)


@bp.route("/nouvelle", methods=["GET", "POST"])
@require(P.TRANSMISSION_CREATE)
def new():
    form = TransmissionForm()
    form.users.choices = [(str(u.id), u.full_name) for u in member_users()]
    form.teams.choices = [(str(t.id), t.name) for t in list_teams()]
    if form.validate_on_submit():
        try:
            t = ts.create(title=form.title.data, body=form.body.data, category=form.category.data,
                          priority=form.priority.data, due_on=form.due_on.data,
                          user_ids=[u for u in (parse_uuid(x) for x in form.users.data) if u],
                          team_ids=[t for t in (parse_uuid(x) for x in form.teams.data) if t],
                          attachments=form.files.data or [])
        except (TransmissionError, UploadError) as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Transmission envoyée.", "success")
            return redirect(url_for("transmissions.detail", transmission_id=t.id))
    return render_template("transmissions/new.html", form=form)


@bp.route("/<transmission_id>")
@require(P.TRANSMISSION_VIEW)
def detail(transmission_id):
    t = _get_visible(transmission_id)
    if ts.mark_read(t):
        db.session.refresh(t)
    expected = ts.expected_readers(t)
    users = {u.id: u for u in db.session.scalars(sa.select(User).where(User.id.in_(expected))).all()} if expected else {}
    receipts = ts.receipts(t)
    attachments = repo(Attachment).all(Attachment.owner_type == "transmission", Attachment.owner_id == t.id,
                                       order_by=Attachment.created_at)
    can_act = can(P.TRANSMISSION_UPDATE) and (t.author_id == current_user.id or ts.is_recipient(t)
                                              or can(P.TRANSMISSION_VIEW_ALL))
    status_form = StatusForm()
    status_form.status.choices = [(s, TRANSMISSION_STATUS_LABELS[s]) for s in sorted(ts.TRANSITIONS.get(t.status, []))]
    return render_template("transmissions/detail.html", t=t, receipts=receipts, expected=users,
                           comments=ts.comments(t), attachments=attachments, status_form=status_form,
                           comment_form=CommentForm(), upload_form=UploadForm(), can_act=can_act,
                           read_by={r.user_id for r in receipts})


@bp.route("/<transmission_id>/statut", methods=["POST"])
@require(P.TRANSMISSION_UPDATE)
def change_status(transmission_id):
    t = _get_visible(transmission_id)
    form = StatusForm()
    form.status.choices = [(s, s) for s in ts.TRANSITIONS.get(t.status, [])]
    if form.validate_on_submit():
        try:
            ts.change_status(t, form.status.data, form.comment.data)
        except TransmissionError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Statut mis à jour.", "success")
    else:
        flash("Changement de statut invalide.", "danger")
    return redirect(url_for("transmissions.detail", transmission_id=t.id))


@bp.route("/<transmission_id>/commentaires", methods=["POST"])
@require(P.TRANSMISSION_UPDATE)
def comment(transmission_id):
    t = _get_visible(transmission_id)
    form = CommentForm()
    if form.validate_on_submit():
        try:
            ts.add_comment(t, form.body.data)
        except TransmissionError as e:
            db.session.rollback()
            flash(str(e), "danger")
    return redirect(url_for("transmissions.detail", transmission_id=t.id))


@bp.route("/<transmission_id>/pieces-jointes", methods=["POST"])
@require(P.TRANSMISSION_UPDATE, P.FILES_UPLOAD)
def upload(transmission_id):
    t = _get_visible(transmission_id)
    form = UploadForm()
    if form.validate_on_submit():
        try:
            for f in form.files.data or []:
                if f and f.filename:
                    store_upload(f, "transmission", t.id)
            db.session.commit()
        except UploadError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Pièces jointes ajoutées.", "success")
    return redirect(url_for("transmissions.detail", transmission_id=t.id))
