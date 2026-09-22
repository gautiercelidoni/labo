"""Isolation stricte entre laboratoires : ORM, contraintes PostgreSQL, routes, exports, sessions."""
from __future__ import annotations

import io
import pathlib
import re
import uuid
from datetime import date

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.audit import AuditEvent
from app.models.ciq import CIQResult, CIQRun
from app.models.equipment import Equipment, MaintenancePlan
from app.models.files import Attachment
from app.models.tenant import Laboratory, Membership
from app.models.user import User
from app.repositories.base import paginate, repo
from app.security.tenancy import NoTenantContext, TenantError, system_context, tenant_context
from app.services import ciq_service, membership_service
from app.services.ciq_service import EntryInput
from tests import factories
from tests.conftest import login

ROOT = pathlib.Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# Filtre ORM automatique
# ---------------------------------------------------------------------------

def test_select_is_filtered_by_active_lab(app, world, in_lab):
    with in_lab(world.lab_a):
        factories.make_equipment("Appareil A", "A-1")
        db.session.commit()
    with in_lab(world.lab_b):
        factories.make_equipment("Appareil B", "B-1")
        db.session.commit()
        names = db.session.scalars(sa.select(Equipment.name)).all()
        assert names == ["Appareil B"]
    with in_lab(world.lab_a):
        assert [e.name for e in repo(Equipment).all()] == ["Appareil A"]


def test_query_without_lab_context_is_refused(app, world):
    with app.app_context():
        with pytest.raises(NoTenantContext):
            db.session.scalars(sa.select(Equipment)).all()
        # Les tables globales restent accessibles.
        assert db.session.scalar(sa.select(sa.func.count()).select_from(User)) >= 1


def test_counts_joins_and_subqueries_are_filtered(app, world, in_lab):
    with in_lab(world.lab_a):
        for i in range(3):
            factories.make_equipment(f"A{i}", f"A-{i}")
        db.session.commit()
    with in_lab(world.lab_b):
        factories.make_equipment("B0", "B-0")
        db.session.commit()
    with in_lab(world.lab_a):
        assert repo(Equipment).count() == 3
        page = paginate(sa.select(Equipment).order_by(Equipment.name), 1, 2)
        assert page.total == 3 and len(page.items) == 2
        # Jointure : les membres listés sont ceux du laboratoire actif uniquement.
        emails = {u.email for u in membership_service.member_users()}
        assert world.emails["admin_b"] not in emails
        assert world.emails["admin"] in emails


def test_bulk_update_and_delete_are_filtered(app, world, in_lab):
    with in_lab(world.lab_b):
        factories.make_equipment("B", "B-1")
        db.session.commit()
    with in_lab(world.lab_a):
        factories.make_equipment("A", "A-1")
        db.session.commit()
        db.session.execute(sa.update(Equipment).values(notes="modifié"))
        db.session.commit()
    with in_lab(world.lab_b):
        assert repo(Equipment).first().notes is None


def test_get_by_id_of_other_lab_returns_none(app, world, in_lab):
    with in_lab(world.lab_b):
        eq_b = factories.make_equipment("B", "B-1")
        db.session.commit()
        eq_b_id = eq_b.id
    with in_lab(world.lab_a):
        assert repo(Equipment).get(eq_b_id) is None
        assert repo(Equipment).get("pas-un-uuid") is None


# ---------------------------------------------------------------------------
# Contrôle à l'écriture (before_flush)
# ---------------------------------------------------------------------------

def test_creating_object_for_other_lab_is_refused(app, world, in_lab):
    with in_lab(world.lab_a):
        db.session.add(Equipment(tenant_id=world.lab_b, name="Intrus", internal_id="X", status="in_service",
                                 criticality="low"))
        with pytest.raises(TenantError):
            db.session.flush()
        db.session.rollback()


def test_tenant_id_is_filled_automatically(app, world, in_lab):
    with in_lab(world.lab_a):
        eq = factories.make_equipment("Auto", "AUTO-1")
        assert eq.tenant_id == world.lab_a


def test_modifying_object_of_other_lab_is_refused(app, world, in_lab):
    with in_lab(world.lab_b):
        factories.make_equipment("B", "B-1")
        db.session.commit()
    with app.app_context():
        with system_context():
            eq_b = db.session.scalars(sa.select(Equipment)).first()
        with tenant_context(world.lab_a):
            eq_b.name = "piraté"
            with pytest.raises(TenantError):
                db.session.flush()
            db.session.rollback()


