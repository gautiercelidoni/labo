"""Administration du laboratoire : paramètres, membres, invitations, équipes, catégories, exports."""
from __future__ import annotations

from flask import Response, abort, flash, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.admin import bp
from app.blueprints.admin.forms import CategoryForm, InvitationForm, LabSettingsForm, MembershipForm, TeamForm
from app.blueprints.auth.forms import EmptyForm
from app.extensions import db
from app.models.tenant import Invitation, Membership, Team
from app.repositories.base import repo
from app.security.permissions import P, require
from app.services import lab_service, membership_service
from app.services.file_storage import UploadError
from app.services.lab_service import LabError
from app.services.membership_service import MembershipError


def _team_choices():
    return [("", "— Aucune —")] + [(str(t.id), t.name) for t in membership_service.list_teams()]


@bp.route("/", methods=["GET", "POST"])
@require(P.LAB_SETTINGS)
def settings():
    lab = lab_service.get_current_lab()
    form = LabSettingsForm(obj=lab)
    if request.method == "GET":
        form.siret.data = lab.legal_info.get("siret")
        form.address.data = lab.legal_info.get("address")
        form.accreditation.data = lab.legal_info.get("accreditation")
    if form.validate_on_submit():
        try:
            lab_service.update_settings(form.data, form.logo.data)
        except UploadError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Paramètres du laboratoire enregistrés.", "success")
            return redirect(url_for("admin.settings"))
    return render_template("admin/settings.html", form=form, lab=lab)


@bp.route("/membres")
@require(P.USERS_MANAGE)
def members():
    invitations = repo(Invitation).all(Invitation.accepted_at.is_(None), Invitation.revoked_at.is_(None),
                                       order_by=Invitation.created_at.desc())
    form = InvitationForm()
    form.team_id.choices = _team_choices()
    lab = lab_service.get_current_lab()
    return render_template(
        "admin/members.html",
        members=membership_service.list_members(),
        invitations=[i for i in invitations if i.is_pending],
        form=form,
        action_form=EmptyForm(),
        active_count=membership_service.active_member_count(),
        lab=lab,
    )


@bp.route("/membres/inviter", methods=["POST"])
@require(P.USERS_MANAGE)
def invite():
    form = InvitationForm()
    form.team_id.choices = _team_choices()
    if not form.validate_on_submit():
        flash("Invitation invalide : " + "; ".join(e for errs in form.errors.values() for e in errs), "danger")
        return redirect(url_for("admin.members"))
    try:
        _, token = membership_service.invite(form.email.data, form.role.data, form.team_id.data)
    except MembershipError as e:
        db.session.rollback()
        flash(str(e), "danger")
    else:
        flash(f"Invitation envoyée à {form.email.data}.", "success")
    return redirect(url_for("admin.members"))


@bp.route("/invitations/<invitation_id>/revoquer", methods=["POST"])
@require(P.USERS_MANAGE)
def revoke_invitation(invitation_id):
    if not EmptyForm().validate_on_submit():
        abort(400)
    invitation = repo(Invitation).get_or_404(invitation_id)
    try:
        membership_service.revoke_invitation(invitation)
    except MembershipError as e:
        flash(str(e), "danger")
    else:
        flash("Invitation révoquée.", "success")
    return redirect(url_for("admin.members"))


@bp.route("/membres/<membership_id>", methods=["GET", "POST"])
@require(P.USERS_MANAGE)
def edit_member(membership_id):
    membership = repo(Membership).get_or_404(membership_id)
    form = MembershipForm(obj=membership)
    form.team_id.choices = _team_choices()
    if form.validate_on_submit():
        try:
            membership_service.update_membership(membership, form.role.data, form.team_id.data)
        except MembershipError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Droits mis à jour.", "success")
            return redirect(url_for("admin.members"))
    return render_template("admin/member_edit.html", form=form, membership=membership, action_form=EmptyForm())


@bp.route("/membres/<membership_id>/activation", methods=["POST"])
@require(P.USERS_MANAGE)
def toggle_member(membership_id):
    if not EmptyForm().validate_on_submit():
        abort(400)
    membership = repo(Membership).get_or_404(membership_id)
    if membership.user_id == current_user.id and membership.is_active:
        flash("Vous ne pouvez pas désactiver votre propre accès.", "danger")
        return redirect(url_for("admin.members"))
    try:
        membership_service.set_active(membership, not membership.is_active)
    except MembershipError as e:
        db.session.rollback()
        flash(str(e), "danger")
    else:
        flash("Accès mis à jour.", "success")
    return redirect(url_for("admin.members"))


@bp.route("/equipes", methods=["GET", "POST"])
@require(P.USERS_MANAGE)
def teams():
    form = TeamForm()
    if form.validate_on_submit():
        try:
            membership_service.save_team(None, form.name.data, form.description.data)
        except MembershipError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Équipe créée.", "success")
            return redirect(url_for("admin.teams"))
    return render_template("admin/teams.html", form=form, teams=membership_service.list_teams())


@bp.route("/equipes/<team_id>", methods=["GET", "POST"])
@require(P.USERS_MANAGE)
def edit_team(team_id):
    team = repo(Team).get_or_404(team_id)
    form = TeamForm(obj=team)
    if form.validate_on_submit():
        try:
            membership_service.save_team(team, form.name.data, form.description.data)
        except MembershipError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Équipe modifiée.", "success")
            return redirect(url_for("admin.teams"))
    return render_template("admin/team_edit.html", form=form, team=team)


@bp.route("/categories", methods=["GET", "POST"])
@require(P.LAB_SETTINGS)
def categories():
    form = CategoryForm()
    if form.validate_on_submit():
        try:
            lab_service.add_category(form.name.data)
        except LabError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            flash("Catégorie ajoutée.", "success")
            return redirect(url_for("admin.categories"))
    return render_template("admin/categories.html", form=form, categories=lab_service.list_categories())


@bp.route("/export-donnees", methods=["GET", "POST"])
@require(P.LAB_SETTINGS, P.EXPORT_RUN)
def export_data():
    form = EmptyForm()
    if form.validate_on_submit():
        data = lab_service.export_lab_data()
        lab = lab_service.get_current_lab()
        return Response(
            data,
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="export-{lab.slug}.zip"'},
        )
    return render_template("admin/export.html", form=form)
