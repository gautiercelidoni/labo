"""Notifications générées par le cron (échéances, retards) et affichées dans l'application."""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import Timestamps, TenantScoped, UUIDPk, check_in, tenant_table_args

NOTIFICATION_LEVELS = ("info", "warning", "danger")


class Notification(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "notification"
    __table_args__ = tenant_table_args(
        # Le cron peut tourner plusieurs fois sans créer de doublon.
        sa.UniqueConstraint("tenant_id", "dedup_key"),
        sa.Index("ix_notification_user_read", "tenant_id", "user_id", "read_at"),
        check_in("level", NOTIFICATION_LEVELS),
    )

    # Nul : notification destinée à tous les responsables (admin + qualité).
    user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    kind: Mapped[str] = mapped_column(sa.String(40))
    object_type: Mapped[str | None] = mapped_column(sa.String(40))
    object_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    level: Mapped[str] = mapped_column(sa.String(10), default="info")
    message: Mapped[str] = mapped_column(sa.Text)
    link: Mapped[str | None] = mapped_column(sa.String(255))
    dedup_key: Mapped[str] = mapped_column(sa.String(200))
    read_at: Mapped[datetime | None]
    emailed_at: Mapped[datetime | None]
