from flask import Blueprint

bp = Blueprint("transmissions", __name__, url_prefix="/transmissions")

from app.blueprints.transmissions import routes  # noqa: E402,F401