def test_changing_tenant_id_is_refused(app, world, in_lab):
    with in_lab(world.lab_a):
        eq = factories.make_equipment("A", "A-1")
        db.session.commit()
        eq.tenant_id = world.lab_b
        with pytest.raises(TenantError):
            db.session.flush()
        db.session.rollback()


def test_system_context_is_forbidden_in_routes(app):
    with app.test_request_context("/tableau-de-bord"):
        from flask import request

        request.url_rule = app.url_map._rules_by_endpoint["dashboard.index"][0]
        with pytest.raises(TenantError):
            with system_context():
                pass


# ---------------------------------------------------------------------------
# Contraintes PostgreSQL
# ---------------------------------------------------------------------------

def test_composite_foreign_key_blocks_cross_lab_reference(app, world, in_lab):
    with in_lab(world.lab_b):
        eq_b = factories.make_equipment("B", "B-1")
        db.session.commit()
        eq_b_id = eq_b.id
    with app.app_context():
        # Même en contournant l'application (contexte système), PostgreSQL refuse la référence.
        with system_context():
            db.session.add(MaintenancePlan(tenant_id=world.lab_a, equipment_id=eq_b_id, event_type="calibration",
                                           period_value=1, period_unit="year"))
            with pytest.raises(IntegrityError):
                db.session.flush()
            db.session.rollback()


def test_unique_constraints_are_per_lab(app, world, in_lab):
    with in_lab(world.lab_a):
        factories.make_equipment("A", "MEME-ID")
        db.session.commit()
    with in_lab(world.lab_b):
        factories.make_equipment("B", "MEME-ID")  # autorisé : autre laboratoire
        db.session.commit()
    with in_lab(world.lab_a):
        db.session.add(Equipment(name="Doublon", internal_id="MEME-ID", status="in_service", criticality="low"))
        with pytest.raises(IntegrityError):
            db.session.flush()
        db.session.rollback()


# ---------------------------------------------------------------------------
# Accès HTTP par identifiant (IDOR)
# ---------------------------------------------------------------------------

@pytest.fixture
def lab_b_data(app, world):
    """Un jeu complet de ressources dans le laboratoire B."""
    with factories.acting_as(app, world.emails["admin_b"], world.lab_b):
        setup = factories.make_ciq_setup("westgard", levels=1, name="Secret B")
        run, _ = ciq_service.create_run(setup.parameter, factories.run_at(1),
                                        [EntryInput(setup.levels[0], setup.lots[0],
                                                    factories.value_for(setup.limits[0], 3.5), "rejet B")])
        result = db.session.scalar(sa.select(CIQResult).where(CIQResult.run_id == run.id))
        action = CorrectiveAction(source_type="manual", description="Action B", status="open")
        plan = MaintenancePlan(equipment_id=setup.equipment.id, event_type="calibration", period_value=1,
                               period_unit="year", next_due_on=date(2020, 1, 1))
        db.session.add_all([action, plan])
        from werkzeug.datastructures import FileStorage

        from app.services.file_storage import store_upload

        attachment = store_upload(FileStorage(io.BytesIO(b"%PDF-1.4\nsecret B\n%%EOF"), "certificat-b.pdf"),
                                  "equipment", setup.equipment.id)
        from app.services import nc_service, transmission_service

        nc = nc_service.create({"title": "NC B", "description": "confidentiel", "origin": "other",
                                "severity": "minor", "detected_on": date(2026, 1, 5)})
        tr = transmission_service.create(title="Transmission B", body="confidentiel", category="general",
                                         priority="normal", due_on=None, user_ids=[world.admin_b_id], team_ids=[],
                                         attachments=[])
        membership = db.session.scalar(sa.select(Membership).where(Membership.user_id == world.admin_b_id))
        db.session.commit()
        return {
            "equipment": setup.equipment.id, "parameter": setup.parameter.id, "level": setup.levels[0].id,
            "lot": setup.lots[0].id, "run": run.id, "result": result.id, "action": action.id, "plan": plan.id,
            "attachment": attachment.id, "nc": nc.id, "transmission": tr.id, "membership": membership.id,
        }


READ_URLS = [
    "/metrologie/equipements/{equipment}",
    "/metrologie/equipements/{equipment}/historique.pdf",
    "/metrologie/plans/{plan}/modifier",
    "/ciq/parametres/{parameter}",
    "/ciq/parametres/{parameter}/graphique",
    "/ciq/parametres/{parameter}/niveaux/{level}/graphique.json",
    "/ciq/parametres/{parameter}/rapport.pdf",
    "/ciq/niveaux/{level}",
    "/ciq/lots/{lot}",
    "/ciq/series/{run}",
    "/actions-correctives/{action}",
    "/fichiers/{attachment}",
    "/non-conformites/{nc}",
    "/non-conformites/{nc}/fiche.pdf",
    "/transmissions/{transmission}",
    "/administration/membres/{membership}",
]

