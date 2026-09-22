"""Autorisations : chaque rôle n'a que ses droits, vérifiés côté serveur."""
from __future__ import annotations

from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.base import utcnow
from app.models.billing import Subscription
from app.models.equipment import Equipment
from app.repositories.base import repo
from app.security.permissions import ALL_PERMISSIONS, READ_PERMISSIONS, ROLE_PERMISSIONS, P, role_has
from app.security.tenancy import tenant_context
from tests import factories
from tests.conftest import login

# Routes accessibles sans laboratoire actif ou publiques.
PUBLIC_ENDPOINTS = {
    "static", "health.health", "auth.login", "auth.logout", "auth.select_lab", "auth.activate_lab",
    "auth.forgot_password", "auth.reset_password", "auth.change_password", "auth.accept_invitation",
    "auth.signup", "billing.stripe_webhook", "dashboard.root",
}


def test_every_route_declares_its_permissions(app):
    """Toute route métier passe par @require / @require_lab (contrôle serveur systématique)."""
    missing = []
    for rule in app.url_map.iter_rules():
        if rule.endpoint in PUBLIC_ENDPOINTS:
            continue
        view = app.view_functions[rule.endpoint]
        if not hasattr(view, "required_permissions"):
            missing.append(rule.endpoint)
    assert missing == []


def test_role_matrix():
    assert ROLE_PERMISSIONS["admin"] == ALL_PERMISSIONS
    reader = ROLE_PERMISSIONS["reader"]
    assert reader <= READ_PERMISSIONS, "le lecteur ne doit avoir aucune permission d'écriture"
    for perm in (P.USERS_MANAGE, P.BILLING_MANAGE, P.LAB_SETTINGS):
        assert [r for r in ROLE_PERMISSIONS if role_has(r, perm)] == ["admin"]
    assert {r for r in ROLE_PERMISSIONS if role_has(r, P.CA_VALIDATE)} == {"admin", "quality"}
    assert {r for r in ROLE_PERMISSIONS if role_has(r, P.CIQ_RESULT_CREATE)} == {"admin", "quality", "technician"}
    assert {r for r in ROLE_PERMISSIONS if role_has(r, P.AUDIT_VIEW)} == {"admin", "quality"}


@pytest.fixture
def equipment_id(app, world):
    with app.app_context(), tenant_context(world.lab_a):
        eq = factories.make_equipment("Balance", "BAL-1")
        db.session.commit()
        return eq.id


# (méthode, url, données, rôles autorisés)
CASES = [
    ("GET", "/administration/membres", None, {"admin"}),
    ("POST", "/administration/membres/inviter", {"email": "nouveau@labo-a.fr", "role": "reader", "team_id": ""}, {"admin"}),
    ("GET", "/administration/", None, {"admin"}),
    ("GET", "/abonnement", None, {"admin"}),
    ("GET", "/audit/", None, {"admin", "quality"}),
    ("GET", "/ciq/saisie", None, {"admin", "quality", "technician"}),
    ("GET", "/ciq/parametres/nouveau", None, {"admin", "quality"}),
    ("GET", "/metrologie/equipements/nouveau", None, {"admin", "quality"}),
    ("POST", "/metrologie/equipements/{eq}/statut", {"status": "maintenance", "reason": "test"}, {"admin", "quality"}),
    ("POST", "/metrologie/equipements/{eq}/realisations",
     {"event_type": "verification", "performed_on": "2026-05-01", "outcome": "conform", "plan_id": ""},
     {"admin", "quality", "technician"}),
    ("GET", "/actions-correctives/nouvelle", None, {"admin", "quality"}),
    ("GET", "/transmissions/nouvelle", None, {"admin", "quality", "technician"}),
    ("GET", "/non-conformites/nouvelle", None, {"admin", "quality", "technician"}),
    ("GET", "/ciq/historique/export.csv", None, {"admin", "quality", "technician", "reader"}),
]

ROLE_USERS = {"admin": "admin", "quality": "quality", "technician": "tech", "reader": "reader"}


@pytest.mark.parametrize("role", list(ROLE_USERS))
@pytest.mark.parametrize("method,url,data,allowed", CASES, ids=[f"{c[0]} {c[1]}" for c in CASES])
def test_route_access_by_role(client, world, equipment_id, role, method, url, data, allowed):
    login(client, world.emails[ROLE_USERS[role]])
    url = url.format(eq=equipment_id)
    response = client.get(url) if method == "GET" else client.post(url, data=data)
    if role in allowed:
        assert response.status_code in (200, 302), (role, url, response.status_code)
    else:
        assert response.status_code == 403, (role, url, response.status_code)


