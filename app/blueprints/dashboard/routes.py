from flask import redirect, render_template, url_for
from flask_login import current_user

from app.blueprints.dashboard import bp
from app.forms import lab_today
from app.security.permissions import P, require
from app.services import dashboard_service
from app.services.audit_service import ACTION_LABELS


@bp.route("/")
def root():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    return redirect(url_for("auth.login"))


@bp.route("/tableau-de-bord")
@require(P.DASHBOARD_VIEW)
def index():
    today = lab_today()
    return render_template("dashboard/index.html", cards=dashboard_service.cards(today),
                           activity=dashboard_service.recent_activity(), action_labels=ACTION_LABELS, today=today)
