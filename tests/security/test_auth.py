"""Authentification : connexion, blocage, jetons, sessions, CSRF, invitations."""
from __future__ import annotations

import re
from datetime import timedelta

import sqlalchemy as sa

from app.extensions import db
from app.models.audit import AuditEvent
from app.models.base import utcnow
from app.models.tenant import Membership
from app.models.user import AuthToken, User
from app.repositories.base import repo
from app.security.tenancy import tenant_context
from app.security.tokens import new_token
from app.services import email_service
from tests import factories
from tests.conftest import extract_csrf, login


def test_valid_login_redirects_to_dashboard(client, world):
    response = client.post("/connexion", data={"email": world.emails["admin"], "password": factories.PASSWORD})
    assert response.status_code == 302
    assert response.headers["Location"].endswith("/tableau-de-bord")
    assert client.get("/tableau-de-bord").status_code == 200


def test_login_is_case_insensitive_on_email(client, world):
    response = client.post("/connexion", data={"email": "ADMIN@Labo-A.fr", "password": factories.PASSWORD})
    assert response.status_code == 302


def test_invalid_login_generic_message_and_audit(app, client, world):
    response = client.post("/connexion", data={"email": world.emails["admin"], "password": "mauvais"})
    assert response.status_code == 200
    assert "Email ou mot de passe incorrect" in response.data.decode()
    response = client.post("/connexion", data={"email": "inconnu@labo-a.fr", "password": "mauvais"})
    assert "Email ou mot de passe incorrect" in response.data.decode()  # pas d'énumération
    with app.app_context():
        from app.security.tenancy import system_context

        with system_context():
            assert db.session.scalar(sa.select(sa.func.count()).select_from(AuditEvent)
                                     .where(AuditEvent.action == "auth.login_failed")) == 2


def test_blocking_after_n_failures(app, client, world):
    for _ in range(app.config["LOGIN_MAX_FAILURES"]):
        client.post("/connexion", data={"email": world.emails["tech"], "password": "mauvais"})
    # Même le bon mot de passe est refusé pendant la fenêtre de blocage.
    response = client.post("/connexion", data={"email": world.emails["tech"], "password": factories.PASSWORD})
    assert response.status_code == 200
    assert "Trop de tentatives" in response.data.decode()
    # Un autre compte n'est pas affecté.
    assert client.post("/connexion", data={"email": world.emails["reader"],
                                           "password": factories.PASSWORD}).status_code == 302


def test_protected_page_requires_login(client):
    response = client.get("/ciq/")
    assert response.status_code == 302
    assert "/connexion" in response.headers["Location"]


def test_logout_requires_post(client, world):
    login(client, world.emails["admin"])
    assert client.get("/deconnexion").status_code == 405
    assert client.post("/deconnexion").status_code == 302
    assert client.get("/tableau-de-bord").status_code == 302


def _reset_token(app, email: str, *, expired=False, used=False) -> str:
    token, token_hash = new_token()
    with app.app_context():
        user = db.session.scalar(sa.select(User).where(User.email == email))
        db.session.add(AuthToken(user_id=user.id, purpose="reset_password", token_hash=token_hash,
                                 expires_at=utcnow() + (timedelta(minutes=-1) if expired else timedelta(hours=1)),
                                 used_at=utcnow() if used else None))
        db.session.commit()
    return token


def test_password_reset_flow(app, client, world):
    client.post("/mot-de-passe-oublie", data={"email": world.emails["tech"]})
    assert len(email_service.outbox) == 1
    link = re.search(r"http\S+/reinitialiser/(\S+)", email_service.outbox[0].body)
    token = link.group(1)
    new = "Nouveau-Mot-de-passe-2027"
    response = client.post(f"/reinitialiser/{token}", data={"password": new, "confirm": new})
    assert response.status_code == 302
    login(client, world.emails["tech"], password=new)
    # Jeton déjà utilisé : refusé.
    response = client.post(f"/reinitialiser/{token}", data={"password": new, "confirm": new})
    assert response.status_code == 302 and "/mot-de-passe-oublie" in response.headers["Location"]


def test_forgot_password_unknown_email_same_response(client):
    response = client.post("/mot-de-passe-oublie", data={"email": "personne@labo-a.fr"})
    assert response.status_code == 302
    assert email_service.outbox == []


def test_expired_token_is_refused(app, client, world):
    token = _reset_token(app, world.emails["tech"], expired=True)
    response = client.post(f"/reinitialiser/{token}", data={"password": "Xx-123456789aa", "confirm": "Xx-123456789aa"})
    assert response.status_code == 302 and "/mot-de-passe-oublie" in response.headers["Location"]


def test_used_token_is_refused(app, client, world):
    token = _reset_token(app, world.emails["tech"], used=True)
    assert client.get(f"/reinitialiser/{token}").status_code == 302


def test_token_hash_only_is_stored(app, world):
    token = _reset_token(app, world.emails["tech"])
    with app.app_context():
        stored = db.session.scalars(sa.select(AuthToken.token_hash)).all()
        assert token not in stored


