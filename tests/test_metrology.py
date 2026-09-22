"""Métrologie : échéances depuis la date effective, suspension proposée, alertes cron idempotentes."""
from __future__ import annotations

import io
from datetime import date, timedelta

import sqlalchemy as sa

from app.extensions import db
from app.models.equipment import Equipment, MaintenanceEvent, MaintenancePlan
from app.models.files import Attachment
from app.models.notifications import Notification
from app.repositories.base import repo
from app.security.tenancy import tenant_context
from app.services import email_service, metrology_service
from app.services.notification_service import run_all
from tests import factories
from tests.conftest import login

PDF = b"%PDF-1.4\n%%EOF\n"


def _plan(eq, **kw):
    data = {"event_type": "calibration", "period_value": 1, "period_unit": "year", "provider": "Métrologie SA",
            "responsible_id": None, "last_done_on": None, "next_due_on": None, "comment": None, "is_active": True}
    data.update(kw)
    return metrology_service.save_plan(eq, None, data)


def test_event_reschedules_from_effective_date(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        eq = factories.make_equipment("Étuve", "ETU-1")
        plan = _plan(eq, last_done_on=date(2025, 3, 1))
        assert plan.next_due_on == date(2026, 3, 1)
        # Réalisée en retard le 20 mars : la prochaine échéance part du 20 mars.
        metrology_service.record_event(eq, plan, event_type="calibration", performed_on=date(2026, 3, 20),
                                       outcome="conform", provider=None, comment="ok", attachment=None)
        assert plan.last_done_on == date(2026, 3, 20)
        assert plan.next_due_on == date(2027, 3, 20)


def test_older_event_does_not_move_due_date_backwards(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        eq = factories.make_equipment("Étuve", "ETU-1")
        plan = _plan(eq, last_done_on=date(2026, 3, 1))
        metrology_service.record_event(eq, plan, event_type="calibration", performed_on=date(2025, 1, 10),
                                       outcome="conform", provider=None, comment="saisie tardive", attachment=None)
        assert plan.next_due_on == date(2027, 3, 1)


def test_non_conform_calibration_proposes_suspension(app, client, world):
    with app.app_context(), tenant_context(world.lab_a):
        eq = factories.make_equipment("Balance", "BAL-1")
        db.session.commit()
        eq_id = eq.id
    login(client, world.emails["tech"])
    response = client.post(f"/metrologie/equipements/{eq_id}/realisations", data={
        "plan_id": "", "event_type": "calibration", "performed_on": "2026-05-02", "outcome": "non_conform",
        "comment": "Écart hors tolérance", "attachment": (io.BytesIO(PDF), "certificat.pdf")},
        content_type="multipart/form-data")
    assert response.status_code == 302 and "suspendre=1" in response.headers["Location"]
    page = client.get(response.headers["Location"]).data.decode()
    assert "Suspendre l&#39;équipement" in page or "Suspendre l'équipement" in page
    assert client.post(f"/metrologie/equipements/{eq_id}/suspendre").status_code == 302
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(Equipment).get(eq_id).status == "suspended"
        event = repo(MaintenanceEvent).first()
        assert repo(Attachment).first(Attachment.owner_type == "maintenance_event",
                                      Attachment.owner_id == event.id) is not None


def test_suspend_shortcut_requires_non_conform_calibration(app, client, world):
    with app.app_context(), tenant_context(world.lab_a):
        eq = factories.make_equipment("Balance", "BAL-1")
        db.session.commit()
        eq_id = eq.id
    login(client, world.emails["tech"])
    assert client.post(f"/metrologie/equipements/{eq_id}/suspendre").status_code == 403


def _setup_due_plans(app, world):
    today = date.today()
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        eq = factories.make_equipment("Spectro", "SPE-1")
        for offset in (20, 5, 0, -10, 200):
            _plan(eq, next_due_on=today + timedelta(days=offset), responsible_id=world.tech_id)
        db.session.commit()


def test_notifications_cron_thresholds_and_idempotence(app, world):
    _setup_due_plans(app, world)
    with app.app_context():
        stats = run_all()
        assert stats["laboratoires"] == 2
    with app.app_context(), tenant_context(world.lab_a):
        kinds = db.session.scalars(sa.select(Notification.dedup_key)).all()
        thresholds = sorted({k.split(":")[3] for k in kinds})
        assert thresholds == ["J-30", "J-7", "J0", "retard"]  # l'échéance à +200 j ne génère rien
        # Destinataires : admin, qualité, consultant (qualité) et le technicien responsable.
        recipients = set(db.session.scalars(sa.select(Notification.user_id)).all())
        assert recipients == {world.admin_id, world.quality_id, world.consultant_id, world.tech_id}
        count = repo(Notification).count()
    assert len(email_service.outbox) == 4  # un récapitulatif par destinataire
    with app.app_context():
        run_all()  # le cron peut tourner deux fois sans doublon
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(Notification).count() == count
    assert len(email_service.outbox) == 4


def test_notifications_page_and_badge(app, client, world):
    _setup_due_plans(app, world)
    with app.app_context():
        run_all()
    login(client, world.emails["tech"])
    page = client.get("/tableau-de-bord").data.decode()
    assert 'badge badge-danger">4<' in page
    assert client.post("/notifications/tout-lire").status_code == 302
    assert 'badge badge-danger">' not in client.get("/tableau-de-bord").data.decode().split("</header>")[0]


def test_dashboard_counts_due_dates(app, client, world):
    _setup_due_plans(app, world)
    login(client, world.emails["reader"])
    page = client.get("/tableau-de-bord").data.decode()
    assert '<span class="value">3</span><br>\n      <span>Échéances métrologiques sous 30 jours' in page
    assert '<span class="value">1</span><br>\n      <span>Échéances métrologiques dépassées' in page


def test_due_list_filters_and_calendar(app, client, world):
    _setup_due_plans(app, world)
    login(client, world.emails["reader"])
    assert client.get("/metrologie/echeances?filtre=retard").data.decode().count("En retard") >= 1
    assert "SPE-1" in client.get("/metrologie/calendrier").data.decode()
    csv = client.get("/metrologie/echeances/export.csv?filtre=tous").data.decode("utf-8-sig")
    assert csv.count("\r\n") == 6


def test_recompute_due_dates(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        eq = factories.make_equipment("Spectro", "SPE-1")
        plan = _plan(eq, last_done_on=date(2026, 1, 15), next_due_on=date(2030, 1, 1))
        assert metrology_service.recompute_due_dates() == 1
        assert plan.next_due_on == date(2027, 1, 15)


def test_archive_keeps_history_and_disables_plans(app, world):
    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        eq = factories.make_equipment("Vieille balance", "OLD-1")
        _plan(eq, last_done_on=date(2025, 1, 1))
        metrology_service.archive_equipment(eq, "Réformée")
        assert eq.status == "retired" and eq.archived_at is not None
        assert repo(MaintenancePlan).count(MaintenancePlan.is_active.is_(True)) == 0
        assert repo(Equipment).get(eq.id) is not None
