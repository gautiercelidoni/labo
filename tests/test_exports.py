"""Exports PDF (génération réelle WeasyPrint) et export administratif."""
from __future__ import annotations

import io
import zipfile

from app.extensions import db
from app.models.audit import AuditEvent
from app.repositories.base import repo
from app.security.tenancy import tenant_context
from app.services import ciq_service
from app.services.ciq_service import EntryInput
from tests import factories
from tests.conftest import login


def _ciq_data(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        setup = factories.make_ciq_setup(levels=2)
        for i, (z1, z2) in enumerate([(0.2, -0.3), (2.4, 0.1), (3.5, 0.2)]):
            ciq_service.create_run(setup.parameter, factories.run_at(10 - i), [
                EntryInput(setup.levels[0], setup.lots[0], factories.value_for(setup.limits[0], z1), "c"),
                EntryInput(setup.levels[1], setup.lots[1], factories.value_for(setup.limits[1], z2), "c"),
            ])
        db.session.commit()
        return setup.parameter.id, setup.equipment.id


def test_monthly_ciq_pdf(app, client, world):
    param_id, _ = _ciq_data(app, world)
    login(client, world.emails["reader"])
    response = client.get(f"/ciq/parametres/{param_id}/rapport.pdf?mois=2026-05")
    assert response.status_code == 200
    assert response.data.startswith(b"%PDF")
    assert response.headers["Content-Disposition"].startswith("attachment")
    with app.app_context(), tenant_context(world.lab_a):
        event = repo(AuditEvent).first(AuditEvent.action == "export.pdf")
        assert event.after["periode"] == "mai 2026"


def test_equipment_and_planning_pdfs(app, client, world):
    _, eq_id = _ciq_data(app, world)
    login(client, world.emails["quality"])
    for url in (f"/metrologie/equipements/{eq_id}/historique.pdf", "/metrologie/echeances/planning.pdf?filtre=tous"):
        response = client.get(url)
        assert response.status_code == 200 and response.data.startswith(b"%PDF"), url


def test_pdf_contains_report_metadata(app, world):
    from app.services import export_pdf

    param_id, _ = _ciq_data(app, world)
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        from flask import render_template

        from app.models.ciq import CIQParameter

        parameter = repo(CIQParameter).get(param_id)
        html = render_template("pdf/base.html", title="Titre", period="mai 2026", filters={"paramètre": "Glucose"},
                               lab=export_pdf.lab_service.get_current_lab(), logo_uri=None, report_id="ABC123",
                               generated_at=factories.run_at(0), author="Qualité A")
        assert "Laboratoire A" in html and "ABC123" in html and "mai 2026" in html and "Qualité A" in html
        assert 'counter(page)' in html and "paramètre = Glucose" in html
        assert parameter is not None


def test_svg_chart_marks_statuses():
    from app.services.export_pdf import control_chart_svg

    svg = str(control_chart_svg([{"z": 0.1, "status": "accepted"}, {"z": 3.4, "status": "rejected",
                                                                    "lot_change": True}]))
    assert svg.startswith("<svg") and "#c00" in svg and "stroke-dasharray=\"2 2\"" in svg


def test_admin_lab_export_zip(app, client, world):
    _ciq_data(app, world)
    login(client, world.emails["admin"])
    response = client.post("/administration/export-donnees")
    archive = zipfile.ZipFile(io.BytesIO(response.data))
    names = set(archive.namelist())
    assert {"membres.csv", "ciq_resultats.csv", "audit.csv", "equipements.csv", "LISEZMOI.txt"} <= names
    results = archive.read("ciq_resultats.csv").decode("utf-8-sig")
    assert results.count("\r\n") == 7  # en-tête + 6 résultats
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(AuditEvent).count(AuditEvent.action == "export.lab_data") == 1


def test_csv_exports_are_audited(app, client, world):
    login(client, world.emails["reader"])
    client.get("/actions-correctives/export.csv")
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(AuditEvent).count(AuditEvent.action == "export.csv") == 1
