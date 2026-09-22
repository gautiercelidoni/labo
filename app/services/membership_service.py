"""Gestion des membres d'un laboratoire : invitations, rôles, équipes, activation."""
from __future__ import annotations

import uuid
from datetime import timedelta

import sqlalchemy as sa
from flask import current_app, g
from flask_login import current_user

from app.extensions import db
from app.models.base import utcnow
from app.models.tenant import ROLES, Invitation, Laboratory, Membership, Team
from app.models.user import User
from app.repositories.base import repo
from app.security.passwords import hash_password
from app.security.tenancy import cross_tenant_read, current_tenant_id, tenant_context
from app.security.tokens import hash_token, new_token
from app.services import audit_service
from app.services.auth_service import check_password_policy, find_user_by_email, normalize_email
from app.services.email_service import EmailError, external_url, send_email


class MembershipError(ValueError):
    pass


def current_lab() -> Laboratory:
    return db.session.scalar(sa.select(Laboratory).where(Laboratory.id == current_tenant_id()))


def active_member_count() -> int:
    return repo(Membership).count(Membership.is_active.is_(True))


def ensure_capacity(extra: int = 1) -> None:
    lab = current_lab()
    if active_member_count() + extra > lab.max_active_users:
        raise MembershipError(
            f"Le plafond de {lab.max_active_users} utilisateurs actifs de votre abonnement est atteint."
        )


def list_members() -> list[Membership]:
    stmt = (
        sa.select(Membership)
        .join(User, User.id == Membership.user_id)
        .order_by(Membership.is_active.desc(), User.full_name)
    )
    return list(db.session.scalars(stmt).all())


def _check_team(team_id: uuid.UUID | None) -> None:
    if team_id is not None and repo(Team).get(team_id) is None:
        raise MembershipError("Équipe inconnue.")


def invite(email: str, role: str, team_id: uuid.UUID | None = None) -> tuple[Invitation, str]:
    if role not in ROLES:
        raise MembershipError("Rôle invalide.")
    _check_team(team_id)
    email = normalize_email(email)
    existing_user = find_user_by_email(email)
    if existing_user is not None:
        member = repo(Membership).first(Membership.user_id == existing_user.id)
        if member is not None and member.is_active:
            raise MembershipError("Cette personne est déjà membre du laboratoire.")
    ensure_capacity()
    # Une seule invitation en attente par email : les précédentes sont révoquées.
    for previous in repo(Invitation).all(Invitation.email == email, Invitation.accepted_at.is_(None),
                                         Invitation.revoked_at.is_(None)):
        previous.revoked_at = utcnow()
    token, token_hash = new_token()
    invitation = Invitation(
        email=email,
        role=role,
        team_id=team_id,
        token_hash=token_hash,
        expires_at=utcnow() + timedelta(days=current_app.config["INVITATION_TTL_DAYS"]),
        invited_by_id=current_user.id,
    )
    db.session.add(invitation)
    db.session.flush()
    audit_service.record("invitation.created", invitation, after={"email": email, "role": role})
    db.session.commit()
    lab = current_lab()
    try:
        send_email(
            email,
            f"Invitation à rejoindre {lab.name}",
            "invitation",
            lab=lab,
            inviter=current_user,
            link=external_url(f"/invitation/{token}"),
            days=current_app.config["INVITATION_TTL_DAYS"],
        )
    except EmailError:
        pass
    return invitation, token


def revoke_invitation(invitation: Invitation) -> None:
    if invitation.accepted_at is not None:
        raise MembershipError("Invitation déjà acceptée.")
    invitation.revoked_at = utcnow()
    audit_service.record("invitation.revoked", invitation, after={"email": invitation.email})
    db.session.commit()


def find_invitation(token: str) -> Invitation | None:
    """Recherche par empreinte du jeton, avant tout contexte de laboratoire."""
    inv = db.session.scalar(
        sa.select(Invitation)
        .where(Invitation.token_hash == hash_token(token))
        .execution_options(**cross_tenant_read())
    )
    if inv is None or not inv.is_pending:
        return None
    return inv


