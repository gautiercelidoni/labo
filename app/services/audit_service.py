"""Journal d'audit : écriture dans la même transaction que la modification métier."""
from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable

import sqlalchemy as sa
from flask import g, has_request_context, request
from flask_login import current_user

from app.extensions import db
from app.models.audit import AuditEvent
from app.security.tenancy import current_tenant_id_or_none

# Champs jamais recopiés dans l'audit.
EXCLUDED_FIELDS = frozenset({"password_hash", "token_hash", "payload"})
EXCLUDED_SUFFIXES = ("_secret", "_hash", "_token")
TECHNICAL_FIELDS = frozenset({"created_at", "updated_at"})


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _json_value(v) for k, v in value.items()}
    return str(value)


def _is_excluded(name: str) -> bool:
    return name in EXCLUDED_FIELDS or name.endswith(EXCLUDED_SUFFIXES)


def snapshot(obj: Any, fields: Iterable[str] | None = None) -> dict:
    """Instantané JSON des colonnes d'un objet, sans secrets."""
    if obj is None:
        return {}
    mapper = sa.inspect(type(obj))
    names = fields or [c.key for c in mapper.column_attrs]
    return {
        name: _json_value(getattr(obj, name))
        for name in names
        if not _is_excluded(name) and name not in TECHNICAL_FIELDS
    }


def diff(before: dict, after: dict) -> tuple[dict, dict]:
    """Ne conserve que les champs modifiés."""
    keys = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    return {k: before.get(k) for k in sorted(keys)}, {k: after.get(k) for k in sorted(keys)}


def client_ip() -> str | None:
    if not has_request_context():
        return None
    # Derrière le reverse proxy, ProxyFix renseigne remote_addr avec l'IP cliente.
    return request.remote_addr


def record(
    action: str,
    obj: Any = None,
    *,
    object_type: str | None = None,
    object_id: Any = None,
    before: dict | None = None,
    after: dict | None = None,
    reason: str | None = None,
    tenant_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
) -> AuditEvent:
    """Ajoute un événement d'audit à la session courante (commit par l'appelant)."""
    if obj is not None:
        object_type = object_type or getattr(obj, "__tablename__", type(obj).__name__)
        object_id = object_id if object_id is not None else getattr(obj, "id", None)
        if tenant_id is None:
            tenant_id = getattr(obj, "tenant_id", None)
    if tenant_id is None:
        tenant_id = current_tenant_id_or_none()
    if user_id is None and has_request_context() and current_user and current_user.is_authenticated:
        user_id = current_user.id
    event = AuditEvent(
        tenant_id=tenant_id,
        user_id=user_id,
        action=action,
        object_type=object_type,
        object_id=str(object_id) if object_id is not None else None,
        ip=client_ip(),
        before=_json_value(before) if before else None,
        after=_json_value(after) if after else None,
        reason=reason,
        request_id=getattr(g, "request_id", None) if has_request_context() else None,
    )
    db.session.add(event)
    return event


def record_change(action: str, obj: Any, before: dict, reason: str | None = None) -> AuditEvent | None:
    """Enregistre une modification en ne gardant que les champs changés."""
    b, a = diff(before, snapshot(obj))
    if not b and not a:
        return None
    return record(action, obj, before=b, after=a, reason=reason)


ACTION_LABELS = {
    "auth.login": "Connexion",
    "auth.login_failed": "Échec de connexion",
    "auth.logout": "Déconnexion",
    "auth.password_changed": "Changement de mot de passe",
    "auth.password_reset": "Réinitialisation du mot de passe",
    "auth.password_reset_requested": "Demande de réinitialisation",
    "lab.switch": "Changement de laboratoire actif",
    "lab.created": "Création du laboratoire",
    "lab.updated": "Modification du laboratoire",
    "membership.created": "Ajout d'un membre",
    "membership.updated": "Modification de droits",
    "membership.deactivated": "Désactivation d'un membre",
    "membership.reactivated": "Réactivation d'un membre",
    "invitation.created": "Invitation envoyée",
    "invitation.revoked": "Invitation révoquée",
    "invitation.accepted": "Invitation acceptée",
    "team.created": "Création d'une équipe",
    "team.updated": "Modification d'une équipe",
    "file.uploaded": "Ajout d'une pièce jointe",
    "file.downloaded": "Téléchargement d'une pièce jointe",
    "file.archived": "Archivage d'une pièce jointe",
    "export.csv": "Export CSV",
    "export.pdf": "Export PDF",
    "export.lab_data": "Export des données du laboratoire",
}
