"""Paramètres du laboratoire, logo, catégories et export administratif des données."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime

import sqlalchemy as sa
from werkzeug.datastructures import FileStorage as UploadedFile

from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.audit import AuditEvent
from app.models.base import utcnow
from app.models.ciq import CIQParameter, CIQResult, ControlLevel, ControlLimitSet, ControlLot
from app.models.equipment import Equipment, EquipmentCategory, MaintenanceEvent, MaintenancePlan
from app.models.files import Attachment
from app.models.non_conformities import NonConformity
from app.models.tenant import Laboratory, Membership
from app.models.transmissions import Transmission
from app.models.user import User
from app.repositories.base import repo
from app.security.tenancy import current_tenant_id
from app.services import audit_service
from app.services.export_csv import build_csv
from app.services.file_storage import IMAGE_TYPES, UploadError, get_storage, store_upload


class LabError(ValueError):
    pass


def get_current_lab() -> Laboratory:
    return db.session.scalar(sa.select(Laboratory).where(Laboratory.id == current_tenant_id()))


def update_settings(data: dict, logo: UploadedFile | None) -> Laboratory:
    lab = get_current_lab()
    before = audit_service.snapshot(lab)
    lab.name = data["name"].strip()
    lab.timezone = data["timezone"]
    lab.legal_info = {k: v for k, v in {
        "siret": (data.get("siret") or "").strip(),
        "address": (data.get("address") or "").strip(),
        "accreditation": (data.get("accreditation") or "").strip(),
    }.items() if v}
    lab.retention_days = data["retention_days"]
    lab.notify_by_email = bool(data.get("notify_by_email"))
    if logo is not None and logo.filename:
        attachment = store_upload(logo, "laboratory", lab.id)
        if attachment.content_type not in IMAGE_TYPES:
            raise UploadError("Le logo doit être une image PNG ou JPEG.")
        lab.logo_attachment_id = attachment.id
    audit_service.record_change("lab.updated", lab, before)
    db.session.commit()
    return lab


def logo_bytes(lab: Laboratory) -> tuple[bytes, str] | None:
    if lab.logo_attachment_id is None:
        return None
    attachment = repo(Attachment).get(lab.logo_attachment_id)
    if attachment is None or attachment.content_type not in IMAGE_TYPES:
        return None
    try:
        return get_storage().read(attachment.storage_key), attachment.content_type
    except (OSError, UploadError):
        return None


def list_categories() -> list[EquipmentCategory]:
    return list(repo(EquipmentCategory).all(order_by=EquipmentCategory.name))


def add_category(name: str) -> EquipmentCategory:
    name = name.strip()
    if repo(EquipmentCategory).first(EquipmentCategory.name == name):
        raise LabError("Cette catégorie existe déjà.")
    category = EquipmentCategory(name=name)
    db.session.add(category)
    db.session.flush()
    audit_service.record("category.created", category, after={"name": name})
    db.session.commit()
    return category


def _rows(model, columns):
    for obj in db.session.scalars(sa.select(model)).yield_per(500):
        yield [getattr(obj, c) for c in columns]


EXPORT_TABLES = [
    ("membres.csv", None, None),
    ("categories_equipements.csv", EquipmentCategory, ["id", "name"]),
    ("equipements.csv", Equipment, ["id", "internal_id", "name", "category_id", "manufacturer", "model",
                                    "serial_number", "location", "commissioned_on", "status", "criticality",
                                    "responsible_id", "notes", "archived_at"]),
    ("plans_metrologie.csv", MaintenancePlan, ["id", "equipment_id", "event_type", "period_value", "period_unit",
                                               "provider", "responsible_id", "last_done_on", "next_due_on",
                                               "is_active", "comment"]),
    ("realisations_metrologie.csv", MaintenanceEvent, ["id", "equipment_id", "plan_id", "event_type",
                                                       "performed_on", "outcome", "provider", "comment",
                                                       "performed_by_id"]),
    ("ciq_parametres.csv", CIQParameter, ["id", "equipment_id", "name", "unit", "decimals", "is_active"]),
    ("ciq_niveaux.csv", ControlLevel, ["id", "parameter_id", "label", "sort_order", "mode"]),
    ("ciq_lots.csv", ControlLot, ["id", "level_id", "manufacturer", "lot_number", "expires_on", "in_use_from",
                                  "in_use_to"]),
    ("ciq_limites.csv", ControlLimitSet, ["id", "lot_id", "mode", "mean", "sd", "source", "n_reference",
                                          "valid_from", "valid_to", "reason"]),
    ("ciq_resultats.csv", CIQResult, ["id", "run_id", "parameter_id", "level_id", "lot_id", "limit_set_id",
                                      "run_at", "value", "z_score", "status", "rules_triggered", "comment",
                                      "entered_by_id", "voided_at", "void_reason"]),
    ("actions_correctives.csv", CorrectiveAction, ["id", "source_type", "source_id", "description",
                                                   "responsible_id", "due_on", "done_on", "status",
                                                   "validated_by_id", "validated_at"]),
    ("transmissions.csv", Transmission, ["id", "author_id", "category", "priority", "title", "body", "due_on",
                                         "status", "created_at"]),
    ("non_conformites.csv", NonConformity, ["id", "year", "seq", "title", "description", "detected_on",
                                            "origin", "severity", "status", "root_cause", "closed_on"]),
    ("pieces_jointes.csv", Attachment, ["id", "original_name", "content_type", "size_bytes", "sha256",
                                        "owner_type", "owner_id", "created_at"]),
    ("audit.csv", AuditEvent, ["id", "occurred_at", "user_id", "action", "object_type", "object_id", "ip",
                               "before", "after", "reason"]),
]


def export_lab_data() -> bytes:
    """Archive ZIP de toutes les données du laboratoire actif (droit à la portabilité)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for filename, model, columns in EXPORT_TABLES:
            if model is None:
                stmt = sa.select(Membership, User).join(User, User.id == Membership.user_id)
                rows = [[u.id, u.full_name, u.email, m.role, m.is_active, m.joined_at]
                        for m, u in db.session.execute(stmt).all()]
                zf.writestr(filename, build_csv(["id", "nom", "email", "rôle", "actif", "depuis"], rows))
                continue
            zf.writestr(filename, build_csv(columns, _rows(model, columns)))
        zf.writestr("LISEZMOI.txt", (
            "Export des données du laboratoire.\n"
            f"Généré le {datetime.now().strftime('%d/%m/%Y %H:%M')}.\n"
            "Fichiers CSV : UTF-8 avec BOM, séparateur point-virgule, virgule décimale.\n"
            "Les pièces jointes elles-mêmes sont listées dans pieces_jointes.csv ; elles peuvent être "
            "téléchargées depuis l'application ou récupérées depuis le stockage par l'hébergeur.\n"
        ))
    audit_service.record("export.lab_data", object_type="laboratory", object_id=current_tenant_id(),
                         after={"generated_at": utcnow().isoformat()})
    db.session.commit()
    return buffer.getvalue()
