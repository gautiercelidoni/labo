"""Connexion, déconnexion, mots de passe, invitations, sélecteur de laboratoire, inscription."""
from __future__ import annotations

from urllib.parse import urlsplit

import sqlalchemy as sa

from flask import abort, current_app, flash, g, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required

from app.blueprints.auth import bp
from app.blueprints.auth.forms import (
    AcceptInvitationForm,
    ChangePasswordForm,
    EmptyForm,
    ForgotPasswordForm,
    LoginForm,
    NewPasswordForm,
    SignupForm,
)
from app.extensions import db
from app.models.tenant import ROLE_LABELS, Laboratory
from app.repositories.base import parse_uuid
from app.services import audit_service, auth_service, membership_service
from app.services.audit_service import client_ip
from app.services.auth_service import AuthError
from app.services.membership_service import MembershipError


def _safe_next(target: str | None) -> str | None:
    if not target:
        return None
    parts = urlsplit(target)
    if parts.scheme or parts.netloc or not target.startswith("/") or target.startswith("//"):
        return None
    return target


def _after_login_redirect(user, next_url: str | None):
    memberships = auth_service.memberships_for_user(user.id)
    if len(memberships) == 1:
        auth_service.activate_lab(user, memberships[0].tenant_id, audit=False)
        auth_service.login_audit(user, memberships[0].tenant_id)
        db.session.commit()
        return redirect(next_url or url_for("dashboard.index"))
    auth_service.login_audit(user, None)
    db.session.commit()
    return redirect(url_for("auth.select_lab"))


