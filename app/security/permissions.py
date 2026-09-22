"""Permissions nommées, rôles fixes et décorateur de contrôle côté serveur."""
from __future__ import annotations

from functools import wraps
from typing import Callable

from flask import abort, g, redirect, request, url_for
from flask_login import current_user


class P:
    DASHBOARD_VIEW = "dashboard.view"
    CIQ_VIEW = "ciq.view"
    CIQ_RESULT_CREATE = "ciq.result.create"
    CIQ_RESULT_VOID = "ciq.result.void"
    CIQ_RUN_JUSTIFY = "ciq.run.justify"
    CIQ_CONFIG_EDIT = "ciq.config.edit"
    METROLOGY_VIEW = "metrology.view"
    METROLOGY_EQUIPMENT_EDIT = "metrology.equipment.edit"
    METROLOGY_PLAN_EDIT = "metrology.plan.edit"
    METROLOGY_EVENT_CREATE = "metrology.event.create"
    CA_VIEW = "corrective_action.view"
    CA_CREATE = "corrective_action.create"
    CA_COMPLETE = "corrective_action.complete"
    CA_VALIDATE = "corrective_action.validate"
    AUDIT_VIEW = "audit.view"
    EXPORT_RUN = "export.run"
    FILES_UPLOAD = "files.upload"
    USERS_MANAGE = "users.manage"
    LAB_SETTINGS = "lab.settings.edit"
    BILLING_MANAGE = "billing.manage"
    TRANSMISSION_VIEW = "transmission.view"
    TRANSMISSION_VIEW_ALL = "transmission.view_all"
    TRANSMISSION_CREATE = "transmission.create"
    TRANSMISSION_UPDATE = "transmission.update"
    NC_VIEW = "nc.view"
    NC_CREATE = "nc.create"
    NC_EDIT = "nc.edit"
    NC_CLOSE = "nc.close"


ALL_PERMISSIONS = frozenset(v for k, v in vars(P).items() if k.isupper())

# Permissions de consultation : restent accordées quand le laboratoire est en lecture seule.
READ_PERMISSIONS = frozenset(
    {
        P.DASHBOARD_VIEW,
        P.CIQ_VIEW,
        P.METROLOGY_VIEW,
        P.CA_VIEW,
        P.AUDIT_VIEW,
        P.EXPORT_RUN,
        P.TRANSMISSION_VIEW,
        P.TRANSMISSION_VIEW_ALL,
        P.NC_VIEW,
    }
)
# Toujours accordée à l'administrateur, même en lecture seule, pour pouvoir régulariser.
ALWAYS_ALLOWED = frozenset({P.BILLING_MANAGE})

_READER = {
    P.DASHBOARD_VIEW,
    P.CIQ_VIEW,
    P.METROLOGY_VIEW,
    P.CA_VIEW,
    P.EXPORT_RUN,
    P.TRANSMISSION_VIEW,
    P.NC_VIEW,
}
_TECHNICIAN = _READER | {
    P.CIQ_RESULT_CREATE,
    P.METROLOGY_EVENT_CREATE,
    P.CA_COMPLETE,
    P.FILES_UPLOAD,
    P.TRANSMISSION_CREATE,
    P.TRANSMISSION_UPDATE,
    P.NC_CREATE,
}
_QUALITY = _TECHNICIAN | {
    P.CIQ_RESULT_VOID,
    P.CIQ_RUN_JUSTIFY,
    P.CIQ_CONFIG_EDIT,
    P.METROLOGY_EQUIPMENT_EDIT,
    P.METROLOGY_PLAN_EDIT,
    P.CA_CREATE,
    P.CA_VALIDATE,
    P.AUDIT_VIEW,
    P.TRANSMISSION_VIEW_ALL,
    P.NC_EDIT,
    P.NC_CLOSE,
}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": ALL_PERMISSIONS,
    "quality": frozenset(_QUALITY),
    "technician": frozenset(_TECHNICIAN),
    "reader": frozenset(_READER),
}


def role_has(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def can(permission: str) -> bool:
    """Vérifie la permission pour l'appartenance active (rôle relu à chaque requête)."""
    membership = getattr(g, "membership", None)
    if membership is None or not current_user.is_authenticated:
        return False
    if not role_has(membership.role, permission):
        return False
    if permission in READ_PERMISSIONS or permission in ALWAYS_ALLOWED:
        return True
    return bool(getattr(g, "write_allowed", False))


def is_read_only_denial(permission: str) -> bool:
    membership = getattr(g, "membership", None)
    return (
        membership is not None
        and role_has(membership.role, permission)
        and not getattr(g, "write_allowed", False)
    )


def require(*permissions: str) -> Callable:
    """Exige une connexion, un laboratoire actif et toutes les permissions indiquées."""

    def decorator(view: Callable) -> Callable:
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return redirect(url_for("auth.login", next=request.full_path))
            if getattr(g, "membership", None) is None:
                return redirect(url_for("auth.select_lab"))
            for permission in permissions:
                if not can(permission):
                    if is_read_only_denial(permission):
                        g.denial_reason = "read_only"
                    abort(403)
            return view(*args, **kwargs)

        wrapped.required_permissions = permissions  # utilisé par les tests de couverture
        return wrapped

    return decorator


def require_lab(view: Callable) -> Callable:
    """Exige seulement un laboratoire actif (pages accessibles à tout membre)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated:
            return redirect(url_for("auth.login", next=request.full_path))
        if getattr(g, "membership", None) is None:
            return redirect(url_for("auth.select_lab"))
        return view(*args, **kwargs)

    wrapped.required_permissions = ()
    return wrapped
