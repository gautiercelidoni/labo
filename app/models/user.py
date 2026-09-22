"""Comptes utilisateurs (globaux), jetons email et tentatives de connexion."""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from flask_login import UserMixin
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column

from app.extensions import db
from app.models.base import Timestamps, UUIDPk, check_in, utcnow

TOKEN_PURPOSES = ("reset_password", "verify_email")


class User(UUIDPk, Timestamps, UserMixin, db.Model):
    # « user » est un mot réservé PostgreSQL : table nommée user_account.
    __tablename__ = "user_account"

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str] = mapped_column(sa.String(255))
    full_name: Mapped[str] = mapped_column(sa.String(200))
    is_platform_admin: Mapped[bool] = mapped_column(default=False)
    is_disabled: Mapped[bool] = mapped_column(default=False, server_default=sa.false())
    password_changed_at: Mapped[datetime | None]
    session_version: Mapped[int] = mapped_column(default=1)
    last_login_at: Mapped[datetime | None]
    anonymized_at: Mapped[datetime | None]

    @property
    def is_active(self) -> bool:  # Flask-Login
        return not self.is_disabled and self.anonymized_at is None


class AuthToken(UUIDPk, db.Model):
    __tablename__ = "auth_token"
    __table_args__ = (check_in("purpose", TOKEN_PURPOSES),)

    user_id: Mapped[uuid.UUID] = mapped_column(sa.ForeignKey("user_account.id"), index=True)
    purpose: Mapped[str] = mapped_column(sa.String(30))
    token_hash: Mapped[str] = mapped_column(sa.String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]


class LoginAttempt(db.Model):
    __tablename__ = "login_attempt"
    __table_args__ = (
        sa.Index("ix_login_attempt_email_created_at", "email", "created_at"),
        sa.Index("ix_login_attempt_ip_created_at", "ip", "created_at"),
    )

    id: Mapped[int] = mapped_column(sa.BigInteger, sa.Identity(), primary_key=True)
    email: Mapped[str] = mapped_column(CITEXT)
    ip: Mapped[str | None] = mapped_column(sa.String(64))
    success: Mapped[bool]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
