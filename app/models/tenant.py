"""Laboratoires (tenants), appartenances, équipes et invitations."""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import (
    Timestamps,
    TenantScoped,
    UUIDPk,
    check_in,
    tenant_fk,
    tenant_table_args,
    utcnow,
)

LAB_STATUSES = ("active", "read_only", "suspended")
ROLES = ("admin", "quality", "technician", "reader")

ROLE_LABELS = {
    "admin": "Administrateur",
    "quality": "Responsable qualité",
    "technician": "Technicien",
    "reader": "Lecture seule",
}


class Laboratory(UUIDPk, Timestamps, db.Model):
    __tablename__ = "laboratory"
    __table_args__ = (check_in("status", LAB_STATUSES),)

    name: Mapped[str] = mapped_column(sa.String(200))
    slug: Mapped[str] = mapped_column(sa.String(80), unique=True)
    timezone: Mapped[str] = mapped_column(sa.String(64), default="Europe/Paris")
    logo_attachment_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    legal_info: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    retention_days: Mapped[int] = mapped_column(default=3650)
    max_active_users: Mapped[int] = mapped_column(default=15)
    status: Mapped[str] = mapped_column(sa.String(20), default="active")
    notify_by_email: Mapped[bool] = mapped_column(default=True, server_default=sa.true())


class Team(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "team"
    __table_args__ = tenant_table_args(sa.UniqueConstraint("tenant_id", "name"))

    name: Mapped[str] = mapped_column(sa.String(120))
    description: Mapped[str | None] = mapped_column(sa.Text)


class Membership(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "membership"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "user_id"),
        check_in("role", ROLES),
        tenant_fk("team_id", "team"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.ForeignKey("user_account.id", ondelete="RESTRICT"), index=True
    )
    role: Mapped[str] = mapped_column(sa.String(20))
    team_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    is_active: Mapped[bool] = mapped_column(default=True)
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)

    user = relationship("User", lazy="joined", viewonly=True)
    laboratory = relationship(
        "Laboratory", primaryjoin="Laboratory.id == foreign(Membership.tenant_id)", viewonly=True
    )
    team = relationship("Team", primaryjoin="Team.id == foreign(Membership.team_id)", viewonly=True)

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)


class Invitation(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "invitation"
    __table_args__ = tenant_table_args(
        sa.Index("ix_invitation_tenant_email", "tenant_id", "email"),
        check_in("role", ROLES),
        tenant_fk("team_id", "team"),
    )

    email: Mapped[str] = mapped_column(sa.String(254))
    role: Mapped[str] = mapped_column(sa.String(20))
    team_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    expires_at: Mapped[datetime]
    accepted_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]
    invited_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    @property
    def role_label(self) -> str:
        return ROLE_LABELS.get(self.role, self.role)

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.revoked_at is None and self.expires_at > utcnow()
