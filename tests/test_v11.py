"""V1.1 : cahier de transmission et non-conformités."""
from __future__ import annotations

from datetime import date

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.non_conformities import NonConformity, NonConformityStatusChange
from app.models.tenant import Membership, Team
from app.models.transmissions import Transmission, TransmissionReadReceipt
from app.repositories.base import repo
from app.security.tenancy import tenant_context
from app.services import ciq_service, nc_service, transmission_service
from app.services.ciq_service import EntryInput
from app.services.nc_service import NCError
from tests import factories
from tests.conftest import login


# ---------------------------------------------------------------------------
# Transmissions
# ---------------------------------------------------------------------------

@pytest.fixture
def team_setup(app, world):
    with app.app_context(), tenant_context(world.lab_a):
        team = Team(name="Poste automates")
        db.session.add(team)
        db.session.flush()
        m = db.session.scalar(sa.select(Membership).where(Membership.user_id == world.tech2_id))
        m.team_id = team.id
        db.session.commit()
        return team.id


def test_transmission_visibility_and_read_receipt(app, client, world, team_setup):
    login(client, world.emails["tech"])
    response = client.post("/transmissions/nouvelle", data={
        "title": "Étuve instable", "body": "38,6 °C à 14 h", "category": "equipment", "priority": "urgent",
        "teams": [str(team_setup)]})
    assert response.status_code == 302
    tid = response.headers["Location"].rsplit("/", 1)[1]
    # Le lecteur n'est ni auteur ni destinataire : invisible.
    login(client, world.emails["reader"])
    assert client.get(f"/transmissions/{tid}").status_code == 404
    assert "Étuve instable" not in client.get("/transmissions/").data.decode()
    # Le destinataire (via son équipe) la voit ; sa lecture est horodatée une seule fois.
    login(client, world.emails["tech2"])
    assert "Étuve instable" in client.get("/transmissions/?non_lues=1").data.decode()
    assert client.get(f"/transmissions/{tid}").status_code == 200
    client.get(f"/transmissions/{tid}")
    with app.app_context(), tenant_context(world.lab_a):
        receipts = repo(TransmissionReadReceipt).all()
        assert len(receipts) == 1 and receipts[0].user_id == world.tech2_id
        assert repo(Transmission).first().status == "read"
    assert "Étuve instable" not in client.get("/transmissions/?non_lues=1").data.decode()
    # Le responsable qualité voit toutes les transmissions.
    login(client, world.emails["quality"])
    assert client.get(f"/transmissions/{tid}").status_code == 200


def test_transmission_status_flow_and_no_deletion(app, client, world):
    with factories.acting_as(app, world.emails["tech"], world.lab_a):
        t = transmission_service.create(title="Réactif", body="…", category="reagents", priority="normal",
                                        due_on=None, user_ids=[world.tech2_id], team_ids=[], attachments=[])
        db.session.commit()
        tid = t.id
    login(client, world.emails["tech2"])
    client.post(f"/transmissions/{tid}/statut", data={"status": "in_progress", "comment": "je m'en occupe"})
    client.post(f"/transmissions/{tid}/statut", data={"status": "archived"})
    with app.app_context(), tenant_context(world.lab_a):
        t = repo(Transmission).get(tid)
        assert t.status == "archived" and t.archived_at is not None
    # Aucune route de suppression n'existe.
    assert not [r for r in app.url_map.iter_rules() if "transmissions" in r.rule and "DELETE" in r.methods]
    assert not [r for r in app.url_map.iter_rules() if "transmissions" in r.rule and "supprimer" in r.rule]


def test_transmission_requires_recipient(app, world):
    with factories.acting_as(app, world.emails["tech"], world.lab_a):
        with pytest.raises(transmission_service.TransmissionError):
            transmission_service.create(title="x", body="y", category="general", priority="normal", due_on=None,
                                        user_ids=[], team_ids=[], attachments=[])
        with pytest.raises(transmission_service.TransmissionError):
            transmission_service.create(title="x", body="y", category="general", priority="normal", due_on=None,
                                        user_ids=[world.admin_b_id], team_ids=[], attachments=[])


def test_dashboard_shows_unread_transmissions(app, client, world):
    with factories.acting_as(app, world.emails["tech"], world.lab_a):
        transmission_service.create(title="Urgent", body="…", category="general", priority="urgent", due_on=None,
                                    user_ids=[world.reader_id], team_ids=[], attachments=[])
        db.session.commit()
    login(client, world.emails["reader"])
    page = client.get("/tableau-de-bord").data.decode()
    assert '<span class="value">1</span><br>\n      <span>Transmissions non lues' in page


