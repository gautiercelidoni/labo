"""Actions correctives (V1 : liées aux rejets CIQ ; V1.1 : réutilisées par les non-conformités)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import Timestamps, TenantScoped, UUIDPk, check_in, tenant_table_args

CA_SOURCE_TYPES = ("ciq_run", "non_conformity", "manual")
CA_STATUSES = ("open", "done", "validated")
CA_STATUS_LABELS = {"open": "Ouverte", "done": "Réalisée", "validated": "Validée"}


class CorrectiveAction(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "corrective_action"
    __table_args__ = tenant_table_args(
        sa.Index("ix_corrective_action_status_due", "tenant_id", "status", "due_on"),
        sa.Index("ix_corrective_action_source", "tenant_id", "source_type", "source_id"),
        check_in("source_type", CA_SOURCE_TYPES),
        check_in("status", CA_STATUSES),
    )

    source_type: Mapped[str] = mapped_column(sa.String(30))
    source_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    description: Mapped[str] = mapped_column(sa.Text)
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    due_on: Mapped[date | None]
    done_on: Mapped[date | None]
    done_comment: Mapped[str | None] = mapped_column(sa.Text)
    validated_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    validated_at: Mapped[datetime | None]
    status: Mapped[str] = mapped_column(sa.String(20), default="open")
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    responsible = relationship("User", foreign_keys=[responsible_id], viewonly=True)
    validated_by = relationship("User", foreign_keys=[validated_by_id], viewonly=True)

    @property
    def status_label(self) -> str:
        return CA_STATUS_LABELS[self.status]
