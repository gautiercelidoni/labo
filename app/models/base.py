"""Base déclarative, conventions de nommage et mixins communs."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable

import sqlalchemy as sa
from sqlalchemy.orm import DeclarativeBase, Mapped, declared_attr, mapped_column, registry

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    registry = registry(
        metadata=sa.MetaData(naming_convention=NAMING_CONVENTION),
        type_annotation_map={datetime: sa.DateTime(timezone=True)},
    )


class UUIDPk:
    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(
        default=utcnow, server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=utcnow, onupdate=utcnow, server_default=sa.func.now(), nullable=False
    )


class TenantFiltered:
    """Marqueur : toute requête ORM sur ces modèles est filtrée par le tenant courant.

    Les classes concrètes redéfinissent `tenant_id` ; cette déclaration sert au critère
    `with_loader_criteria`, évalué une première fois sur la classe du mixin.
    """

    tenant_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, nullable=True)


class TenantScoped(TenantFiltered):
    """Donnée appartenant à un laboratoire : tenant_id obligatoire et vérifié au flush."""

    @declared_attr
    def tenant_id(cls) -> Mapped[uuid.UUID]:  # noqa: N805
        return mapped_column(
            sa.Uuid, sa.ForeignKey("laboratory.id", ondelete="RESTRICT"), nullable=False, index=True
        )


def tenant_table_args(*extra: object) -> tuple:
    """Ajoute la contrainte unique (tenant_id, id) nécessaire aux clés étrangères composites."""
    return (sa.UniqueConstraint("tenant_id", "id"), *extra)


def tenant_fk(column: str, target_table: str, ondelete: str = "RESTRICT") -> sa.ForeignKeyConstraint:
    """Clé étrangère composite (tenant_id, colonne) -> (tenant_id, id) de la table cible.

    Empêche au niveau PostgreSQL toute référence vers une ligne d'un autre laboratoire.
    """
    return sa.ForeignKeyConstraint(
        ["tenant_id", column],
        [f"{target_table}.tenant_id", f"{target_table}.id"],
        ondelete=ondelete,
    )


def check_in(column: str, values: Iterable[str], name: str | None = None) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IN ({quoted})", name=name or column)


def check_in_nullable(column: str, values: Iterable[str], name: str | None = None) -> sa.CheckConstraint:
    quoted = ", ".join(f"'{v}'" for v in values)
    return sa.CheckConstraint(f"{column} IS NULL OR {column} IN ({quoted})", name=name or column)