def test_reader_cannot_write_anything(app, client, world, equipment_id):
    login(client, world.emails["reader"])
    posts = [
        (f"/metrologie/equipements/{equipment_id}/statut", {"status": "retired", "reason": "x"}),
        (f"/metrologie/equipements/{equipment_id}/pieces-jointes", {}),
        ("/metrologie/equipements/nouveau", {"name": "x", "internal_id": "x", "status": "in_service",
                                              "criticality": "low"}),
        ("/transmissions/nouvelle", {"title": "x", "body": "x"}),
        ("/non-conformites/nouvelle", {"title": "x"}),
        ("/actions-correctives/nouvelle", {"description": "x"}),
        ("/ciq/saisie", {}),
        ("/administration/equipes", {"name": "x"}),
    ]
    for url, data in posts:
        assert client.post(url, data=data).status_code == 403, url
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(Equipment).get(equipment_id).status == "in_service"


def test_only_authorised_role_validates_corrective_action(app, client, world):
    with app.app_context(), tenant_context(world.lab_a):
        action = CorrectiveAction(source_type="manual", description="Nettoyer", status="done",
                                  responsible_id=world.tech_id, done_comment="fait")
        db.session.add(action)
        db.session.commit()
        action_id = action.id
    login(client, world.emails["tech"])
    assert client.post(f"/actions-correctives/{action_id}/valider", data={}).status_code == 403
    login(client, world.emails["quality"])
    assert client.post(f"/actions-correctives/{action_id}/valider", data={}).status_code == 302
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(CorrectiveAction).get(action_id).status == "validated"


def test_technician_completes_only_assigned_actions(app, client, world):
    with app.app_context(), tenant_context(world.lab_a):
        mine = CorrectiveAction(source_type="manual", description="à moi", status="open", responsible_id=world.tech_id)
        other = CorrectiveAction(source_type="manual", description="autre", status="open",
                                 responsible_id=world.tech2_id)
        db.session.add_all([mine, other])
        db.session.commit()
        ids = mine.id, other.id
    login(client, world.emails["tech"])
    data = {"done_on": "2026-05-01", "comment": "réalisé"}
    client.post(f"/actions-correctives/{ids[0]}/realisee", data=data)
    client.post(f"/actions-correctives/{ids[1]}/realisee", data=data)
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(CorrectiveAction).get(ids[0]).status == "done"
        assert repo(CorrectiveAction).get(ids[1]).status == "open"


def test_last_admin_cannot_remove_own_role(app, client, world):
    login(client, world.emails["admin"])
    with app.app_context(), tenant_context(world.lab_a):
        from app.models.tenant import Membership

        mid = db.session.scalar(sa.select(Membership.id).where(Membership.user_id == world.admin_id))
    client.post(f"/administration/membres/{mid}", data={"role": "reader", "team_id": ""})
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(Membership).get(mid).role == "admin"


def test_role_change_is_audited(app, client, world):
    from app.models.audit import AuditEvent
    from app.models.tenant import Membership

    login(client, world.emails["admin"])
    with app.app_context(), tenant_context(world.lab_a):
        mid = db.session.scalar(sa.select(Membership.id).where(Membership.user_id == world.tech_id))
    client.post(f"/administration/membres/{mid}", data={"role": "quality", "team_id": ""})
    with app.app_context(), tenant_context(world.lab_a):
        event = repo(AuditEvent).first(AuditEvent.action == "membership.updated")
        assert event.before == {"role": "technician"} and event.after == {"role": "quality"}


# ---------------------------------------------------------------------------
# Laboratoire en lecture seule (abonnement expiré)
# ---------------------------------------------------------------------------

def _expire_trial(app, lab_id):
    with app.app_context(), tenant_context(lab_id):
        sub = db.session.scalar(sa.select(Subscription))
        sub.status = "trialing"
        sub.trial_ends_at = utcnow() - timedelta(days=1)
        db.session.commit()


def test_expired_lab_is_read_only_but_data_is_kept(app, client, world, equipment_id):
    _expire_trial(app, world.lab_a)
    login(client, world.emails["admin"])
    page = client.get("/metrologie/")
    assert page.status_code == 200 and "Balance" in page.data.decode()
    assert "Lecture seule" in page.data.decode()
    response = client.post(f"/metrologie/equipements/{equipment_id}/statut", data={"status": "retired", "reason": "x"})
    assert response.status_code == 403
    assert "lecture seule" in response.data.decode()
    # La gestion de l'abonnement reste accessible pour régulariser.
    assert client.get("/abonnement").status_code == 200
    # Les exports restent possibles.
    assert client.get("/metrologie/equipements/export.csv").status_code == 200


def test_user_cap_is_enforced(app, client, world):
    with app.app_context():
        from app.models.tenant import Laboratory

        lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == world.lab_a))
        lab.max_active_users = 6  # déjà 6 membres actifs dans A
        db.session.commit()
    login(client, world.emails["admin"])
    client.post("/administration/membres/inviter", data={"email": "septieme@labo-a.fr", "role": "reader",
                                                          "team_id": ""})
    from app.models.tenant import Invitation

    with app.app_context(), tenant_context(world.lab_a):
        assert repo(Invitation).count() == 0
