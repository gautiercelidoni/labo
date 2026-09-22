"""Abonnement Stripe d'un laboratoire et journal des événements Stripe reçus."""
from __future__ import annotations

from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import Timestamps, TenantScoped, UUIDPk, check_in, check_in_nullable, tenant_table_args, utcnow

PLANS = ("monthly", "yearly")
PLAN_LABELS = {"monthly": "Mensuel", "yearly": "Annuel"}
SUBSCRIPTION_STATUSES = ("trialing", "active", "past_due", "canceled", "suspended")
SUBSCRIPTION_STATUS_LABELS = {
    "trialing": "Essai",
    "active": "Actif",
    "past_due": "Paiement en retard",
    "canceled": "Résilié",
    "suspended": "Suspendu",
}


class Subscription(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "subscription"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", name="uq_subscription_tenant"),
        check_in_nullable("plan", PLANS),
        check_in("status", SUBSCRIPTION_STATUSES),
    )

    stripe_customer_id: Mapped[str | None] = mapped_column(sa.String(80), unique=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(sa.String(80), unique=True)
    plan: Mapped[str | None] = mapped_column(sa.String(20))
    status: Mapped[str] = mapped_column(sa.String(20), default="trialing")
    trial_ends_at: Mapped[datetime | None]
    current_period_end: Mapped[datetime | None]
    grace_until: Mapped[datetime | None]
    cancel_at_period_end: Mapped[bool] = mapped_column(default=False, server_default=sa.false())

    @property
    def status_label(self) -> str:
        return SUBSCRIPTION_STATUS_LABELS[self.status]

    @property
    def plan_label(self) -> str:
        return PLAN_LABELS.get(self.plan or "", "—")


class StripeEvent(db.Model):
    __tablename__ = "stripe_event"

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    stripe_event_id: Mapped[str] = mapped_column(sa.String(80), unique=True)
    type: Mapped[str] = mapped_column(sa.String(80))
    received_at: Mapped[datetime] = mapped_column(default=utcnow)
    processed_at: Mapped[datetime | None]
    payload: Mapped[dict] = mapped_column(JSONB)
