"""Point de santé pour le reverse proxy, Docker et la supervision."""
import sqlalchemy as sa
from flask import Blueprint, jsonify

from app.extensions import db

bp = Blueprint("health", __name__)


@bp.route("/sante")
def health():
    try:
        db.session.execute(sa.text("SELECT 1"))
        database = "ok"
    except Exception:  # la base est injoignable : l'état est rapporté, pas levé
        db.session.rollback()
        database = "erreur"
    status = 200 if database == "ok" else 503
    return jsonify({"statut": "ok" if status == 200 else "degrade", "base": database}), status
