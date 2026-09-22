"""Journal d'audit append-only (protégé aussi au niveau PostgreSQL, cf. migration)."""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import TenantFiltered, utcnow


class AuditEvent(TenantFiltered, db.Model):
    __tablename__ = "audit_event"
    __table_args__ = (
        sa.Index("ix_audit_event_tenant_occurred", "tenant_id", sa.text("occurred_at DESC")),
        sa.Index("ix_audit_event_tenant_object", "tenant_id", "object_type", "object_id"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=sa.func.now())
    # Nullable : certains événements sont globaux (échec de connexion sur email inconnu).
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("laboratory.id"))
    user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    action: Mapped[str] = mapped_column(sa.String(80))
    object_type: Mapped[str | None] = mapped_column(sa.String(60))
    object_id: Mapped[str | None] = mapped_column(sa.String(64))
    ip: Mapped[str | None] = mapped_column(sa.String(64))
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(sa.Text)
    request_id: Mapped[str | None] = mapped_column(sa.String(64))

    user = relationship("User", viewonly=True, lazy="joined")
