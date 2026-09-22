"""Journal d'audit non modifiable : ni par l'application, ni en SQL par le rôle applicatif."""
from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import DBAPIError

from app.extensions import db
from app.models.audit import AuditEvent
from app.repositories.base import repo
from app.security.tenancy import tenant_context
from app.services import audit_service
from tests import factories


def _write_event(app, lab_id):
    with app.app_context(), tenant_context(lab_id):
        audit_service.record("test.event", object_type="test", object_id="1", after={"x": 1})
        db.session.commit()


@pytest.mark.parametrize("statement", [
    "UPDATE audit_event SET action = 'falsifié'",
    "DELETE FROM audit_event",
    "TRUNCATE audit_event",
])
def test_application_role_cannot_alter_audit(app, world, _transaction, statement):
    _write_event(app, world.lab_a)
    savepoint = _transaction.begin_nested()
    with pytest.raises(DBAPIError) as exc:
        _transaction.execute(sa.text(statement))
    assert "permission denied" in str(exc.value).lower()
    savepoint.rollback()


def test_trigger_blocks_even_owner_role(app, world):
    """Défense en profondeur : même le rôle propriétaire est bloqué par le trigger."""
    owner = sa.create_engine(app.config["DATABASE_OWNER_URL"])
    with owner.connect() as conn:
        trans = conn.begin()
        lab_id = conn.execute(sa.text("INSERT INTO laboratory (id, name, slug, timezone, legal_info, retention_days, "
                                      "max_active_users, status, notify_by_email, created_at, updated_at) VALUES "
                                      "(gen_random_uuid(), 'T', 'trigger-test', 'Europe/Paris', '{}', 1, 1, 'active',"
                                      " true, now(), now()) RETURNING id")).scalar()
        conn.execute(sa.text("INSERT INTO audit_event (occurred_at, tenant_id, action) VALUES (now(), :t, 'x')"),
                     {"t": lab_id})
        for statement in ("UPDATE audit_event SET action = 'y'", "DELETE FROM audit_event", "TRUNCATE audit_event"):
            nested = conn.begin_nested()
            with pytest.raises(DBAPIError) as exc:
                conn.execute(sa.text(statement))
            assert "ajout seul" in str(exc.value)
            nested.rollback()
        trans.rollback()
    owner.dispose()


def test_application_has_no_update_path_for_audit(app, world):
    """L'ORM peut ajouter mais toute modification échoue à la validation de la base."""
    _write_event(app, world.lab_a)
    with app.app_context(), tenant_context(world.lab_a):
        event = repo(AuditEvent).first(AuditEvent.action == "test.event")
        event.action = "modifié"
        with pytest.raises(DBAPIError):
            db.session.flush()
        db.session.rollback()


def test_snapshots_exclude_secrets(app, world):
    from app.models.user import User

    with app.app_context():
        user = db.session.scalar(sa.select(User).where(User.email == world.emails["admin"]))
        snap = audit_service.snapshot(user)
        assert "password_hash" not in snap
        assert snap["email"] == world.emails["admin"]


def test_quality_write_produces_audit_event_in_same_transaction(app, world):
    from app.services import metrology_service

    with factories.acting_as(app, world.emails["quality"], world.lab_a):
        eq = metrology_service.save_equipment(None, {
            "name": "Centrifugeuse", "internal_id": "CEN-1", "category_id": None, "manufacturer": None,
            "model": None, "serial_number": None, "location": None, "commissioned_on": None,
            "status": "in_service", "criticality": "low", "responsible_id": None, "notes": None})
        metrology_service.set_status(eq, "maintenance", "Révision annuelle")
        events = repo(AuditEvent).all(AuditEvent.object_id == str(eq.id), order_by=AuditEvent.id)
        assert [e.action for e in events] == ["equipment.created", "equipment.status_changed"]
        assert events[1].before == {"status": "in_service"} and events[1].after == {"status": "maintenance"}
        assert events[1].reason == "Révision annuelle"
        assert events[1].user_id == world.quality_id


def test_audit_page_and_csv_export(client, world, app):
    from tests.conftest import login

    _write_event(app, world.lab_a)
    login(client, world.emails["quality"])
    assert "test.event" in client.get("/audit/?action=test").data.decode()
    csv = client.get("/audit/export.csv")
    assert csv.status_code == 200 and "test.event" in csv.data.decode("utf-8-sig")
