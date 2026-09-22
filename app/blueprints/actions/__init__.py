from flask import Blueprint

bp = Blueprint("actions", __name__, url_prefix="/actions-correctives")

from app.blueprints.actions import routes  # noqa: E402,F401