def accept_invitation(invitation: Invitation, user: User | None, *, full_name: str | None = None,
                      password: str | None = None) -> User:
    """Crée le compte si besoin puis l'appartenance, dans le laboratoire de l'invitation."""
    if user is None:
        existing = find_user_by_email(invitation.email)
        if existing is not None:
            raise MembershipError("Un compte existe déjà pour cet email : connectez-vous pour accepter.")
        check_password_policy(password or "")
        user = User(email=invitation.email, full_name=(full_name or "").strip() or invitation.email,
                    password_hash=hash_password(password or ""), password_changed_at=utcnow())
        db.session.add(user)
        db.session.flush()
    elif normalize_email(user.email) != invitation.email:
        raise MembershipError("Cette invitation a été envoyée à une autre adresse email.")
    with tenant_context(invitation.tenant_id):
        inv = repo(Invitation).get(invitation.id)
        membership = repo(Membership).first(Membership.user_id == user.id)
        if membership is not None and membership.is_active:
            raise MembershipError("Vous êtes déjà membre de ce laboratoire.")
        ensure_capacity()
        if membership is None:
            membership = Membership(user_id=user.id, role=inv.role, team_id=inv.team_id, is_active=True)
            db.session.add(membership)
        else:
            membership.role, membership.team_id, membership.is_active = inv.role, inv.team_id, True
        inv.accepted_at = utcnow()
        db.session.flush()
        audit_service.record("invitation.accepted", inv, user_id=user.id, after={"email": inv.email})
        audit_service.record("membership.created", membership, user_id=user.id,
                             after=audit_service.snapshot(membership))
        db.session.commit()
    return user


def _admin_count(exclude_id: uuid.UUID | None = None) -> int:
    criteria = [Membership.role == "admin", Membership.is_active.is_(True)]
    if exclude_id is not None:
        criteria.append(Membership.id != exclude_id)
    return repo(Membership).count(*criteria)


def update_membership(membership: Membership, role: str, team_id: uuid.UUID | None) -> None:
    if role not in ROLES:
        raise MembershipError("Rôle invalide.")
    _check_team(team_id)
    if membership.role == "admin" and role != "admin" and _admin_count(exclude_id=membership.id) == 0:
        raise MembershipError("Impossible de retirer le rôle du dernier administrateur du laboratoire.")
    before = audit_service.snapshot(membership)
    membership.role = role
    membership.team_id = team_id
    audit_service.record_change("membership.updated", membership, before)
    db.session.commit()


def set_active(membership: Membership, active: bool) -> None:
    if membership.is_active == active:
        return
    if not active and membership.role == "admin" and _admin_count(exclude_id=membership.id) == 0:
        raise MembershipError("Impossible de désactiver le dernier administrateur du laboratoire.")
    if active:
        ensure_capacity()
    before = audit_service.snapshot(membership)
    membership.is_active = active
    audit_service.record_change("membership.reactivated" if active else "membership.deactivated", membership, before)
    db.session.commit()


def save_team(team: Team | None, name: str, description: str | None) -> Team:
    name = name.strip()
    duplicate = repo(Team).first(Team.name == name)
    if duplicate is not None and (team is None or duplicate.id != team.id):
        raise MembershipError("Une équipe porte déjà ce nom.")
    if team is None:
        team = Team(name=name, description=description)
        db.session.add(team)
        db.session.flush()
        audit_service.record("team.created", team, after=audit_service.snapshot(team))
    else:
        before = audit_service.snapshot(team)
        team.name, team.description = name, description
        audit_service.record_change("team.updated", team, before)
    db.session.commit()
    return team


def list_teams() -> list[Team]:
    return list(repo(Team).all(order_by=Team.name))


def member_users() -> list[User]:
    """Utilisateurs actifs du laboratoire courant (listes de responsables)."""
    stmt = (
        sa.select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.is_active.is_(True))
        .order_by(User.full_name)
    )
    return list(db.session.scalars(stmt).all())


def is_member(user_id: uuid.UUID | None) -> bool:
    if user_id is None:
        return False
    return repo(Membership).count(Membership.user_id == user_id, Membership.is_active.is_(True)) > 0


def manager_users() -> list[User]:
    stmt = (
        sa.select(User)
        .join(Membership, Membership.user_id == User.id)
        .where(Membership.is_active.is_(True), Membership.role.in_(("admin", "quality")))
    )
    return list(db.session.scalars(stmt).all())


def current_membership() -> Membership | None:
    return getattr(g, "membership", None)
