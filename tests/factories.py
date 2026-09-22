"""Fabriques de données de test (via les modèles et services réels)."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import sqlalchemy as sa
from flask import g
from flask_login import login_user

from app.extensions import db
from app.models.base import utcnow
from app.models.billing import Subscription
from app.models.tenant import Laboratory, Membership
from app.models.user import User
from app.security.passwords import hash_password
from app.security.tenancy import tenant_context

PASSWORD = "Mot-de-passe-Test-2026"
_HASH = None


def password_hash() -> str:
    global _HASH
    if _HASH is None:
        _HASH = hash_password(PASSWORD)
    return _HASH


def make_user(email: str, name: str | None = None) -> User:
    user = User(email=email, full_name=name or email.split("@")[0], password_hash=password_hash(),
                password_changed_at=utcnow())
    db.session.add(user)
    db.session.flush()
    return user


def make_lab(name: str, slug: str) -> Laboratory:
    lab = Laboratory(name=name, slug=slug, timezone="Europe/Paris", max_active_users=15, status="active")
    db.session.add(lab)
    db.session.flush()
    with tenant_context(lab.id):
        db.session.add(Subscription(status="trialing", trial_ends_at=utcnow() + timedelta(days=30)))
        db.session.flush()
    return lab


def add_member(lab: Laboratory, user: User, role: str, team_id=None) -> Membership:
    with tenant_context(lab.id):
        m = Membership(user_id=user.id, role=role, is_active=True, team_id=team_id)
        db.session.add(m)
        db.session.flush()
        return m


def build_world() -> SimpleNamespace:
    lab_a = make_lab("Laboratoire A", "labo-a")
    lab_b = make_lab("Laboratoire B", "labo-b")
    users = {}
    for key, role in (("admin", "admin"), ("quality", "quality"), ("tech", "technician"), ("reader", "reader")):
        users[key] = make_user(f"{key}@labo-a.fr", f"{key.capitalize()} A")
        add_member(lab_a, users[key], role)
    users["tech2"] = make_user("tech2@labo-a.fr", "Tech2 A")
    add_member(lab_a, users["tech2"], "technician")
    users["admin_b"] = make_user("admin@labo-b.fr", "Admin B")
    add_member(lab_b, users["admin_b"], "admin")
    users["consultant"] = make_user("consultant@conseil.fr", "Consultant")
    add_member(lab_a, users["consultant"], "quality")
    add_member(lab_b, users["consultant"], "reader")
    db.session.commit()
    return SimpleNamespace(
        lab_a=lab_a.id, lab_b=lab_b.id,
        **{f"{k}_id": u.id for k, u in users.items()},
        emails={k: u.email for k, u in users.items()},
    )


@contextmanager
def acting_as(app, email: str, lab_id):
    """Contexte de requête simulé : utilisateur connecté et laboratoire actif, pour appeler les services."""
    with app.test_request_context("/"):
        with tenant_context(lab_id):
            user = db.session.scalar(sa.select(User).where(User.email == email))
            login_user(user)
            g.membership = db.session.scalar(sa.select(Membership).where(Membership.user_id == user.id))
            g.lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == lab_id))
            g.write_allowed = True
            yield user


def make_equipment(name: str = "Analyseur", internal_id: str = "EQ-1"):
    from app.models.equipment import Equipment

    eq = Equipment(name=name, internal_id=internal_id, status="in_service", criticality="medium")
    db.session.add(eq)
    db.session.flush()
    return eq


def make_ciq_setup(mode: str = "westgard", levels: int = 2, name: str = "Glucose", equipment=None):
    """Paramètre + niveaux + lots + limites (cible 100, s 2 pour N1 ; 200, s 4 pour N2)."""
    from app.services import ciq_service

    equipment = equipment or make_equipment(f"Équipement {name}", f"EQ-{name}")
    param = ciq_service.save_parameter(None, equipment_id=equipment.id, name=name, unit="u", decimals=2,
                                       is_active=True, mode=mode)
    setup = SimpleNamespace(parameter=param, equipment=equipment, levels=[], lots=[], limits=[])
    for i in range(levels):
        level = ciq_service.save_level(param, None, label=f"N{i + 1}", sort_order=i + 1, mode=None)
        lot = ciq_service.save_lot(level, None, lot_number=f"LOT-{name}-{i + 1}", manufacturer=None,
                                   expires_on=None, in_use_from=None, in_use_to=None)
        limits = ciq_service.set_limits(lot, mode=mode, mean=Decimal(100 * (i + 1)), sd=Decimal(2 * (i + 1)),
                                        source="supplier", reason=None)
        setup.levels.append(level)
        setup.lots.append(lot)
        setup.limits.append(limits)
    return setup


def value_for(limits, z) -> Decimal:
    return limits.mean + Decimal(str(z)) * limits.sd


def run_at(days_ago: float) -> datetime:
    return datetime(2026, 6, 1, 8, 0, tzinfo=timezone.utc) - timedelta(days=days_ago)
