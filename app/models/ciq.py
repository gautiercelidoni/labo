"""Contrôles qualité internes : paramètres, niveaux, lots, limites, séries et résultats."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.extensions import db
from app.models.base import (
    Timestamps,
    TenantScoped,
    UUIDPk,
    check_in,
    check_in_nullable,
    tenant_fk,
    tenant_table_args,
)

MODES = ("westgard", "shewhart")
MODE_LABELS = {"westgard": "Westgard", "shewhart": "Carte de contrôle Shewhart"}
LIMIT_SOURCES = ("supplier", "lab", "computed")
LIMIT_SOURCE_LABELS = {"supplier": "Fournisseur", "lab": "Laboratoire", "computed": "Calculée"}
RUN_STATUSES = ("open", "accepted", "rejected", "justified")
RUN_STATUS_LABELS = {
    "open": "Ouverte",
    "accepted": "Acceptée",
    "rejected": "Rejetée",
    "justified": "Rejet traité",
}
RESULT_STATUSES = ("accepted", "warning", "rejected")
RESULT_STATUS_LABELS = {"accepted": "Accepté", "warning": "Alerte", "rejected": "Rejet"}

NUMERIC = sa.Numeric(18, 6)


class CIQParameter(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "ciq_parameter"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "equipment_id", "name"),
        tenant_fk("equipment_id", "equipment"),
    )

    equipment_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    name: Mapped[str] = mapped_column(sa.String(120))
    unit: Mapped[str | None] = mapped_column(sa.String(40))
    decimals: Mapped[int] = mapped_column(default=2)
    is_active: Mapped[bool] = mapped_column(default=True)

    equipment = relationship(
        "Equipment", primaryjoin="Equipment.id == foreign(CIQParameter.equipment_id)", viewonly=True
    )
    levels = relationship(
        "ControlLevel",
        primaryjoin="CIQParameter.id == foreign(ControlLevel.parameter_id)",
        order_by="ControlLevel.sort_order",
        viewonly=True,
    )
    rule_config = relationship(
        "CIQRuleConfig",
        primaryjoin="CIQParameter.id == foreign(CIQRuleConfig.parameter_id)",
        uselist=False,
        viewonly=True,
    )


class ControlLevel(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "control_level"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "parameter_id", "label"),
        check_in_nullable("mode", MODES),
        tenant_fk("parameter_id", "ciq_parameter"),
    )

    parameter_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    label: Mapped[str] = mapped_column(sa.String(40))
    sort_order: Mapped[int] = mapped_column("order", default=0)
    # Mode propre au niveau ; à défaut, celui de la configuration du paramètre.
    mode: Mapped[str | None] = mapped_column(sa.String(20))
    is_active: Mapped[bool] = mapped_column(default=True)

    parameter = relationship(
        "CIQParameter", primaryjoin="CIQParameter.id == foreign(ControlLevel.parameter_id)", viewonly=True
    )


class ControlLot(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "control_lot"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "level_id", "lot_number"),
        tenant_fk("level_id", "control_level"),
    )

    level_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    manufacturer: Mapped[str | None] = mapped_column(sa.String(120))
    lot_number: Mapped[str] = mapped_column(sa.String(80))
    expires_on: Mapped[date | None]
    in_use_from: Mapped[date | None]
    in_use_to: Mapped[date | None]
    archived_at: Mapped[datetime | None]

    level = relationship(
        "ControlLevel", primaryjoin="ControlLevel.id == foreign(ControlLot.level_id)", viewonly=True
    )


class ControlLimitSet(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "control_limit_set"
    __table_args__ = tenant_table_args(
        sa.Index(
            "uq_control_limit_set_active",
            "tenant_id",
            "lot_id",
            unique=True,
            postgresql_where=sa.text("valid_to IS NULL"),
        ),
        check_in("mode", MODES),
        check_in("source", LIMIT_SOURCES),
        sa.CheckConstraint("sd > 0", name="sd_positive"),
        tenant_fk("lot_id", "control_lot"),
    )

    lot_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    mode: Mapped[str] = mapped_column(sa.String(20))
    mean: Mapped[Decimal] = mapped_column(NUMERIC)
    sd: Mapped[Decimal] = mapped_column(NUMERIC)
    source: Mapped[str] = mapped_column(sa.String(20))
    reference_from: Mapped[datetime | None]
    reference_to: Mapped[datetime | None]
    n_reference: Mapped[int | None]
    valid_from: Mapped[datetime]
    valid_to: Mapped[datetime | None]
    reason: Mapped[str | None] = mapped_column(sa.Text)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    lot = relationship("ControlLot", primaryjoin="ControlLot.id == foreign(ControlLimitSet.lot_id)", viewonly=True)
    created_by = relationship("User", foreign_keys=[created_by_id], viewonly=True)


class CIQRuleConfig(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "ciq_rule_config"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "parameter_id"),
        check_in("mode", MODES),
        tenant_fk("parameter_id", "ciq_parameter"),
    )

    parameter_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    mode: Mapped[str] = mapped_column(sa.String(20), default="westgard")
    # règle -> "off" | "warning" | "reject"
    rules: Mapped[dict] = mapped_column(JSONB, default=dict)
    # gravités exigeant un commentaire : sous-ensemble de ["warning", "reject"]
    comment_required_on: Mapped[list] = mapped_column(JSONB, default=lambda: ["warning", "reject"])
    min_reference_points: Mapped[int] = mapped_column(default=20)
    # Enchaîner les séquences inter-séries malgré un changement de lot (via le z-score).
    chain_lots: Mapped[bool] = mapped_column(default=False)


class CIQRun(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "ciq_run"
    __table_args__ = tenant_table_args(
        sa.Index("ix_ciq_run_parameter_run_at", "tenant_id", "parameter_id", "run_at"),
        sa.Index("ix_ciq_run_status", "tenant_id", "status"),
        check_in("status", RUN_STATUSES),
        tenant_fk("equipment_id", "equipment"),
        tenant_fk("parameter_id", "ciq_parameter"),
    )

    equipment_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    parameter_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    run_at: Mapped[datetime]
    operator_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    status: Mapped[str] = mapped_column(sa.String(20), default="open")
    justification: Mapped[str | None] = mapped_column(sa.Text)
    justified_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    justified_at: Mapped[datetime | None]
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    parameter = relationship(
        "CIQParameter", primaryjoin="CIQParameter.id == foreign(CIQRun.parameter_id)", viewonly=True
    )
    equipment = relationship("Equipment", primaryjoin="Equipment.id == foreign(CIQRun.equipment_id)", viewonly=True)
    operator = relationship("User", foreign_keys=[operator_id], viewonly=True)
    results = relationship(
        "CIQResult",
        primaryjoin="CIQRun.id == foreign(CIQResult.run_id)",
        order_by="CIQResult.created_at",
        viewonly=True,
    )

    @property
    def status_label(self) -> str:
        return RUN_STATUS_LABELS[self.status]


class CIQResult(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "ciq_result"
    __table_args__ = tenant_table_args(
        # Unicité limitée aux résultats non annulés : un résultat annulé peut être ressaisi.
        sa.Index(
            "uq_ciq_result_run_level_valid",
            "tenant_id",
            "run_id",
            "level_id",
            unique=True,
            postgresql_where=sa.text("voided_at IS NULL"),
        ),
        sa.Index("ix_ciq_result_lot_run_at", "tenant_id", "lot_id", "run_at"),
        sa.Index("ix_ciq_result_level_run_at", "tenant_id", "level_id", "run_at"),
        check_in("status", RESULT_STATUSES),
        check_in("mode", MODES),
        tenant_fk("run_id", "ciq_run"),
        tenant_fk("level_id", "control_level"),
        tenant_fk("lot_id", "control_lot"),
        tenant_fk("limit_set_id", "control_limit_set"),
        tenant_fk("parameter_id", "ciq_parameter"),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    level_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    lot_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    limit_set_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    parameter_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, index=True)
    run_at: Mapped[datetime]
    mode: Mapped[str] = mapped_column(sa.String(20))
    value: Mapped[Decimal] = mapped_column(NUMERIC)
    z_score: Mapped[Decimal] = mapped_column(NUMERIC)
    deviation: Mapped[Decimal] = mapped_column(NUMERIC)
    status: Mapped[str] = mapped_column(sa.String(20))
    rules_triggered: Mapped[list] = mapped_column(JSONB, default=list)
    rules_not_evaluated: Mapped[list] = mapped_column(JSONB, default=list)
    comment: Mapped[str | None] = mapped_column(sa.Text)
    entered_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    voided_at: Mapped[datetime | None]
    void_reason: Mapped[str | None] = mapped_column(sa.Text)
    voided_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    run = relationship("CIQRun", primaryjoin="CIQRun.id == foreign(CIQResult.run_id)", viewonly=True)
    level = relationship("ControlLevel", primaryjoin="ControlLevel.id == foreign(CIQResult.level_id)", viewonly=True)
    lot = relationship("ControlLot", primaryjoin="ControlLot.id == foreign(CIQResult.lot_id)", viewonly=True)
    limit_set = relationship(
        "ControlLimitSet", primaryjoin="ControlLimitSet.id == foreign(CIQResult.limit_set_id)", viewonly=True
    )
    entered_by = relationship("User", foreign_keys=[entered_by_id], viewonly=True)

    @property
    def status_label(self) -> str:
        return RESULT_STATUS_LABELS[self.status]