WRITE_URLS = [
    ("/metrologie/equipements/{equipment}/modifier", {"name": "x", "internal_id": "x", "status": "in_service",
                                                      "criticality": "low"}),
    ("/metrologie/equipements/{equipment}/statut", {"status": "retired", "reason": "x"}),
    ("/metrologie/equipements/{equipment}/archiver", {"reason": "x"}),
    ("/metrologie/equipements/{equipment}/realisations", {"event_type": "calibration", "performed_on": "2026-01-01"}),
    ("/ciq/resultats/{result}/annuler", {"v-reason": "x"}),
    ("/ciq/series/{run}/justifier", {"j-justification": "x", "j-action_description": "x"}),
    ("/ciq/lots/{lot}/limites", {"limits-mode": "westgard", "limits-mean": "1", "limits-sd": "1",
                                 "limits-source": "lab", "limits-reason": "x"}),
    ("/ciq/parametres/{parameter}/regles", {"rules-mode": "westgard"}),
    ("/actions-correctives/{action}/realisee", {"done_on": "2026-01-01", "comment": "x"}),
    ("/non-conformites/{nc}/statut", {"status": "open", "comment": "x"}),
    ("/transmissions/{transmission}/commentaires", {"body": "x"}),
    ("/administration/membres/{membership}/activation", {}),
    ("/non-conformites/depuis-serie/{run}", {}),
]


def test_user_of_lab_a_cannot_read_resources_of_lab_b(client, world, lab_b_data):
    login(client, world.emails["admin"])
    for template in READ_URLS:
        url = template.format(**lab_b_data)
        response = client.get(url)
        assert response.status_code == 404, (url, response.status_code)


def test_user_of_lab_a_cannot_modify_resources_of_lab_b(app, client, world, lab_b_data):
    login(client, world.emails["admin"])
    for template, data in WRITE_URLS:
        url = template.format(**lab_b_data)
        response = client.post(url, data=data)
        assert response.status_code == 404, (url, response.status_code)
    with app.app_context(), tenant_context(world.lab_b):
        eq = repo(Equipment).get(lab_b_data["equipment"])
        assert eq.name == "Équipement Secret B" and eq.status == "in_service" and eq.archived_at is None
        assert repo(CIQResult).get(lab_b_data["result"]).voided_at is None
        assert repo(CIQRun).get(lab_b_data["run"]).status == "rejected"
        assert repo(Membership).get(lab_b_data["membership"]).is_active is True


def test_random_uuid_returns_404(client, world):
    login(client, world.emails["admin"])
    assert client.get(f"/metrologie/equipements/{uuid.uuid4()}").status_code == 404
    assert client.get("/metrologie/equipements/pas-un-uuid").status_code == 404


def test_lists_exports_and_dashboard_never_mix_labs(client, world, lab_b_data):
    login(client, world.emails["admin"])
    for url in ["/metrologie/", "/metrologie/echeances?filtre=tous", "/ciq/", "/ciq/historique",
                "/actions-correctives/", "/non-conformites/", "/transmissions/?boite=toutes", "/audit/",
                "/metrologie/equipements/export.csv", "/ciq/historique/export.csv", "/actions-correctives/export.csv",
                "/non-conformites/export.csv", "/audit/export.csv", "/tableau-de-bord"]:
        body = client.get(url).data.decode("utf-8", "replace")
        for secret in ("Secret B", "Action B", "NC B", "Transmission B", "certificat-b"):
            assert secret not in body, (url, secret)
    dashboard = client.get("/tableau-de-bord").data.decode()
    # Le labo B a une série rejetée et une échéance dépassée ; le labo A n'en a pas.
    assert re.search(r'<span class="value">0</span><br>\s*<span>Séries CIQ rejetées', dashboard)
    assert re.search(r'<span class="value">0</span><br>\s*<span>Échéances métrologiques dépassées', dashboard)


def test_lab_data_export_contains_only_active_lab(client, world, lab_b_data):
    import zipfile

    login(client, world.emails["admin"])
    response = client.post("/administration/export-donnees")
    assert response.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    content = "".join(archive.read(name).decode("utf-8-sig") for name in archive.namelist())
    assert "Secret B" not in content and "confidentiel" not in content
    assert world.emails["admin"] in content


