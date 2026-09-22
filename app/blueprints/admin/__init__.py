from flask import Blueprint

bp = Blueprint("admin", __name__, url_prefix="/administration")

from app.blueprints.admin import routes  # noqa: E402,F401
