"""Cahier de transmission (V1.1)."""
from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import Timestamps, TenantScoped, UUIDPk, check_in, tenant_fk, tenant_table_args, utcnow

PRIORITIES = ("normal", "important", "urgent")
PRIORITY_LABELS = {"normal": "Normale", "important": "Importante", "urgent": "Urgente"}
TRANSMISSION_STATUSES = ("new", "read", "in_progress", "done", "archived")
TRANSMISSION_STATUS_LABELS = {
    "new": "Nouveau",
    "read": "Lu",
    "in_progress": "En cours",
    "done": "Traité",
    "archived": "Archivé",
}
CATEGORIES = ("general", "equipment", "ciq", "reagents", "samples", "quality", "other")
CATEGORY_LABELS = {
    "general": "Général",
    "equipment": "Équipement",
    "ciq": "CIQ",
    "reagents": "Réactifs / consommables",
    "samples": "Échantillons",
    "quality": "Qualité",
    "other": "Autre",
}


class Transmission(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "transmission"
    __table_args__ = tenant_table_args(
        sa.Index("ix_transmission_status", "tenant_id", "status", "created_at"),
        check_in("priority", PRIORITIES),
        check_in("status", TRANSMISSION_STATUSES),
        check_in("category", CATEGORIES),
    )

    author_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("user_account.id"))
    category: Mapped[str] = mapped_column(sa.String(20), default="general")
    priority: Mapped[str] = mapped_column(sa.String(20), default="normal")
    title: Mapped[str] = mapped_column(sa.String(200))
    body: Mapped[str] = mapped_column(sa.Text)
    due_on: Mapped[date | None]
    status: Mapped[str] = mapped_column(sa.String(20), default="new")
    archived_at: Mapped[datetime | None]

    author = relationship("User", foreign_keys=[author_id], viewonly=True)
    recipients = relationship(
        "TransmissionRecipient",
        primaryjoin="Transmission.id == foreign(TransmissionRecipient.transmission_id)",
        viewonly=True,
    )

    @property
    def priority_label(self) -> str:
        return PRIORITY_LABELS[self.priority]

    @property
    def status_label(self) -> str:
        return TRANSMISSION_STATUS_LABELS[self.status]

    @property
    def category_label(self) -> str:
        return CATEGORY_LABELS[self.category]


class TransmissionRecipient(UUIDPk, TenantScoped, db.Model):
    __tablename__ = "transmission_recipient"
    __table_args__ = tenant_table_args(
        sa.CheckConstraint("(user_id IS NULL) <> (team_id IS NULL)", name="one_target"),
        sa.Index("ix_transmission_recipient_user", "tenant_id", "user_id"),
        sa.Index("ix_transmission_recipient_team", "tenant_id", "team_id"),
        tenant_fk("transmission_id", "transmission"),
        tenant_fk("team_id", "team"),
    )

    transmission_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    team_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)

    user = relationship("User", foreign_keys=[user_id], viewonly=True)
    team = relationship("Team", primaryjoin="Team.id == foreign(TransmissionRecipient.team_id)", viewonly=True)


class TransmissionReadReceipt(UUIDPk, TenantScoped, db.Model):
    __tablename__ = "transmission_read_receipt"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "transmission_id", "user_id"),
        tenant_fk("transmission_id", "transmission"),
    )

    transmission_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("user_account.id"))
    read_at: Mapped[datetime] = mapped_column(default=utcnow)

    user = relationship("User", foreign_keys=[user_id], viewonly=True)


class TransmissionComment(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "transmission_comment"
    __table_args__ = tenant_table_args(tenant_fk("transmission_id", "transmission"))

    transmission_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    author_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("user_account.id"))
    body: Mapped[str] = mapped_column(sa.Text)

    author = relationship("User", foreign_keys=[author_id], viewonly=True)
