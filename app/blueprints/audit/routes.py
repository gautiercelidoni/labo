"""Consultation du journal d'audit (lecture seule) et export CSV."""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

import json

import sqlalchemy as sa
from flask import render_template, request

from app.blueprints.audit import bp
from app.extensions import db
from app.forms import lab_zone
from app.models.audit import AuditEvent
from app.repositories.base import paginate, parse_uuid
from app.security.permissions import P, require
from app.services.audit_service import ACTION_LABELS, action_label
from app.services.export_csv import csv_response
from app.services.membership_service import member_users


def _parse_date(value: str | None):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() if value else None
    except ValueError:
        return None


def _filtered_query():
    args = request.args
    stmt = sa.select(AuditEvent).order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
    filters = {}
    if args.get("action"):
        stmt = stmt.where(AuditEvent.action.startswith(args["action"]))
        filters["action"] = args["action"]
    if args.get("objet"):
        stmt = stmt.where(AuditEvent.object_type == args["objet"])
        filters["objet"] = args["objet"]
    if args.get("identifiant"):
        stmt = stmt.where(AuditEvent.object_id == args["identifiant"])
        filters["identifiant"] = args["identifiant"]
    user_id = parse_uuid(args.get("utilisateur"))
    if user_id:
        stmt = stmt.where(AuditEvent.user_id == user_id)
        filters["utilisateur"] = str(user_id)
    tz = lab_zone()
    start = _parse_date(args.get("du"))
    if start:
        stmt = stmt.where(AuditEvent.occurred_at >= datetime.combine(start, time.min, tz).astimezone(timezone.utc))
        filters["du"] = start.isoformat()
    end = _parse_date(args.get("au"))
    if end:
        stmt = stmt.where(AuditEvent.occurred_at < datetime.combine(end + timedelta(days=1), time.min, tz)
                          .astimezone(timezone.utc))
        filters["au"] = end.isoformat()
    return stmt, filters


@bp.route("/")
@require(P.AUDIT_VIEW)
def index():
    stmt, filters = _filtered_query()
    page = paginate(stmt, request.args.get("page", 1, type=int), 50)
    object_types = db.session.scalars(
        sa.select(AuditEvent.object_type).where(AuditEvent.object_type.is_not(None)).distinct()
        .order_by(AuditEvent.object_type)
    ).all()
    return render_template("audit/index.html", page=page, filters=filters, users=member_users(),
                           object_types=object_types, action_labels=ACTION_LABELS)


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False) if value else ""


@bp.route("/export.csv")
@require(P.AUDIT_VIEW, P.EXPORT_RUN)
def export():
    stmt, filters = _filtered_query()
    events = db.session.scalars(stmt.limit(100000)).all()
    rows = [
        [e.occurred_at, e.user.full_name if e.user else "", e.action, action_label(e.action),
         e.object_type, e.object_id, e.ip, _json(e.before), _json(e.after), e.reason]
        for e in events
    ]
    return csv_response(
        "journal-audit.csv",
        ["Date", "Utilisateur", "Action", "Libellé", "Objet", "Identifiant", "IP", "Avant", "Après", "Motif"],
        rows,
        filters,
    )
