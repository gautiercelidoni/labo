"""Application factory."""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from flask import Flask, g, has_request_context, render_template, request, session
from flask_login import current_user
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import get_config
from app.extensions import csrf, db, login_manager, migrate


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if has_request_context():
            payload["request_id"] = getattr(g, "request_id", None)
            payload["path"] = request.path
            payload["method"] = request.method
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging(app: Flask) -> None:
    handler = logging.StreamHandler(sys.stdout)
    if app.config["LOG_JSON"]:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    app.logger.handlers = [handler]
    app.logger.setLevel(app.config["LOG_LEVEL"])
    app.logger.propagate = False


def _lab_tz() -> ZoneInfo:
    lab = getattr(g, "lab", None) if has_request_context() else None
    try:
        return ZoneInfo(lab.timezone if lab else "Europe/Paris")
    except Exception:  # fuseau invalide en base : repli sûr
        return ZoneInfo("Europe/Paris")


def format_datetime(value: datetime | None, fmt: str = "%d/%m/%Y %H:%M") -> str:
    if value is None:
        return "—"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(_lab_tz()).strftime(fmt)


def format_date(value: date | None) -> str:
    if value is None:
        return "—"
    return value.strftime("%d/%m/%Y")


def format_number(value, decimals: int | None = None) -> str:
    if value is None:
        return "—"
    if isinstance(value, (int, float, Decimal)):
        if decimals is not None:
            value = Decimal(value).quantize(Decimal(1).scaleb(-decimals))
        text = format(value, "f") if isinstance(value, Decimal) else str(value)
        return text.replace(".", ",")
    return str(value)


def create_app(config_name: str | None = None) -> Flask:
    if (config_name or os.environ.get("APP_ENV", "development")) == "development":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
    config_cls = get_config(config_name)
    app = Flask(__name__)
    app.config.from_object(config_cls)
    app.config["MAX_CONTENT_LENGTH"] = (app.config["MAX_UPLOAD_MB"] + 1) * 1024 * 1024
    if not app.config["SECRET_KEY"]:
        if config_cls.ENV_NAME == "development":
            app.config["SECRET_KEY"] = os.urandom(32).hex()
        else:
            raise RuntimeError("SECRET_KEY doit être défini.")
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)  # type: ignore[method-assign]

    _configure_logging(app)

    from app.models import all as _all_models  # noqa: F401  enregistre les modèles
    from app import forms as _forms  # noqa: F401  messages de validation en français

    db.init_app(app)
    migrate.init_app(app, db, directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "migrations"))
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.security.headers import init_headers
    from app.security.request_context import init_request_context

    init_request_context(app)
    init_headers(app)
    _register_user_loader()
    _register_template_helpers(app)
    _register_blueprints(app)
    _register_error_handlers(app)

    from app.cli import register_cli

    register_cli(app)
    return app


def _register_user_loader() -> None:
    import sqlalchemy as sa

    from app.models.user import User
    from app.repositories.base import parse_uuid

    @login_manager.user_loader
    def load_user(user_id: str):
        uid = parse_uuid(user_id)
        if uid is None:
            return None
        user = db.session.scalar(sa.select(User).where(User.id == uid))
        if user is None or not user.is_active:
            return None
        # Invalidation des autres sessions après changement de mot de passe.
        if session.get("sv") != user.session_version:
            return None
        return user


def _register_template_helpers(app: Flask) -> None:
    from app.security.permissions import can

    app.jinja_env.filters["dt"] = format_datetime
    app.jinja_env.filters["d"] = format_date
    app.jinja_env.filters["num"] = format_number

    @app.context_processor
    def inject_globals():
        from app.services.notification_service import unread_count

        membership = getattr(g, "membership", None)
        return {
            "can": can,
            "active_lab": getattr(g, "lab", None),
            "membership": membership,
            "write_allowed": getattr(g, "write_allowed", False),
            "access_reason": getattr(g, "access_reason", None),
            "unread_notifications": unread_count() if membership and current_user.is_authenticated else 0,
            "app_env": app.config["ENV_NAME"],
        }


def _register_blueprints(app: Flask) -> None:
    from app.blueprints.actions import bp as actions_bp
    from app.blueprints.admin import bp as admin_bp
    from app.blueprints.audit import bp as audit_bp
    from app.blueprints.auth import bp as auth_bp
    from app.blueprints.billing import bp as billing_bp
    from app.blueprints.ciq import bp as ciq_bp
    from app.blueprints.dashboard import bp as dashboard_bp
    from app.blueprints.files import bp as files_bp
    from app.blueprints.health import bp as health_bp
    from app.blueprints.metrologie import bp as metrologie_bp
    from app.blueprints.non_conformites import bp as nc_bp
    from app.blueprints.notifications import bp as notifications_bp
    from app.blueprints.transmissions import bp as transmissions_bp

    for bp in (
        health_bp,
        auth_bp,
        dashboard_bp,
        admin_bp,
        ciq_bp,
        actions_bp,
        metrologie_bp,
        files_bp,
        audit_bp,
        billing_bp,
        notifications_bp,
        transmissions_bp,
        nc_bp,
    ):
        app.register_blueprint(bp)


def _register_error_handlers(app: Flask) -> None:
    from flask_wtf.csrf import CSRFError

    from app.security.tenancy import TenantError

    messages = {
        400: ("Requête invalide", "La requête n'a pas pu être traitée."),
        403: ("Accès refusé", "Vous n'avez pas les droits nécessaires pour cette action."),
        404: ("Page introuvable", "La ressource demandée n'existe pas ou n'est pas accessible."),
        405: ("Méthode non autorisée", "Cette action n'est pas permise."),
        413: ("Fichier trop volumineux", "Le fichier dépasse la taille maximale autorisée."),
        500: ("Erreur interne", "Une erreur inattendue s'est produite. Elle a été journalisée."),
    }

    def render_error(code: int, detail: str | None = None):
        title, message = messages[code]
        if code == 403 and getattr(g, "denial_reason", None) == "read_only":
            message = (
                "Le laboratoire est en lecture seule ("
                + (getattr(g, "access_reason", None) or "abonnement inactif")
                + "). Un administrateur peut régulariser l'abonnement."
            )
        return render_template("errors/error.html", code=code, title=title, message=detail or message), code

    for code in (400, 403, 404, 405, 413):
        app.register_error_handler(code, lambda e, c=code: render_error(c))

    @app.errorhandler(CSRFError)
    def csrf_error(e):
        return render_error(400, "Le formulaire a expiré ou est invalide (protection CSRF). Rechargez la page.")

    @app.errorhandler(TenantError)
    def tenant_error(e):
        db.session.rollback()
        app.logger.warning("Violation d'isolation bloquée : %s", e)
        return render_error(404)

    @app.errorhandler(500)
    def internal_error(e):
        db.session.rollback()
        app.logger.exception("Erreur interne")
        return render_error(500)
