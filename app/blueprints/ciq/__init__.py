from flask import Blueprint

bp = Blueprint("ciq", __name__, url_prefix="/ciq")

from app.blueprints.ciq import routes  # noqa: E402,F401
