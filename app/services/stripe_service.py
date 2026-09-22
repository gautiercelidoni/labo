"""Abonnement Stripe : Checkout, portail client, webhooks vérifiés et idempotents.

L'état réel de l'abonnement provient UNIQUEMENT des webhooks : le retour navigateur après
Checkout n'est jamais pris en compte. Aucune donnée bancaire n'est stockée.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import sqlalchemy as sa
import stripe
from flask import current_app
from flask_login import current_user
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.base import utcnow
from app.models.billing import StripeEvent, Subscription
from app.models.tenant import Laboratory
from app.repositories.base import parse_uuid
from app.security.tenancy import current_tenant_id, system_context, tenant_context
from app.services import audit_service


class BillingError(RuntimeError):
    pass


class InvalidSignature(ValueError):
    pass


STATUS_MAP = {
    "trialing": "trialing",
    "active": "active",
    "past_due": "past_due",
    "unpaid": "suspended",
    "paused": "suspended",
    "canceled": "canceled",
    "incomplete_expired": "canceled",
}


def _key() -> str:
    key = current_app.config["STRIPE_SECRET_KEY"]
    if not key:
        raise BillingError("Stripe n'est pas configuré (STRIPE_SECRET_KEY manquant).")
    return key


def _ts(value) -> datetime | None:
    return datetime.fromtimestamp(int(value), tz=timezone.utc) if value else None


def current_subscription() -> Subscription | None:
    return db.session.scalars(sa.select(Subscription)).first()


def plan_for_price(price_id: str | None) -> str | None:
    cfg = current_app.config
    return {cfg["STRIPE_PRICE_MONTHLY"]: "monthly", cfg["STRIPE_PRICE_YEARLY"]: "yearly"}.get(price_id or "")


def _invoice_footer() -> str:
    cfg = current_app.config
    parts = [cfg["INVOICE_ISSUER_NAME"], cfg["INVOICE_ISSUER_ADDRESS"],
             f"SIRET {cfg['INVOICE_ISSUER_SIRET']}" if cfg["INVOICE_ISSUER_SIRET"] else ""]
    if not cfg["STRIPE_TAX_ENABLED"] and cfg["VAT_EXEMPTION_MENTION"]:
        parts.append(cfg["VAT_EXEMPTION_MENTION"])
    return " — ".join(p for p in parts if p)[:5000]


def ensure_customer(lab: Laboratory, sub: Subscription) -> str:
    if sub.stripe_customer_id:
        return sub.stripe_customer_id
    params = {
        "name": lab.name,
        "email": current_user.email,
        "metadata": {"tenant_id": str(lab.id)},
    }
    footer = _invoice_footer()
    if footer:
        params["invoice_settings"] = {"footer": footer}
    customer = stripe.Customer.create(api_key=_key(), **params)
    sub.stripe_customer_id = customer["id"]
    audit_service.record("billing.customer_created", sub, after={"stripe_customer_id": customer["id"]})
    db.session.commit()
    return customer["id"]


def create_checkout_url(plan: str) -> str:
    cfg = current_app.config
    price = {"monthly": cfg["STRIPE_PRICE_MONTHLY"], "yearly": cfg["STRIPE_PRICE_YEARLY"]}.get(plan)
    if not price:
        raise BillingError("Offre inconnue ou non configurée.")
    lab = db.session.scalar(sa.select(Laboratory).where(Laboratory.id == current_tenant_id()))
    sub = current_subscription()
    if sub is None:
        sub = Subscription(status="trialing", trial_ends_at=utcnow())
        db.session.add(sub)
        db.session.flush()
    customer_id = ensure_customer(lab, sub)
    subscription_data: dict = {"metadata": {"tenant_id": str(lab.id)}}
    # L'essai restant est conservé si le laboratoire s'abonne pendant sa période d'essai.
    if sub.status == "trialing" and sub.trial_ends_at and sub.trial_ends_at > utcnow() + timedelta(days=2):
        subscription_data["trial_end"] = int(sub.trial_ends_at.timestamp())
    params = {
        "mode": "subscription",
        "customer": customer_id,
        "client_reference_id": str(lab.id),
        "line_items": [{"price": price, "quantity": 1}],
        "subscription_data": subscription_data,
        "success_url": cfg["BASE_URL"].rstrip("/") + "/abonnement/retour?statut=succes",
        "cancel_url": cfg["BASE_URL"].rstrip("/") + "/abonnement/retour?statut=annule",
        "locale": "fr",
        "billing_address_collection": "required",
        "customer_update": {"address": "auto", "name": "auto"},
        "tax_id_collection": {"enabled": True},
        "metadata": {"tenant_id": str(lab.id)},
    }
    if cfg["STRIPE_TAX_ENABLED"]:
        params["automatic_tax"] = {"enabled": True}
    session = stripe.checkout.Session.create(api_key=_key(), **params)
    audit_service.record("billing.checkout_started", sub, after={"plan": plan})
    db.session.commit()
    return session["url"]


def create_portal_url() -> str:
    sub = current_subscription()
    if sub is None or not sub.stripe_customer_id:
        raise BillingError("Aucun client Stripe associé : souscrivez d'abord une offre.")
    session = stripe.billing_portal.Session.create(
        api_key=_key(), customer=sub.stripe_customer_id,
        return_url=current_app.config["BASE_URL"].rstrip("/") + "/abonnement",
    )
    return session["url"]


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

def verify(payload: bytes, signature: str | None) -> dict:
    secret = current_app.config["STRIPE_WEBHOOK_SECRET"]
    if not secret or not signature:
        raise InvalidSignature("Signature absente.")
    try:
        stripe.WebhookSignature.verify_header(payload, signature, secret, tolerance=300)
    except stripe.SignatureVerificationError as e:
        raise InvalidSignature(str(e))
    try:
        return json.loads(payload)
    except ValueError:
        raise InvalidSignature("Contenu invalide.")


def _find_subscription(obj: dict) -> Subscription | None:
    """Recherche inter-labos du laboratoire concerné (contexte système du webhook)."""
    candidates = []
    metadata = obj.get("metadata") or {}
    tenant_id = parse_uuid(metadata.get("tenant_id") or obj.get("client_reference_id"))
    if tenant_id:
        candidates.append(Subscription.tenant_id == tenant_id)
    if obj.get("object") == "subscription" and obj.get("id"):
        candidates.append(Subscription.stripe_subscription_id == obj["id"])
    if obj.get("subscription") and isinstance(obj["subscription"], str):
        candidates.append(Subscription.stripe_subscription_id == obj["subscription"])
    customer = obj.get("customer")
    if isinstance(customer, str):
        candidates.append(Subscription.stripe_customer_id == customer)
    for criterion in candidates:
        sub = db.session.scalars(sa.select(Subscription).where(criterion)).first()
        if sub is not None:
            return sub
    return None


def _period_end(obj: dict) -> datetime | None:
    if obj.get("current_period_end"):
        return _ts(obj["current_period_end"])
    items = (obj.get("items") or {}).get("data") or []
    ends = [i.get("current_period_end") for i in items if i.get("current_period_end")]
    return _ts(max(ends)) if ends else None


def _apply(sub: Subscription, event_type: str, obj: dict) -> None:
    before = audit_service.snapshot(sub)
    now = utcnow()
    if event_type == "checkout.session.completed":
        if obj.get("customer"):
            sub.stripe_customer_id = obj["customer"]
        if obj.get("subscription"):
            sub.stripe_subscription_id = obj["subscription"]
    elif event_type in ("customer.subscription.created", "customer.subscription.updated",
                        "customer.subscription.deleted"):
        sub.stripe_subscription_id = obj.get("id") or sub.stripe_subscription_id
        if isinstance(obj.get("customer"), str):
            sub.stripe_customer_id = obj["customer"]
        status = "canceled" if event_type == "customer.subscription.deleted" else STATUS_MAP.get(obj.get("status", ""))
        if status:
            sub.status = status
        items = (obj.get("items") or {}).get("data") or []
        if items:
            plan = plan_for_price((items[0].get("price") or {}).get("id"))
            if plan:
                sub.plan = plan
        sub.trial_ends_at = _ts(obj.get("trial_end")) or (sub.trial_ends_at if status == "trialing" else None)
        sub.current_period_end = _period_end(obj) or sub.current_period_end
        sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
        if sub.status == "past_due" and sub.grace_until is None:
            sub.grace_until = now + timedelta(days=current_app.config["GRACE_DAYS"])
        if sub.status in ("active", "trialing"):
            sub.grace_until = None
    elif event_type == "invoice.payment_failed":
        sub.status = "past_due"
        if sub.grace_until is None:
            sub.grace_until = now + timedelta(days=current_app.config["GRACE_DAYS"])
    elif event_type == "invoice.paid":
        if sub.status in ("past_due", "suspended", "trialing"):
            sub.status = "active"
        sub.grace_until = None
        lines = ((obj.get("lines") or {}).get("data") or [])
        ends = [((line.get("period") or {}).get("end")) for line in lines if line.get("period")]
        if ends:
            sub.current_period_end = _ts(max(ends))
    audit_service.record_change(f"billing.{event_type}", sub, before)


HANDLED = {
    "checkout.session.completed",
    "customer.subscription.created",
    "customer.subscription.updated",
    "customer.subscription.deleted",
    "invoice.payment_failed",
    "invoice.paid",
}


def handle_event(event: dict) -> str:
    """Traite un événement vérifié. Renvoie 'processed', 'duplicate', 'ignored' ou 'unmatched'."""
    event_id = event.get("id")
    event_type = event.get("type", "")
    if not event_id:
        raise InvalidSignature("Événement sans identifiant.")
    with system_context():
        existing = db.session.scalar(sa.select(StripeEvent).where(StripeEvent.stripe_event_id == event_id)
                                     .with_for_update())
        if existing is not None and existing.processed_at is not None:
            return "duplicate"
        record = existing or StripeEvent(stripe_event_id=event_id, type=event_type, payload=event)
        if existing is None:
            db.session.add(record)
        outcome = "ignored"
        sub = None
        if event_type in HANDLED:
            obj = (event.get("data") or {}).get("object") or {}
            sub = _find_subscription(obj)
            outcome = "unmatched" if sub is None else "processed"
    # La modification et le commit ont lieu dans le contexte du laboratoire concerné.
    with tenant_context(sub.tenant_id) if sub is not None else system_context():
        if sub is not None:
            _apply(sub, event_type, obj)
        record.processed_at = utcnow()
        try:
            db.session.commit()
        except IntegrityError:
            # Même événement reçu en parallèle et déjà enregistré : idempotence garantie par l'unicité.
            db.session.rollback()
            return "duplicate"
    return outcome