# ---------------------------------------------------------------------------
# Utilisateur membre de deux laboratoires, retrait d'accès
# ---------------------------------------------------------------------------

def test_multi_lab_member_sees_only_active_lab(client, world, lab_b_data, in_lab):
    with in_lab(world.lab_a):
        factories.make_equipment("Appareil du labo A", "A-9")
        db.session.commit()
    login(client, world.emails["consultant"])
    # Plusieurs appartenances : choix explicite du laboratoire.
    assert client.get("/tableau-de-bord").status_code == 302
    client.post(f"/laboratoires/{world.lab_a}/activer")
    body = client.get("/metrologie/").data.decode()
    assert "Appareil du labo A" in body and "Secret B" not in body
    assert client.get(f"/metrologie/equipements/{lab_b_data['equipment']}").status_code == 404
    client.post(f"/laboratoires/{world.lab_b}/activer")
    body = client.get("/metrologie/").data.decode()
    assert "Secret B" in body and "Appareil du labo A" not in body
    # Rôle distinct par laboratoire : lecteur dans B.
    assert client.get("/ciq/saisie").status_code == 403


def test_switching_to_foreign_lab_is_refused(client, world):
    login(client, world.emails["admin"])
    assert client.post(f"/laboratoires/{world.lab_b}/activer").status_code == 404


def test_lab_switch_is_audited(app, client, world):
    login(client, world.emails["consultant"])
    client.post(f"/laboratoires/{world.lab_a}/activer")
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(AuditEvent).count(AuditEvent.action == "lab.switch", AuditEvent.user_id == world.consultant_id) == 1


def test_membership_removal_is_effective_immediately(app, client, other_client, world):
    login(client, world.emails["tech"])
    assert client.get("/ciq/").status_code == 200
    login(other_client, world.emails["admin"])
    with app.app_context(), tenant_context(world.lab_a):
        membership_id = db.session.scalar(sa.select(Membership.id).where(Membership.user_id == world.tech_id))
    assert other_client.post(f"/administration/membres/{membership_id}/activation").status_code == 302
    response = client.get("/ciq/")
    assert response.status_code == 302
    assert "/laboratoires" in response.headers["Location"]


def test_role_change_is_effective_immediately(app, client, other_client, world):
    login(client, world.emails["quality"])
    assert client.get("/audit/").status_code == 200
    login(other_client, world.emails["admin"])
    with app.app_context(), tenant_context(world.lab_a):
        membership_id = db.session.scalar(sa.select(Membership.id).where(Membership.user_id == world.quality_id))
    other_client.post(f"/administration/membres/{membership_id}", data={"role": "reader", "team_id": ""})
    assert client.get("/audit/").status_code == 403


# ---------------------------------------------------------------------------
# Garde-fous statiques (exécutés comme dans la CI)
# ---------------------------------------------------------------------------

def _python_sources():
    return [p for p in (ROOT / "app").rglob("*.py")]


def test_no_session_get_on_models():
    offenders = []
    for path in _python_sources():
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if re.search(r"db\.session\.get\(|\.session\.get\((?!\")", line) and "flask" not in line:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno}")
    assert offenders == [], "session.get() interdit (utiliser TenantRepository) : " + ", ".join(offenders)


def test_cross_tenant_read_is_limited_to_audited_places():
    allowed = {"app/security/tenancy.py", "app/services/auth_service.py", "app/services/membership_service.py"}
    users = {str(p.relative_to(ROOT)) for p in _python_sources() if "cross_tenant_read" in p.read_text()}
    assert users <= allowed, users - allowed


def test_system_context_is_limited_to_global_tasks():
    allowed = {"app/security/tenancy.py", "app/cli.py", "app/services/notification_service.py",
               "app/services/stripe_service.py"}
    users = {str(p.relative_to(ROOT)) for p in _python_sources() if "system_context" in p.read_text()}
    assert users <= allowed, users - allowed


def test_every_tenant_table_has_tenant_unique_key():
    from app.models.base import TenantScoped

    for mapper in db.Model.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, TenantScoped):
            table = cls.__table__
            uniques = [tuple(c.name for c in cons.columns) for cons in table.constraints
                       if isinstance(cons, sa.UniqueConstraint)]
            assert ("tenant_id", "id") in uniques, table.name
            assert table.c.tenant_id.nullable is False, table.name


def test_attachment_model_is_tenant_scoped():
    from app.models.base import TenantScoped

    assert issubclass(Attachment, TenantScoped)
    assert issubclass(Laboratory, object) and not issubclass(Laboratory, TenantScoped)
