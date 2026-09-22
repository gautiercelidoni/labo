"""Configuration des tests : base PostgreSQL réelle, migrations Alembic, une transaction par test.

- Le schéma est recréé une fois par session avec le rôle propriétaire puis migré (flask db upgrade),
  ce qui teste aussi les migrations sur base vide.
- L'application se connecte avec le rôle applicatif (droits restreints sur l'audit).
- Chaque test s'exécute dans une transaction annulée à la fin (les commit deviennent des savepoints).
"""
from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
from flask_migrate import upgrade

from app import create_app
from app.extensions import db
from app.security.tenancy import TenantSession, tenant_context
from app.services import email_service

from tests import factories


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    app = create_app("test")
    app.config["UPLOAD_DIR"] = str(tmp_path_factory.mktemp("uploads"))
    owner_engine = sa.create_engine(app.config["DATABASE_OWNER_URL"])
    with owner_engine.begin() as conn:
        conn.execute(sa.text("DROP SCHEMA IF EXISTS public CASCADE"))
        conn.execute(sa.text("CREATE SCHEMA public"))
    owner_engine.dispose()
    with app.app_context():
        upgrade()
        db.engine.dispose()
    return app


@pytest.fixture(autouse=True)
def _transaction(app):
    with app.app_context():
        engine = db.engine
    connection = engine.connect()
    outer = connection.begin()
    original = db.session
    db.session = db._make_scoped_session(
        {"bind": connection, "join_transaction_mode": "create_savepoint", "class_": TenantSession}
    )
    email_service.outbox.clear()
    try:
        yield connection
    finally:
        with app.app_context():
            db.session.remove()
        outer.rollback()
        connection.close()
        db.session = original


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def other_client(app):
    return app.test_client()


@pytest.fixture
def world(app):
    """Deux laboratoires A et B ; un utilisateur par rôle dans A, un admin dans B, un consultant dans les deux."""
    with app.app_context():
        return factories.build_world()


@pytest.fixture
def in_lab(app):
    """Contexte applicatif + laboratoire pour manipuler directement les services et modèles."""
    from contextlib import contextmanager

    @contextmanager
    def _ctx(lab_id, user_email: str | None = None):
        if user_email:
            with factories.acting_as(app, user_email, lab_id):
                yield
        else:
            with app.app_context(), tenant_context(lab_id):
                yield

    return _ctx


def extract_csrf(html: str) -> str:
    match = re.search(r'name="csrf_token" (?:type="hidden" )?value="([^"]+)"', html) or re.search(
        r'id="csrf_token" name="csrf_token" type="hidden" value="([^"]+)"', html)
    assert match, "jeton CSRF introuvable"
    return match.group(1)


def login(client, email: str, lab_id=None, password: str = factories.PASSWORD):
    client.post("/deconnexion")  # repart d'une session vierge
    response = client.post("/connexion", data={"email": email, "password": password})
    assert response.status_code == 302, response.data[:500]
    if lab_id is not None:
        response = client.post(f"/laboratoires/{lab_id}/activer")
        assert response.status_code == 302
    return response


@pytest.fixture
def as_user(client):
    def _as(email: str, lab_id=None):
        login(client, email, lab_id)
        return client

    return _as


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests lents (données de démonstration complètes)")


__all__ = ["login", "extract_csrf", "SimpleNamespace"]
