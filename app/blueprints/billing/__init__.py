from flask import Blueprint

bp = Blueprint("billing", __name__)

from app.blueprints.billing import routes  # noqa: E402,F401
