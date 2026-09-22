from flask import Blueprint

bp = Blueprint("metrologie", __name__, url_prefix="/metrologie")

from app.blueprints.metrologie import routes  # noqa: E402,F401