# ---------------------------------------------------------------------------
# Non-conformités
# ---------------------------------------------------------------------------

def _nc(**kw):
    data = {"title": "Écart", "description": "…", "origin": "other", "severity": "minor",
            "detected_on": date(2026, 4, 2)}
    data.update(kw)
    return nc_service.create(data)


def test_nc_numbering_per_lab_and_year(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        assert [_nc().number for _ in range(3)] == ["NC-2026-001", "NC-2026-002", "NC-2026-003"]
        assert _nc(detected_on=date(2027, 1, 3)).number == "NC-2027-001"
    with factories.acting_as(app, world.emails["admin_b"], world.lab_b):
        assert _nc().number == "NC-2026-001"  # numérotation propre à chaque laboratoire


def test_nc_closure_requirements(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        nc = _nc()
        for status in ("open", "analysis", "verification"):
            nc_service.change_status(nc, status, None)
        with pytest.raises(NCError) as exc:
            nc_service.change_status(nc, "closed", None)
        assert "analyse de cause" in str(exc.value)
        nc_service.update(nc, {"root_cause": "Formation insuffisante"})
        with pytest.raises(NCError, match="action corrective ou une justification"):
            nc_service.change_status(nc, "closed", None)
        db.session.add(CorrectiveAction(source_type="non_conformity", source_id=nc.id, description="Former",
                                        status="open"))
        db.session.flush()
        with pytest.raises(NCError, match="efficacité"):
            nc_service.change_status(nc, "closed", None)
        nc_service.update(nc, {"effectiveness_check": "Audit à 3 mois conforme"})
        nc_service.change_status(nc, "closed", "Clôture")
        assert nc.status == "closed" and nc.validated_by_id == world.quality_id
        with pytest.raises(NCError):
            nc_service.update(nc, {"title": "modifié"})
        history = repo(NonConformityStatusChange).all(NonConformityStatusChange.non_conformity_id == nc.id,
                                                      order_by=NonConformityStatusChange.changed_at)
        assert [h.to_status for h in history] == ["draft", "open", "analysis", "verification", "closed"]


def test_nc_closure_reserved_to_quality(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        nc = _nc(root_cause="x", no_action_justification="y", effectiveness_justification="z")
        for status in ("open", "analysis", "verification"):
            nc_service.change_status(nc, status, None)
        nc_id = nc.id
        db.session.commit()
    with factories.acting_as(app, world.emails["tech"], world.lab_a):
        nc = repo(NonConformity).get(nc_id)
        with pytest.raises(NCError):
            nc_service.change_status(nc, "closed", None)


def test_nc_cancellation_requires_justification(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        nc = _nc()
        with pytest.raises(NCError, match="justifiée"):
            nc_service.change_status(nc, "cancelled", "")
        nc_service.change_status(nc, "cancelled", "Doublon de NC-2026-001")
        assert nc.status == "cancelled" and nc.cancel_reason.startswith("Doublon")


def test_ciq_rejection_converted_to_nc(app, client, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        setup = factories.make_ciq_setup(levels=1)
        run, _ = ciq_service.create_run(setup.parameter, factories.run_at(1), [
            EntryInput(setup.levels[0], setup.lots[0], factories.value_for(setup.limits[0], 3.6), "rejet")])
        db.session.commit()
        run_id = run.id
    login(client, world.emails["tech"])
    response = client.post(f"/non-conformites/depuis-serie/{run_id}")
    assert response.status_code == 302
    client.post(f"/non-conformites/depuis-serie/{run_id}")  # pas de doublon
    with app.app_context(), tenant_context(world.lab_a):
        [nc] = repo(NonConformity).all()
        assert nc.origin == "ciq" and nc.source_ciq_run_id == run_id


def test_nc_pages_and_pdf(app, client, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        nc = _nc()
        db.session.commit()
        nc_id = nc.id
    login(client, world.emails["reader"])
    assert "NC-2026-001" in client.get("/non-conformites/?statut=ouvertes").data.decode()
    assert client.get(f"/non-conformites/{nc_id}").status_code == 200
    pdf = client.get(f"/non-conformites/{nc_id}/fiche.pdf")
    assert pdf.status_code == 200 and pdf.data.startswith(b"%PDF")
    assert client.get("/non-conformites/liste.pdf").data.startswith(b"%PDF")
