"""Parc d'équipements, plans de métrologie / maintenance et réalisations."""
from __future__ import annotations

import uuid
from datetime import date, datetime

import sqlalchemy as sa
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

EQUIPMENT_STATUSES = ("in_service", "out_of_service", "maintenance", "suspended", "retired")
EQUIPMENT_STATUS_LABELS = {
    "in_service": "En service",
    "out_of_service": "Hors service",
    "maintenance": "En maintenance",
    "suspended": "Suspendu",
    "retired": "Réformé",
}
CRITICALITIES = ("low", "medium", "high")
CRITICALITY_LABELS = {"low": "Faible", "medium": "Moyenne", "high": "Élevée"}
EVENT_TYPES = (
    "calibration",
    "verification",
    "preventive",
    "corrective",
    "intermediate_check",
    "qualification",
)
EVENT_TYPE_LABELS = {
    "calibration": "Étalonnage",
    "verification": "Vérification",
    "preventive": "Maintenance préventive",
    "corrective": "Maintenance corrective",
    "intermediate_check": "Contrôle intermédiaire",
    "qualification": "Qualification",
}
PERIOD_UNITS = ("day", "week", "month", "year")
PERIOD_UNIT_LABELS = {"day": "jour(s)", "week": "semaine(s)", "month": "mois", "year": "an(s)"}
OUTCOMES = ("conform", "non_conform")
OUTCOME_LABELS = {"conform": "Conforme", "non_conform": "Non conforme"}


class EquipmentCategory(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "equipment_category"
    __table_args__ = tenant_table_args(sa.UniqueConstraint("tenant_id", "name"))

    name: Mapped[str] = mapped_column(sa.String(120))


class Equipment(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "equipment"
    __table_args__ = tenant_table_args(
        sa.UniqueConstraint("tenant_id", "internal_id"),
        check_in("status", EQUIPMENT_STATUSES),
        check_in("criticality", CRITICALITIES),
        tenant_fk("category_id", "equipment_category"),
    )

    name: Mapped[str] = mapped_column(sa.String(200))
    category_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    manufacturer: Mapped[str | None] = mapped_column(sa.String(120))
    model: Mapped[str | None] = mapped_column(sa.String(120))
    serial_number: Mapped[str | None] = mapped_column(sa.String(120))
    internal_id: Mapped[str] = mapped_column(sa.String(60))
    location: Mapped[str | None] = mapped_column(sa.String(120))
    commissioned_on: Mapped[date | None]
    status: Mapped[str] = mapped_column(sa.String(20), default="in_service")
    criticality: Mapped[str] = mapped_column(sa.String(10), default="medium")
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    notes: Mapped[str | None] = mapped_column(sa.Text)
    archived_at: Mapped[datetime | None]
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    category = relationship(
        "EquipmentCategory",
        primaryjoin="EquipmentCategory.id == foreign(Equipment.category_id)",
        viewonly=True,
    )
    responsible = relationship("User", foreign_keys=[responsible_id], viewonly=True)

    @property
    def status_label(self) -> str:
        return EQUIPMENT_STATUS_LABELS[self.status]

    @property
    def criticality_label(self) -> str:
        return CRITICALITY_LABELS[self.criticality]


class MaintenancePlan(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "maintenance_plan"
    __table_args__ = tenant_table_args(
        sa.Index(
            "ix_maintenance_plan_due",
            "tenant_id",
            "next_due_on",
            postgresql_where=sa.text("is_active"),
        ),
        check_in("event_type", EVENT_TYPES),
        check_in("period_unit", PERIOD_UNITS),
        sa.CheckConstraint("period_value > 0", name="period_positive"),
        tenant_fk("equipment_id", "equipment"),
    )

    equipment_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    event_type: Mapped[str] = mapped_column(sa.String(30))
    period_value: Mapped[int]
    period_unit: Mapped[str] = mapped_column(sa.String(10))
    provider: Mapped[str | None] = mapped_column(sa.String(200))
    responsible_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))
    last_done_on: Mapped[date | None]
    next_due_on: Mapped[date | None]
    comment: Mapped[str | None] = mapped_column(sa.Text)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    equipment = relationship(
        "Equipment", primaryjoin="Equipment.id == foreign(MaintenancePlan.equipment_id)", viewonly=True
    )
    responsible = relationship("User", foreign_keys=[responsible_id], viewonly=True)

    @property
    def event_type_label(self) -> str:
        return EVENT_TYPE_LABELS[self.event_type]

    @property
    def period_label(self) -> str:
        return f"{self.period_value} {PERIOD_UNIT_LABELS[self.period_unit]}"


class MaintenanceEvent(UUIDPk, Timestamps, TenantScoped, db.Model):
    __tablename__ = "maintenance_event"
    __table_args__ = tenant_table_args(
        sa.Index("ix_maintenance_event_equipment", "tenant_id", "equipment_id", "performed_on"),
        check_in("event_type", EVENT_TYPES),
        check_in_nullable("outcome", OUTCOMES),
        tenant_fk("plan_id", "maintenance_plan"),
        tenant_fk("equipment_id", "equipment"),
    )

    # Nullable : maintenance corrective non planifiée.
    plan_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid)
    equipment_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid)
    event_type: Mapped[str] = mapped_column(sa.String(30))
    performed_on: Mapped[date]
    outcome: Mapped[str | None] = mapped_column(sa.String(20))
    provider: Mapped[str | None] = mapped_column(sa.String(200))
    comment: Mapped[str | None] = mapped_column(sa.Text)
    performed_by_id: Mapped[uuid.UUID | None] = mapped_column(sa.ForeignKey("user_account.id"))

    equipment = relationship(
        "Equipment", primaryjoin="Equipment.id == foreign(MaintenanceEvent.equipment_id)", viewonly=True
    )
    performed_by = relationship("User", foreign_keys=[performed_by_id], viewonly=True)

    @property
    def event_type_label(self) -> str:
        return EVENT_TYPE_LABELS[self.event_type]

    @property
    def outcome_label(self) -> str:
        return OUTCOME_LABELS.get(self.outcome or "", "—")
