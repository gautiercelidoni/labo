from flask import abort, redirect, render_template, request, url_for
from flask_login import current_user

from app.blueprints.auth.forms import EmptyForm
from app.blueprints.notifications import bp
from app.models.notifications import Notification
from app.repositories.base import paginate, repo
from app.security.permissions import require_lab
from app.services import notification_service


@bp.route("/")
@require_lab
def index():
    unread_only = request.args.get("non_lues") == "1"
    page = paginate(notification_service.list_query(unread_only), request.args.get("page", 1, type=int), 30)
    return render_template("notifications/index.html", page=page, unread_only=unread_only, form=EmptyForm())


@bp.route("/<notification_id>/ouvrir", methods=["POST"])
@require_lab
def open_notification(notification_id):
    if not EmptyForm().validate_on_submit():
        abort(400)
    notification = repo(Notification).get_or_404(notification_id)
    if notification.user_id != current_user.id:
        abort(404)
    notification_service.mark_read(notification)
    link = notification.link if notification.link and notification.link.startswith("/") else None
    return redirect(link or url_for("notifications.index"))


@bp.route("/tout-lire", methods=["POST"])
@require_lab
def mark_all():
    if not EmptyForm().validate_on_submit():
        abort(400)
    notification_service.mark_all_read()
    return redirect(url_for("notifications.index"))
