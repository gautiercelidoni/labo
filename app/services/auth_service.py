"""Authentification, sessions, mots de passe et création de laboratoire."""
from __future__ import annotations

import re
import unicodedata
import uuid
from datetime import timedelta

import sqlalchemy as sa
from flask import current_app, session
from flask_login import login_user, logout_user

from app.extensions import db
from app.models.base import utcnow
from app.models.billing import Subscription
from app.models.tenant import Laboratory, Membership
from app.models.user import AuthToken, User
from app.security import rate_limit
from app.security.passwords import hash_password, needs_rehash, password_problems, verify_password
from app.security.tenancy import cross_tenant_read, tenant_context
from app.security.tokens import hash_token, new_token
from app.services import audit_service
from app.services.email_service import EmailError, external_url, send_email


class AuthError(ValueError):
    pass


GENERIC_LOGIN_ERROR = "Email ou mot de passe incorrect."
BLOCKED_LOGIN_ERROR = (
    "Trop de tentatives de connexion. Réessayez dans quelques minutes ou réinitialisez votre mot de passe."
)


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def find_user_by_email(email: str) -> User | None:
    return db.session.scalar(sa.select(User).where(User.email == normalize_email(email)))


def authenticate(email: str, password: str, ip: str | None) -> User:
    email = normalize_email(email)
    if rate_limit.is_blocked(email, ip):
        rate_limit.record_attempt(email, ip, False)
        audit_service.record("auth.login_failed", object_type="user", reason="bloqué (trop de tentatives)",
                             after={"email": email})
        db.session.commit()
        raise AuthError(BLOCKED_LOGIN_ERROR)
    user = find_user_by_email(email)
    ok = verify_password(user.password_hash if user else None, password)
    if not ok or user is None or not user.is_active:
        rate_limit.record_attempt(email, ip, False)
        audit_service.record(
            "auth.login_failed",
            object_type="user",
            object_id=user.id if user else None,
            user_id=user.id if user else None,
            after={"email": email},
        )
        db.session.commit()
        raise AuthError(GENERIC_LOGIN_ERROR)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    rate_limit.record_attempt(email, ip, True)
    user.last_login_at = utcnow()
    return user


def start_session(user: User, remember: bool = False) -> None:
    # Nouvelle session à la connexion (protection contre la fixation de session).
    session.clear()
    login_user(user, remember=remember)
    session["sv"] = user.session_version
    session.permanent = True


def end_session() -> None:
    logout_user()
    session.clear()


def memberships_for_user(user_id: uuid.UUID) -> list[Membership]:
    """Appartenances actives de l'utilisateur dans tous ses laboratoires (sélecteur)."""
    stmt = (
        sa.select(Membership)
        .join(Laboratory, Laboratory.id == Membership.tenant_id)
        .where(Membership.user_id == user_id, Membership.is_active.is_(True))
        .order_by(Laboratory.name)
        .execution_options(**cross_tenant_read())
    )
    return list(db.session.scalars(stmt).all())


def activate_lab(user: User, lab_id: uuid.UUID, *, audit: bool = True) -> Membership | None:
    with tenant_context(lab_id):
        membership = db.session.scalar(
            sa.select(Membership).where(Membership.user_id == user.id, Membership.is_active.is_(True))
        )
        if membership is None:
            return None
        previous = session.get("active_lab_id")
        session["active_lab_id"] = str(lab_id)
        if audit:
            audit_service.record(
                "lab.switch",
                object_type="laboratory",
                object_id=lab_id,
                tenant_id=lab_id,
                user_id=user.id,
                before={"laboratory_id": previous} if previous else None,
                after={"laboratory_id": str(lab_id)},
            )
        return membership


def login_audit(user: User, lab_id: uuid.UUID | None) -> None:
    audit_service.record("auth.login", object_type="user", object_id=user.id, user_id=user.id, tenant_id=lab_id)


def check_password_policy(password: str) -> None:
    problems = password_problems(password, current_app.config["PASSWORD_MIN_LENGTH"])
    if problems:
        raise AuthError(" ".join(problems))


def _set_password(user: User, password: str) -> None:
    check_password_policy(password)
    user.password_hash = hash_password(password)
    user.password_changed_at = utcnow()
    # Incrémenter la version invalide toutes les sessions existantes.
    user.session_version = (user.session_version or 1) + 1


