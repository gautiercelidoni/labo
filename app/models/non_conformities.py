"""Non-conformités (V1.1)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import (
    Timestamps,
    TenantScoped,
    UUIDPk,
    check_in,
    check_in_nullable,
    tenant_fk,
    tenant_table_args,
    utcnow,
)

NC_STATUSES = ("draft", "open", "analysis", "action", "verification", "closed", "cancelled")
NC_STATUS_LABELS = {
    "draft": "Brouillon",
    "open": "Ouverte",
    "analysis": "En analyse",
    "action": "Action en cours",
    "verification": "En vérification",
    "closed": "Clôturée",
    "cancelled": "Annulée",
}
NC_SEVERITIES = ("minor", "major", "critical")
NC_SEVERITY_LABELS = {"minor": "Mineure", "major": "Majeure", "critical": "Critique"}
NC_ORIGINS = ("ciq", "metrology", "audit_internal", "audit_external", "complaint", "supplier", "other")
NC_ORIGIN_LABELS = {
    "ciq": "Contrôle qualité interne",
    "metrology": "Métrologie / équipement",
    "audit_internal": "Audit interne",
    "audit_external": "Audit externe",
    "complaint": "Réclamation client",
    "supplier": "Fournisseur",
    "other": "Autre",
}


class NonConformity(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "non_conformity"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "year", "seq"),
        sa.Index("ix_non_conformity_status", "tenant_id", "status"),
        check_in("status", NC_STATUSES),
        check_in("severity", NC_SEVERITIES),
        check_in("origin", NC_ORIGINS),
        tenant_fk("source_ciq_run_id", "ciq_run"),
        tenant_fk("equipment_id", "equipment"),
    )

    year: Mapped[int]
    seq: Mapped[int]
    title: Mapped[str] = mapped_column(sa.String(200))
    description: Mapped[str] = mapped_column(sa.Text)
    detected_on: Mapped[date]
    detected_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    origin: Mapped[str] = mapped_column(sa.String(30), default="other")
    severity: Mapped[str] = mapped_column(sa.String(20), default="minor")
    impact: Mapped[str | None] = mapped_column(sa.Text)
    immediate_action: Mapped[str | None] = mapped_column(sa.Text)
    root_cause: Mapped[str | None] = mapped_column(sa.Text)
    no_action_justification: Mapped[str | None] = mapped_column(sa.Text)
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    target_date: Mapped[date | None]
    effectiveness_check: Mapped[str | None] = mapped_column(sa.Text)
    effectiveness_justification: Mapped[str | None] = mapped_column(sa.Text)
    closed_on: Mapped[date | None]
    validated_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    status: Mapped[str] = mapped_column(sa.String(20), default="draft")
    cancel_reason: Mapped[str | None] = mapped_column(sa.Text)
    source_ciq_run_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    equipment_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    detected_by = relationship("User", foreign_keys=[detected_by_id], viewonly=True)
    responsible = relationship("User", foreign_keys=[responsible_id], viewonly=True)
    validated_by = relationship("User", foreign_keys=[validated_by_id], viewonly=True)

    @property
    def number(self) -> str:
        return f"NC-{self.year}-{self.seq:03d}"

    @property
    def status_label(self) -> str:
        return NC_STATUS_LABELS[self.status]

    @property
    def severity_label(self) -> str:
        return NC_SEVERITY_LABELS[self.severity]

    @property
    def origin_label(self) -> str:
        return NC_ORIGIN_LABELS[self.origin]


class NonConformityStatusChange(UUIDPk, TenantScoped, db.Model):
    __tablename__ = "non_conformity_status_change"
    __table_args__ = tenant_table_args(
        check_in_nullable("from_status", NC_STATUSES, name="from_status"),
        check_in("to_status", NC_STATUSES, name="to_status"),
        tenant_fk("non_conformity_id", "non_conformity"),
    )

    non_conformity_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    from_status: Mapped[str | None] = mapped_column(sa.String(20))
    to_status: Mapped[str] = mapped_column(sa.String(20))
    changed_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    changed_at: Mapped[datetime] = mapped_column(default=utcnow)
    comment: Mapped[str | None] = mapped_column(sa.Text)

    changed_by = relationship("User", foreign_keys=[changed_by_id], viewonly=True)
