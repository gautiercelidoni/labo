"""Isolation multi-tenant centralisée.

Trois mécanismes complémentaires :
- un contexte de tenant (ContextVar) défini par requête ou explicitement par les commandes CLI ;
- un filtre ORM automatique ajouté à chaque SELECT / UPDATE / DELETE ORM ;
- une vérification au flush de chaque objet tenant-scoped créé, modifié ou supprimé.

Aucune requête sur un modèle de laboratoire n'est possible sans contexte : l'absence de
contexte lève une exception (comportement « fermé par défaut »).
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterator

import flask_sqlalchemy.session
from flask import has_request_context, request
from sqlalchemy import event, inspect
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria

from app.models.base import TenantFiltered, TenantScoped


class TenantError(RuntimeError):
    """Violation de l'isolation entre laboratoires."""


class NoTenantContext(TenantError):
    pass


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID | None
    system: bool = False


_current: ContextVar[TenantContext | None] = ContextVar("tenant_context", default=None)

# Seules routes autorisées à ouvrir un contexte système (événements globaux, sans labo).
SYSTEM_CONTEXT_ENDPOINTS = frozenset({"billing.stripe_webhook"})


def get_context() -> TenantContext | None:
    return _current.get()


def current_tenant_id() -> uuid.UUID:
    ctx = _current.get()
    if ctx is None or ctx.tenant_id is None:
        raise NoTenantContext("Aucun laboratoire actif dans le contexte courant.")
    return ctx.tenant_id


def current_tenant_id_or_none() -> uuid.UUID | None:
    ctx = _current.get()
    return ctx.tenant_id if ctx else None


def set_tenant(tenant_id: uuid.UUID):
    """Définit le tenant courant et renvoie le jeton de réinitialisation (usage : requêtes)."""
    return _current.set(TenantContext(tenant_id=tenant_id))


def reset_tenant(token) -> None:
    _current.reset(token)


@contextmanager
def tenant_context(tenant_id: uuid.UUID) -> Iterator[None]:
    token = _current.set(TenantContext(tenant_id=tenant_id))
    try:
        yield
    finally:
        _current.reset(token)


@contextmanager
def system_context() -> Iterator[None]:
    """Contexte sans filtre, réservé aux tâches globales (cron multi-labos, webhooks Stripe)."""
    if has_request_context() and request.endpoint not in SYSTEM_CONTEXT_ENDPOINTS:
        raise TenantError("Le contexte système est interdit dans les routes.")
    token = _current.set(TenantContext(tenant_id=None, system=True))
    try:
        yield
    finally:
        _current.reset(token)


# Option d'exécution permettant une lecture inter-labos ciblée (liste des appartenances
# d'un utilisateur au moment de la connexion). Son usage est limité par un test statique.
CROSS_TENANT_OPTION = "labq_cross_tenant_read"


def cross_tenant_read() -> dict:
    return {CROSS_TENANT_OPTION: True}


class TenantSession(flask_sqlalchemy.session.Session):
    """Session Flask-SQLAlchemy portant les écouteurs d'isolation.

    Respecte aussi un `bind` explicite (tests exécutés dans une transaction englobante).
    """

    def get_bind(self, mapper=None, clause=None, bind=None, **kwargs):
        if bind is None and self.bind is not None:
            return self.bind
        return super().get_bind(mapper=mapper, clause=clause, bind=bind, **kwargs)


def _involves_tenant_models(state: ORMExecuteState) -> bool:
    for mapper in state.all_mappers:
        if issubclass(mapper.class_, TenantFiltered):
            return True
    return False


@event.listens_for(TenantSession, "do_orm_execute")
def _apply_tenant_filter(state: ORMExecuteState) -> None:
    if not (state.is_select or state.is_update or state.is_delete):
        return
    if state.is_column_load or state.is_relationship_load:
        # Les critères posés sur la requête d'origine se propagent aux chargements dérivés.
        return
    ctx = _current.get()
    if ctx is not None and ctx.system:
        return
    if state.execution_options.get(CROSS_TENANT_OPTION):
        return
    if ctx is None or ctx.tenant_id is None:
        if _involves_tenant_models(state):
            raise NoTenantContext("Requête sur une donnée de laboratoire sans laboratoire actif.")
        return
    tenant_id = ctx.tenant_id
    state.statement = state.statement.options(
        with_loader_criteria(
            TenantFiltered,
            lambda cls: cls.tenant_id == tenant_id,
            include_aliases=True,
        )
    )


@event.listens_for(TenantSession, "before_flush")
def _check_tenant_on_flush(session: Session, flush_context, instances) -> None:
    ctx = _current.get()
    for obj in list(session.new):
        if not isinstance(obj, TenantScoped):
            continue
        if ctx is None:
            raise NoTenantContext(f"Création de {type(obj).__name__} sans laboratoire actif.")
        if ctx.system:
            if obj.tenant_id is None:
                raise TenantError(f"{type(obj).__name__} créé en contexte système sans tenant_id.")
            continue
        if obj.tenant_id is None:
            obj.tenant_id = ctx.tenant_id
        elif obj.tenant_id != ctx.tenant_id:
            raise TenantError(f"{type(obj).__name__} rattaché à un autre laboratoire.")
    for obj in list(session.dirty) + list(session.deleted):
        if not isinstance(obj, TenantScoped):
            continue
        if ctx is None:
            raise NoTenantContext(f"Modification de {type(obj).__name__} sans laboratoire actif.")
        if inspect(obj).attrs.tenant_id.history.has_changes():
            raise TenantError(f"Le laboratoire de {type(obj).__name__} ne peut pas être changé.")
        if ctx.system:
            continue
        if obj.tenant_id != ctx.tenant_id:
            raise TenantError(f"Modification de {type(obj).__name__} d'un autre laboratoire.")