def change_password(user: User, current_password: str, new_password: str) -> None:
    if not verify_password(user.password_hash, current_password):
        raise AuthError("Le mot de passe actuel est incorrect.")
    _set_password(user, new_password)
    audit_service.record("auth.password_changed", object_type="user", object_id=user.id, user_id=user.id)
    db.session.commit()
    # La session courante reste valide, toutes les autres sont invalidées.
    session["sv"] = user.session_version


def request_password_reset(email: str) -> None:
    user = find_user_by_email(email)
    if user is None or not user.is_active:
        return  # réponse identique : pas d'énumération des comptes
    token, token_hash = new_token()
    ttl = current_app.config["PASSWORD_RESET_TTL_MINUTES"]
    db.session.add(
        AuthToken(user_id=user.id, purpose="reset_password", token_hash=token_hash,
                  expires_at=utcnow() + timedelta(minutes=ttl))
    )
    audit_service.record("auth.password_reset_requested", object_type="user", object_id=user.id, user_id=user.id)
    db.session.commit()
    try:
        send_email(
            user.email,
            "Réinitialisation de votre mot de passe",
            "password_reset",
            user=user,
            link=external_url(f"/reinitialiser/{token}"),
            ttl=ttl,
        )
    except EmailError:
        pass


def get_valid_token(token: str, purpose: str) -> AuthToken | None:
    record = db.session.scalar(sa.select(AuthToken).where(AuthToken.token_hash == hash_token(token)))
    if record is None or record.purpose != purpose or record.used_at is not None or record.expires_at <= utcnow():
        return None
    return record


def reset_password(token: str, new_password: str) -> User:
    record = get_valid_token(token, "reset_password")
    if record is None:
        raise AuthError("Ce lien est invalide, expiré ou a déjà été utilisé.")
    user = db.session.scalar(sa.select(User).where(User.id == record.user_id))
    if user is None or not user.is_active:
        raise AuthError("Ce lien est invalide, expiré ou a déjà été utilisé.")
    _set_password(user, new_password)
    record.used_at = utcnow()
    # Les autres jetons en cours pour cet utilisateur sont neutralisés.
    db.session.execute(
        sa.update(AuthToken)
        .where(AuthToken.user_id == user.id, AuthToken.used_at.is_(None), AuthToken.id != record.id)
        .values(used_at=utcnow())
    )
    audit_service.record("auth.password_reset", object_type="user", object_id=user.id, user_id=user.id)
    db.session.commit()
    return user


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:60] or "labo"


def unique_slug(name: str) -> str:
    base = slugify(name)
    slug, n = base, 2
    while db.session.scalar(sa.select(sa.func.count()).where(Laboratory.slug == slug)):
        slug = f"{base}-{n}"
        n += 1
    return slug


def create_laboratory(
    name: str,
    admin_email: str,
    admin_full_name: str,
    admin_password: str | None,
    *,
    timezone_name: str = "Europe/Paris",
    trial_days: int | None = None,
) -> tuple[Laboratory, User]:
    """Crée un laboratoire, son premier administrateur et un abonnement d'essai."""
    admin_email = normalize_email(admin_email)
    user = find_user_by_email(admin_email)
    if user is None:
        if admin_password is None:
            raise AuthError("Mot de passe requis pour un nouvel utilisateur.")
        check_password_policy(admin_password)
        user = User(email=admin_email, full_name=admin_full_name.strip(), password_hash=hash_password(admin_password),
                    password_changed_at=utcnow())
        db.session.add(user)
        db.session.flush()
    lab = Laboratory(
        name=name.strip(),
        slug=unique_slug(name),
        timezone=timezone_name,
        max_active_users=current_app.config["DEFAULT_MAX_ACTIVE_USERS"],
        status="active",
    )
    db.session.add(lab)
    db.session.flush()
    days = trial_days if trial_days is not None else current_app.config["TRIAL_DAYS"]
    with tenant_context(lab.id):
        db.session.add(Membership(user_id=user.id, role="admin", is_active=True))
        db.session.add(Subscription(status="trialing", trial_ends_at=utcnow() + timedelta(days=days)))
        audit_service.record("lab.created", lab, tenant_id=lab.id, user_id=user.id,
                             after={"name": lab.name, "admin": admin_email})
        db.session.flush()
    return lab, user
