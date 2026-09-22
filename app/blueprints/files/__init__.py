from flask import Blueprint

bp = Blueprint("files", __name__)

from app.blueprints.files import routes  # noqa: E402,F401
