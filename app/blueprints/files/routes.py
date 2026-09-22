"""Téléchargement contrôlé des pièces jointes et affichage du logo du laboratoire."""
from __future__ import annotations

from flask import Response, abort, g, redirect

from app.blueprints.files import bp
from app.extensions import db
from app.models.actions import CorrectiveAction
from app.models.equipment import Equipment, MaintenanceEvent
from app.models.files import Attachment
from app.models.non_conformities import NonConformity
from app.models.transmissions import Transmission
from app.repositories.base import repo
from app.security.permissions import P, can, require_lab
from app.services import audit_service, lab_service
from app.services.file_storage import IMAGE_TYPES, get_storage

# Type de propriétaire -> (modèle, permission de lecture)
OWNER_RULES = {
    "equipment": (Equipment, P.METROLOGY_VIEW),
    "maintenance_event": (MaintenanceEvent, P.METROLOGY_VIEW),
    "corrective_action": (CorrectiveAction, P.CA_VIEW),
    "transmission": (Transmission, P.TRANSMISSION_VIEW),
    "non_conformity": (NonConformity, P.NC_VIEW),
}


def check_owner_access(attachment: Attachment) -> None:
    """Le lecteur doit pouvoir lire l'objet propriétaire, dans le laboratoire courant."""
    if attachment.owner_type == "laboratory":
        if attachment.owner_id != g.lab.id:
            abort(404)
        return
    rule = OWNER_RULES.get(attachment.owner_type)
    if rule is None:
        abort(404)
    model, permission = rule
    if not can(permission):
        abort(403)
    owner = repo(model).get(attachment.owner_id)
    if owner is None:
        abort(404)
    if model is Transmission:
        from app.services.transmission_service import can_view

        if not can_view(owner):
            abort(404)


@bp.route("/fichiers/<attachment_id>")
@require_lab
def download(attachment_id):
    attachment = repo(Attachment).get_or_404(attachment_id)
    check_owner_access(attachment)
    audit_service.record("file.downloaded", attachment, after={"name": attachment.original_name})
    db.session.commit()
    storage = get_storage()
    url = storage.presigned_url(attachment.storage_key, attachment.original_name, attachment.content_type)
    if url:
        return redirect(url)
    data = storage.read(attachment.storage_key)
    ascii_name = attachment.original_name.encode("ascii", "replace").decode().replace('"', "_").replace("?", "_")
    from urllib.parse import quote

    return Response(
        data,
        mimetype=attachment.content_type,
        headers={
            "Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(attachment.original_name)}",
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    )


@bp.route("/laboratoire/logo")
@require_lab
def lab_logo():
    result = lab_service.logo_bytes(g.lab)
    if result is None:
        abort(404)
    data, content_type = result
    if content_type not in IMAGE_TYPES:
        abort(404)
    return Response(data, mimetype=content_type,
                    headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})
