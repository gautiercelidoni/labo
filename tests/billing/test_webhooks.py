"""Webhooks Stripe : signature, idempotence, synchronisation du statut, lecture seule."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import timedelta

import pytest
import sqlalchemy as sa

from app.extensions import db
from app.models.audit import AuditEvent
from app.models.base import utcnow
from app.models.billing import StripeEvent, Subscription
from app.repositories.base import repo
from app.security.tenancy import system_context, tenant_context
from app.services.access_service import compute_access
from tests.conftest import login

SECRET = "whsec_test_secret"


def signed(payload: dict, secret: str = SECRET, timestamp: int | None = None) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    ts = timestamp or int(time.time())
    signature = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, f"t={ts},v1={signature}"


def post_event(client, payload, **kw):
    body, header = signed(payload, **kw)
    return client.post("/stripe/webhook", data=body, headers={"Stripe-Signature": header},
                       content_type="application/json")


def subscription_event(event_id, lab_id, status="active", event_type="customer.subscription.updated", **extra):
    obj = {"id": "sub_123", "object": "subscription", "customer": "cus_123", "status": status,
           "metadata": {"tenant_id": str(lab_id)}, "cancel_at_period_end": False,
           "items": {"data": [{"price": {"id": "price_test_yearly"},
                               "current_period_end": int(time.time()) + 86400 * 365}]}}
    obj.update(extra)
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


def sub_of(app, lab_id) -> Subscription:
    with app.app_context(), tenant_context(lab_id):
        sub = db.session.scalar(sa.select(Subscription))
        db.session.expunge(sub)
        return sub


def test_invalid_signature_is_rejected(app, client, world):
    payload = subscription_event("evt_1", world.lab_a)
    assert post_event(client, payload, secret="whsec_autre").status_code == 400
    assert client.post("/stripe/webhook", data=b"{}", content_type="application/json").status_code == 400
    body, header = signed(payload, timestamp=int(time.time()) - 3600)  # rejeu tardif
    assert client.post("/stripe/webhook", data=body, headers={"Stripe-Signature": header}).status_code == 400
    assert sub_of(app, world.lab_a).status == "trialing"


def test_subscription_update_syncs_status(app, client, world):
    response = post_event(client, subscription_event("evt_2", world.lab_a))
    assert response.status_code == 200 and response.json["resultat"] == "processed"
    sub = sub_of(app, world.lab_a)
    assert sub.status == "active" and sub.plan == "yearly"
    assert sub.stripe_subscription_id == "sub_123" and sub.stripe_customer_id == "cus_123"
    assert sub.current_period_end > utcnow()
    with app.app_context(), tenant_context(world.lab_a):
        assert repo(AuditEvent).count(AuditEvent.action == "billing.customer.subscription.updated") == 1
    # Le laboratoire B n'est pas touché.
    assert sub_of(app, world.lab_b).status == "trialing"


def test_already_processed_event_is_idempotent(app, client, world):
    payload = subscription_event("evt_3", world.lab_a)
    assert post_event(client, payload).json["resultat"] == "processed"
    # Entre-temps l'état a évolué : le rejeu de l'ancien événement ne doit rien changer.
    post_event(client, subscription_event("evt_4", world.lab_a, status="past_due"))
    response = post_event(client, payload)
    assert response.status_code == 200 and response.json["resultat"] == "duplicate"
    assert sub_of(app, world.lab_a).status == "past_due"
    with app.app_context(), system_context():
        assert db.session.scalar(sa.select(sa.func.count()).select_from(StripeEvent)) == 2


def test_payment_failed_opens_grace_period_then_read_only(app, client, world):
    post_event(client, subscription_event("evt_5", world.lab_a))
    post_event(client, {"id": "evt_6", "type": "invoice.payment_failed",
                        "data": {"object": {"object": "invoice", "customer": "cus_123", "subscription": "sub_123"}}})
    sub = sub_of(app, world.lab_a)
    assert sub.status == "past_due" and sub.grace_until is not None
    with app.app_context():
        from app.models.tenant import Laboratory

        lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == world.lab_a))
        assert compute_access(lab, sub).write_allowed is True
        assert compute_access(lab, sub, now=sub.grace_until + timedelta(seconds=1)).write_allowed is False
    post_event(client, {"id": "evt_7", "type": "invoice.paid",
                        "data": {"object": {"object": "invoice", "customer": "cus_123", "lines": {"data": []}}}})
    sub = sub_of(app, world.lab_a)
    assert sub.status == "active" and sub.grace_until is None


def test_cancellation_leads_to_read_only_without_data_loss(app, client, world):
    post_event(client, subscription_event("evt_8", world.lab_a, event_type="customer.subscription.deleted",
                                          status="canceled", items={"data": []}, current_period_end=int(time.time()) - 10))
    assert sub_of(app, world.lab_a).status == "canceled"
    login(client, world.emails["admin"])
    page = client.get("/tableau-de-bord").data.decode()
    assert "Lecture seule" in page
    assert client.get("/metrologie/equipements/nouveau").status_code == 403


def test_checkout_completed_links_customer(app, client, world):
    post_event(client, {"id": "evt_9", "type": "checkout.session.completed",
                        "data": {"object": {"object": "checkout.session", "client_reference_id": str(world.lab_b),
                                            "customer": "cus_B", "subscription": "sub_B"}}})
    sub = sub_of(app, world.lab_b)
    assert sub.stripe_customer_id == "cus_B" and sub.stripe_subscription_id == "sub_B"
    # Le retour navigateur ne change jamais le statut.
    assert sub.status == "trialing"


def test_unknown_event_type_is_recorded_and_ignored(app, client, world):
    response = post_event(client, {"id": "evt_10", "type": "customer.created", "data": {"object": {}}})
    assert response.json["resultat"] == "ignored"


def test_browser_return_does_not_change_status(app, client, world):
    login(client, world.emails["admin"])
    client.get("/abonnement/retour?statut=succes")
    assert sub_of(app, world.lab_a).status == "trialing"


@pytest.mark.parametrize("status,delta,expected", [
    ("trialing", 1, True), ("trialing", -1, False), ("active", -100, True), ("canceled", 1, True),
    ("canceled", -1, False), ("suspended", 1, False),
])
def test_access_rules(app, world, status, delta, expected):
    with app.app_context():
        from app.models.tenant import Laboratory

        lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == world.lab_a))
        when = utcnow() + timedelta(days=delta)
        sub = Subscription(status=status, trial_ends_at=when, current_period_end=when)
        assert compute_access(lab, sub).write_allowed is expected