@bp.route("/connexion", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    form = LoginForm()
    if form.validate_on_submit():
        try:
            user = auth_service.authenticate(form.email.data, form.password.data, client_ip())
        except AuthError as e:
            flash(str(e), "danger")
            return render_template("auth/login.html", form=form), 200
        auth_service.start_session(user, remember=form.remember.data)
        return _after_login_redirect(user, _safe_next(request.args.get("next")))
    return render_template(
        "auth/login.html",
        form=form,
        show_demo=current_app.config["SHOW_DEMO_CREDENTIALS"],
        signup_enabled=current_app.config["SELF_SIGNUP_ENABLED"],
    )


@bp.route("/deconnexion", methods=["POST"])
@login_required
def logout():
    lab_id = parse_uuid(session.get("active_lab_id"))
    audit_service.record("auth.logout", object_type="user", object_id=current_user.id, tenant_id=lab_id)
    db.session.commit()
    auth_service.end_session()
    flash("Vous êtes déconnecté.", "info")
    return redirect(url_for("auth.login"))


@bp.route("/laboratoires")
@login_required
def select_lab():
    memberships = auth_service.memberships_for_user(current_user.id)
    return render_template("auth/select_lab.html", memberships=memberships, form=EmptyForm(),
                           role_labels=ROLE_LABELS)


@bp.route("/laboratoires/<lab_id>/activer", methods=["POST"])
@login_required
def activate_lab(lab_id):
    form = EmptyForm()
    if not form.validate_on_submit():
        abort(400)
    lid = parse_uuid(lab_id)
    if lid is None:
        abort(404)
    membership = auth_service.activate_lab(current_user, lid)
    if membership is None:
        abort(404)
    db.session.commit()
    flash("Laboratoire actif modifié.", "success")
    return redirect(url_for("dashboard.index"))


@bp.route("/mot-de-passe-oublie", methods=["GET", "POST"])
def forgot_password():
    form = ForgotPasswordForm()
    if form.validate_on_submit():
        auth_service.request_password_reset(form.email.data)
        flash("Si un compte existe pour cet email, un lien de réinitialisation vient d'être envoyé.", "info")
        return redirect(url_for("auth.login"))
    return render_template("auth/forgot_password.html", form=form)


@bp.route("/reinitialiser/<token>", methods=["GET", "POST"])
def reset_password(token):
    if auth_service.get_valid_token(token, "reset_password") is None:
        flash("Ce lien est invalide, expiré ou a déjà été utilisé.", "danger")
        return redirect(url_for("auth.forgot_password"))
    form = NewPasswordForm()
    if form.validate_on_submit():
        try:
            auth_service.reset_password(token, form.password.data)
        except AuthError as e:
            flash(str(e), "danger")
            return render_template("auth/reset_password.html", form=form)
        auth_service.end_session()
        flash("Mot de passe modifié. Vous pouvez vous connecter.", "success")
        return redirect(url_for("auth.login"))
    return render_template("auth/reset_password.html", form=form)


@bp.route("/compte/mot-de-passe", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        try:
            auth_service.change_password(current_user, form.current.data, form.password.data)
        except AuthError as e:
            flash(str(e), "danger")
        else:
            flash("Mot de passe modifié. Vos autres sessions ont été déconnectées.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/change_password.html", form=form)


@bp.route("/invitation/<token>", methods=["GET", "POST"])
def accept_invitation(token):
    invitation = membership_service.find_invitation(token)
    if invitation is None:
        flash("Cette invitation est invalide, expirée ou a déjà été utilisée.", "danger")
        return redirect(url_for("auth.login"))
    existing = auth_service.find_user_by_email(invitation.email)
    lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == invitation.tenant_id))
    if existing is not None:
        # Compte existant : il faut être connecté avec ce compte pour accepter.
        if not current_user.is_authenticated or current_user.id != existing.id:
            flash("Un compte existe déjà pour cet email : connectez-vous puis rouvrez le lien d'invitation.", "info")
            return redirect(url_for("auth.login", next=url_for("auth.accept_invitation", token=token)))
        form = EmptyForm()
        if form.validate_on_submit():
            try:
                membership_service.accept_invitation(invitation, current_user)
            except MembershipError as e:
                flash(str(e), "danger")
                return redirect(url_for("auth.select_lab"))
            auth_service.activate_lab(current_user, invitation.tenant_id)
            db.session.commit()
            flash(f"Vous avez rejoint {lab.name}.", "success")
            return redirect(url_for("dashboard.index"))
        return render_template("auth/accept_invitation.html", form=form, invitation=invitation, lab=lab,
                               existing=True, role_labels=ROLE_LABELS)
    form = AcceptInvitationForm()
    if form.validate_on_submit():
        try:
            user = membership_service.accept_invitation(invitation, None, full_name=form.full_name.data,
                                                        password=form.password.data)
        except (MembershipError, AuthError) as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            auth_service.start_session(user)
            auth_service.activate_lab(user, invitation.tenant_id, audit=False)
            auth_service.login_audit(user, invitation.tenant_id)
            db.session.commit()
            flash(f"Bienvenue dans {lab.name}.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/accept_invitation.html", form=form, invitation=invitation, lab=lab,
                           existing=False, role_labels=ROLE_LABELS)


@bp.route("/inscription", methods=["GET", "POST"])
def signup():
    if not current_app.config["SELF_SIGNUP_ENABLED"]:
        abort(404)
    form = SignupForm()
    if form.validate_on_submit():
        if auth_service.find_user_by_email(form.email.data) is not None:
            flash("Un compte existe déjà pour cet email : connectez-vous d'abord.", "warning")
            return redirect(url_for("auth.login"))
        try:
            lab, user = auth_service.create_laboratory(form.lab_name.data, form.email.data, form.full_name.data,
                                                       form.password.data)
            db.session.commit()
        except AuthError as e:
            db.session.rollback()
            flash(str(e), "danger")
        else:
            auth_service.start_session(user)
            auth_service.activate_lab(user, lab.id, audit=False)
            db.session.commit()
            flash("Laboratoire créé. Votre essai gratuit commence aujourd'hui.", "success")
            return redirect(url_for("dashboard.index"))
    return render_template("auth/signup.html", form=form, trial_days=current_app.config["TRIAL_DAYS"])


@bp.app_context_processor
def inject_lab_switch():
    return {"lab_switch_form": EmptyForm() if getattr(g, "membership", None) else None}
