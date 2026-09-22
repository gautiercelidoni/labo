"""Accès aux données tenant-scoped.

Le filtrage par laboratoire est assuré par l'écouteur ORM (app.security.tenancy) : ce dépôt
n'ajoute jamais de filtre manuel, il garantit simplement que tout accès par identifiant passe
par une requête filtrée (et non par `session.get`, qui peut court-circuiter les critères via
la carte d'identité).
"""
from __future__ import annotations

import uuid
from typing import Any, Generic, Sequence, TypeVar

import sqlalchemy as sa
from flask import abort

from app.extensions import db
from app.models.base import TenantFiltered

T = TypeVar("T")


def parse_uuid(value: Any) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


class TenantRepository(Generic[T]):
    def __init__(self, model: type[T]):
        if not issubclass(model, TenantFiltered):
            raise TypeError(f"{model.__name__} n'est pas un modèle de laboratoire.")
        self.model = model

    def select(self) -> sa.Select:
        return sa.select(self.model)

    def get(self, object_id: Any) -> T | None:
        oid = parse_uuid(object_id)
        if oid is None:
            return None
        return db.session.execute(self.select().where(self.model.id == oid)).scalar_one_or_none()

    def get_or_404(self, object_id: Any) -> T:
        obj = self.get(object_id)
        if obj is None:
            # 404 et non 403 : ne pas révéler l'existence d'une ressource d'un autre labo.
            abort(404)
        return obj

    def all(self, *criteria, order_by=None) -> Sequence[T]:
        stmt = self.select().where(*criteria)
        if order_by is not None:
            stmt = stmt.order_by(*order_by) if isinstance(order_by, (list, tuple)) else stmt.order_by(order_by)
        return db.session.scalars(stmt).all()

    def first(self, *criteria) -> T | None:
        return db.session.scalars(self.select().where(*criteria).limit(1)).first()

    def count(self, *criteria) -> int:
        return db.session.scalar(sa.select(sa.func.count()).select_from(self.model).where(*criteria))


def repo(model: type[T]) -> TenantRepository[T]:
    return TenantRepository(model)


class Page:
    def __init__(self, items: Sequence, total: int, page: int, per_page: int):
        self.items = items
        self.total = total
        self.page = page
        self.per_page = per_page

    @property
    def pages(self) -> int:
        return max(1, -(-self.total // self.per_page))

    @property
    def has_prev(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages


def paginate(stmt: sa.Select, page: int, per_page: int, scalars: bool = True) -> Page:
    """Pagination côté serveur : une requête de comptage + une requête limitée."""
    page = max(1, page)
    total = db.session.scalar(sa.select(sa.func.count()).select_from(stmt.order_by(None).subquery()))
    limited = stmt.limit(per_page).offset((page - 1) * per_page)
    items = db.session.scalars(limited).all() if scalars else db.session.execute(limited).all()
    return Page(items, total or 0, page, per_page)
