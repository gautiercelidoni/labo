"""Chargement, à chaque requête, du laboratoire actif et de l'appartenance de l'utilisateur.

Le rôle est relu en base à chaque requête : un retrait de droits ou une désactivation est
effectif immédiatement.
"""
from __future__ import annotations

import uuid

import sqlalchemy as sa
from flask import Flask, flash, g, redirect, request, session, url_for
from flask_login import current_user

from app.extensions import db
from app.models.billing import Subscription
from app.models.tenant import Laboratory, Membership
from app.repositories.base import parse_uuid
from app.security.tenancy import reset_tenant, set_tenant
from app.services.access_service import compute_access

# Routes accessibles sans laboratoire actif.
LAB_FREE_ENDPOINTS = frozenset({"static", "health.health"})


def _clear_active_lab() -> None:
    session.pop("active_lab_id", None)


def load_request_context():
    g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    g.membership = None
    g.lab = None
    g.write_allowed = False
    g.access_reason = None
    if request.endpoint in LAB_FREE_ENDPOINTS or not current_user.is_authenticated:
        return None
    lab_id = parse_uuid(session.get("active_lab_id"))
    if lab_id is None:
        return None
    g._tenant_token = set_tenant(lab_id)
    membership = db.session.scalars(
        sa.select(Membership).where(Membership.user_id == current_user.id, Membership.is_active.is_(True))
    ).first()
    lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == lab_id))
    if membership is None or lab is None:
        _clear_active_lab()
        reset_tenant(g.pop("_tenant_token"))
        flash("Votre accès à ce laboratoire a été retiré ou n'existe plus.", "warning")
        if request.blueprint != "auth":
            return redirect(url_for("auth.select_lab"))
        return None
    subscription = db.session.scalars(sa.select(Subscription)).first()
    access = compute_access(lab, subscription)
    g.membership = membership
    g.lab = lab
    g.subscription = subscription
    g.write_allowed = access.write_allowed
    g.access_reason = access.reason
    return None


def teardown_request_context(exc=None):
    token = g.pop("_tenant_token", None)
    if token is not None:
        reset_tenant(token)


def init_request_context(app: Flask) -> None:
    app.before_request(load_request_context)
    app.teardown_request(teardown_request_context)
