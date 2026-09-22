"""Pièces jointes : métadonnées en base, contenu dans le stockage (local ou S3)."""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import Timestamps, TenantScoped, UUIDPk, check_in, tenant_table_args

OWNER_TYPES = (
    "laboratory",
    "equipment",
    "maintenance_event",
    "corrective_action",
    "transmission",
    "non_conformity",
)


class Attachment(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "attachment"
    __table_args__ = tenant_table_args(
        sa.Index("ix_attachment_owner", "tenant_id", "owner_type", "owner_id"),
        check_in("owner_type", OWNER_TYPES),
    )

    storage_key: Mapped[str] = mapped_column(sa.String(120), unique=True)
    original_name: Mapped[str] = mapped_column(sa.String(255))
    content_type: Mapped[str] = mapped_column(sa.String(120))
    size_bytes: Mapped[int] = mapped_column(sa.BigInteger)
    sha256: Mapped[str] = mapped_column(sa.String(64))
    owner_type: Mapped[str] = mapped_column(sa.String(40))
    owner_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    uploaded_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    archived_at: Mapped[datetime | None]
