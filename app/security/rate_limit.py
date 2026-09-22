"""Limitation des tentatives de connexion, sans Redis : compteur en base partagé par les workers.

Règle : au-delà de LOGIN_MAX_FAILURES échecs pour un email (ou LOGIN_MAX_FAILURES_PER_IP pour
une IP) dans la fenêtre glissante LOGIN_WINDOW_MINUTES, et depuis la dernière connexion réussie,
toute nouvelle tentative est refusée jusqu'à ce que les échecs sortent de la fenêtre.
Le délai est donc progressif : chaque nouvel échec prolonge le blocage.
"""
from __future__ import annotations

from datetime import timedelta

import sqlalchemy as sa
from flask import current_app

from app.extensions import db
from app.models.base import utcnow
from app.models.user import LoginAttempt


def _window_start():
    return utcnow() - timedelta(minutes=current_app.config["LOGIN_WINDOW_MINUTES"])


def _failures_for_email(email: str) -> int:
    start = _window_start()
    last_success = db.session.scalar(
        sa.select(sa.func.max(LoginAttempt.created_at)).where(
            LoginAttempt.email == email, LoginAttempt.success.is_(True), LoginAttempt.created_at >= start
        )
    )
    since = max(start, last_success) if last_success else start
    return db.session.scalar(
        sa.select(sa.func.count()).where(
            LoginAttempt.email == email,
            LoginAttempt.success.is_(False),
            LoginAttempt.created_at > since,
        )
    )


def _failures_for_ip(ip: str | None) -> int:
    if not ip:
        return 0
    return db.session.scalar(
        sa.select(sa.func.count()).where(
            LoginAttempt.ip == ip, LoginAttempt.success.is_(False), LoginAttempt.created_at > _window_start()
        )
    )


def is_blocked(email: str, ip: str | None) -> bool:
    cfg = current_app.config
    return (
        _failures_for_email(email) >= cfg["LOGIN_MAX_FAILURES"]
        or _failures_for_ip(ip) >= cfg["LOGIN_MAX_FAILURES_PER_IP"]
    )


def record_attempt(email: str, ip: str | None, success: bool) -> None:
    db.session.add(LoginAttempt(email=email, ip=ip, success=success))


def purge_old_attempts(days: int = 30) -> int:
    result = db.session.execute(
        sa.delete(LoginAttempt).where(LoginAttempt.created_at < utcnow() - timedelta(days=days))
    )
    return result.rowcount or 0
