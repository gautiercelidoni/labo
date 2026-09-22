from flask import Blueprint

bp = Blueprint("non_conformites", __name__, url_prefix="/non-conformites")

from app.blueprints.non_conformites import routes  # noqa: E402,F401
