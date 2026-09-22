"""Import de tous les modèles pour qu'ils soient enregistrés dans la métadonnée."""
from app.models.actions import CorrectiveAction
from app.models.audit import AuditEvent
from app.models.billing import StripeEvent, Subscription
from app.models.ciq import (
    CIQParameter,
    CIQResult,
    CIQRuleConfig,
    CIQRun,
    ControlLevel,
    ControlLimitSet,
    ControlLot,
)
from app.models.equipment import Equipment, EquipmentCategory, MaintenanceEvent, MaintenancePlan
from app.models.files import Attachment
from app.models.non_conformities import NonConformity, NonConformityStatusChange
from app.models.notifications import Notification
from app.models.tenant import Invitation, Laboratory, Membership, Team
from app.models.transmissions import (
    Transmission,
    TransmissionComment,
    TransmissionReadReceipt,
    TransmissionRecipient,
)
from app.models.user import AuthToken, LoginAttempt, User

__all__ = [
    "Attachment",
    "AuditEvent",
    "AuthToken",
    "CIQParameter",
    "CIQResult",
    "CIQRuleConfig",
    "CIQRun",
    "ControlLevel",
    "ControlLimitSet",
    "ControlLot",
    "CorrectiveAction",
    "Equipment",
    "EquipmentCategory",
    "Invitation",
    "Laboratory",
    "LoginAttempt",
    "MaintenanceEvent",
    "MaintenancePlan",
    "Membership",
    "NonConformity",
    "NonConformityStatusChange",
    "Notification",
    "StripeEvent",
    "Subscription",
    "Team",
    "Transmission",
    "TransmissionComment",
    "TransmissionReadReceipt",
    "TransmissionRecipient",
    "User",
]