def test_weak_password_is_refused(app, client, world):
    token = _reset_token(app, world.emails["tech"])
    response = client.post(f"/reinitialiser/{token}", data={"password": "court", "confirm": "court"})
    assert response.status_code == 200
    assert "au moins 12 caractères" in response.data.decode()


def test_password_change_invalidates_other_sessions(client, other_client, world):
    login(client, world.emails["quality"])
    login(other_client, world.emails["quality"])
    new = "Changement-Mdp-2026x"
    response = client.post("/compte/mot-de-passe", data={"current": factories.PASSWORD, "password": new,
                                                         "confirm": new})
    assert response.status_code == 302
    # La session courante reste valide, l'autre est déconnectée.
    assert client.get("/tableau-de-bord").status_code == 200
    response = other_client.get("/tableau-de-bord")
    assert response.status_code == 302 and "/connexion" in response.headers["Location"]


def test_csrf_is_enforced(app, client, world):
    app.config["WTF_CSRF_ENABLED"] = True
    try:
        response = client.post("/connexion", data={"email": world.emails["admin"], "password": factories.PASSWORD})
        assert response.status_code == 400
        page = client.get("/connexion")
        token = extract_csrf(page.data.decode())
        response = client.post("/connexion", data={"email": world.emails["admin"], "password": factories.PASSWORD,
                                                   "csrf_token": token})
        assert response.status_code == 302
        # Requête HTMX / AJAX : jeton accepté dans l'en-tête X-CSRFToken.
        token = extract_csrf(client.get("/notifications/").data.decode())
        assert client.post("/notifications/tout-lire").status_code == 400
        assert client.post("/notifications/tout-lire", headers={"X-CSRFToken": token}).status_code == 302
    finally:
        app.config["WTF_CSRF_ENABLED"] = False


def test_security_headers(client):
    response = client.get("/connexion")
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "script-src 'self'" in response.headers["Content-Security-Policy"]
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_session_cookie_flags_in_production():
    from app.config import ProductionConfig

    assert ProductionConfig.SESSION_COOKIE_SECURE is True
    assert ProductionConfig.SESSION_COOKIE_HTTPONLY is True
    assert ProductionConfig.SESSION_COOKIE_SAMESITE == "Lax"


def test_open_redirect_is_blocked(client, world):
    response = client.post("/connexion?next=https://evil.example/", data={"email": world.emails["admin"],
                                                                          "password": factories.PASSWORD})
    assert response.headers["Location"].endswith("/tableau-de-bord")


def test_invitation_flow_creates_account_and_membership(app, client, other_client, world):
    login(client, world.emails["admin"])
    client.post("/administration/membres/inviter", data={"email": "nouvelle@labo-a.fr", "role": "technician",
                                                          "team_id": ""})
    assert len(email_service.outbox) == 1
    token = re.search(r"/invitation/(\S+)", email_service.outbox[0].body).group(1)
    pwd = "Bienvenue-Au-Labo-2026"
    response = other_client.post(f"/invitation/{token}", data={"full_name": "Nouvelle Tech", "password": pwd,
                                                                "confirm": pwd})
    assert response.status_code == 302
    assert other_client.get("/ciq/saisie").status_code == 200
    with app.app_context(), tenant_context(world.lab_a):
        user = db.session.scalar(sa.select(User).where(User.email == "nouvelle@labo-a.fr"))
        assert repo(Membership).first(Membership.user_id == user.id).role == "technician"
    # Invitation à usage unique.
    other_client.post("/deconnexion")
    response = other_client.get(f"/invitation/{token}")
    assert response.status_code == 302 and "/connexion" in response.headers["Location"]


def test_existing_user_accepts_invitation_to_second_lab(app, client, other_client, world):
    login(client, world.emails["admin_b"])
    client.post("/administration/membres/inviter", data={"email": world.emails["tech"], "role": "reader",
                                                          "team_id": ""})
    token = re.search(r"/invitation/(\S+)", email_service.outbox[0].body).group(1)
    login(other_client, world.emails["tech"])
    assert other_client.post(f"/invitation/{token}").status_code == 302
    with app.app_context(), tenant_context(world.lab_b):
        assert repo(Membership).first(Membership.user_id == world.tech_id).role == "reader"


def test_no_self_signup_by_default(client):
    assert client.get("/inscription").status_code == 404


def test_self_signup_creates_lab_with_trial(app, client):
    app.config["SELF_SIGNUP_ENABLED"] = True
    try:
        pwd = "Mon-Nouveau-Labo-2026"
        response = client.post("/inscription", data={"lab_name": "Labo des Sources", "full_name": "Eve",
                                                      "email": "eve@sources.fr", "password": pwd, "confirm": pwd})
        assert response.status_code == 302
        page = client.get("/abonnement").data.decode()
        assert "Essai" in page
    finally:
        app.config["SELF_SIGNUP_ENABLED"] = False
