"""Rapports PDF A4 générés avec WeasyPrint à partir de gabarits Jinja.

Chaque rapport porte : nom et logo du laboratoire, période, date de génération, auteur,
filtres appliqués, pagination et identifiant de rapport. Les graphiques sont produits en SVG
côté serveur (aucun JavaScript dans les PDF).
"""
from __future__ import annotations

import base64
import calendar
import statistics
import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import sqlalchemy as sa
from flask import render_template
from flask_login import current_user
from markupsafe import Markup

from app.extensions import db
from app.forms import lab_zone
from app.models.base import utcnow
from app.models.ciq import CIQParameter, CIQResult, CIQRun, ControlLimitSet, ControlLot
from app.models.equipment import Equipment, MaintenancePlan
from app.repositories.base import repo
from app.services import audit_service, ciq_service, lab_service, metrology_service


def _render_pdf(template: str, title: str, period: str, filters: dict, **context) -> bytes:
    from weasyprint import HTML

    report_id = uuid.uuid4().hex[:12].upper()
    lab = lab_service.get_current_lab()
    logo = lab_service.logo_bytes(lab)
    logo_uri = f"data:{logo[1]};base64,{base64.b64encode(logo[0]).decode()}" if logo else None
    html = render_template(
        f"pdf/{template}", title=title, period=period, filters=filters, lab=lab, logo_uri=logo_uri,
        report_id=report_id, generated_at=utcnow(), author=current_user.full_name, **context,
    )
    pdf = HTML(string=html, base_url=None).write_pdf()
    audit_service.record("export.pdf", object_type="report", object_id=report_id,
                         after={"rapport": title, "periode": period, "filtres": filters})
    db.session.commit()
    return pdf


# ---------------------------------------------------------------------------
# Graphique SVG (Levey-Jennings / carte de contrôle)
# ---------------------------------------------------------------------------

def control_chart_svg(points: list[dict], width: int = 700, height: int = 220) -> Markup:
    """Tracé en z-score (−4 à +4) : lisible quel que soit le jeu de limites de chaque point."""
    if not points:
        return Markup("<p class='muted'>Aucun résultat sur la période.</p>")
    left, right, top, bottom = 40, 10, 10, 20
    plot_w, plot_h = width - left - right, height - top - bottom

    def y(z: float) -> float:
        z = max(-4.0, min(4.0, z))
        return top + plot_h * (4 - z) / 8

    n = len(points)

    def x(i: int) -> float:
        return left + (plot_w * (i + 0.5) / n)

    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
             f'viewBox="0 0 {width} {height}" font-family="sans-serif" font-size="9">']
    styles = {0: ("#333", "1"), 1: ("#9ab", "0.5"), 2: ("#e0a000", "0.8"), 3: ("#c00", "0.8")}
    for k in (3, 2, 1, 0, -1, -2, -3):
        color, w = styles[abs(k)]
        dash = "" if k == 0 else ' stroke-dasharray="4 3"'
        parts.append(f'<line x1="{left}" x2="{width - right}" y1="{y(k):.1f}" y2="{y(k):.1f}" '
                     f'stroke="{color}" stroke-width="{w}"{dash}/>')
        label = "cible" if k == 0 else f"{k:+d}s"
        parts.append(f'<text x="{left - 4}" y="{y(k) + 3:.1f}" text-anchor="end" fill="#555">{label}</text>')
    for i, p in enumerate(points):
        if p.get("lot_change") or p.get("limits_change"):
            parts.append(f'<line x1="{x(i) - plot_w / n / 2:.1f}" x2="{x(i) - plot_w / n / 2:.1f}" y1="{top}" '
                         f'y2="{top + plot_h}" stroke="#6a3" stroke-width="0.8" stroke-dasharray="2 2"/>')
    path = " ".join(f"{'M' if i == 0 else 'L'}{x(i):.1f},{y(p['z']):.1f}" for i, p in enumerate(points))
    parts.append(f'<path d="{path}" fill="none" stroke="#2a5d9f" stroke-width="1"/>')
    colors = {"accepted": "#2a5d9f", "warning": "#e0a000", "rejected": "#c00"}
    for i, p in enumerate(points):
        parts.append(f'<circle cx="{x(i):.1f}" cy="{y(p["z"]):.1f}" r="2.6" fill="{colors[p["status"]]}"/>')
    parts.append("</svg>")
    return Markup("".join(parts))


def _month_bounds(first: date) -> tuple[datetime, datetime]:
    tz = lab_zone()
    last_day = calendar.monthrange(first.year, first.month)[1]
    start = datetime.combine(first, time.min, tz).astimezone(timezone.utc)
    end = datetime.combine(first.replace(day=last_day) + timedelta(days=1), time.min, tz).astimezone(timezone.utc)
    return start, end


MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre",
          "novembre", "décembre"]


def ciq_monthly_report(parameter: CIQParameter, first: date) -> bytes:
    start, end = _month_bounds(first)
    config = ciq_service.get_config(parameter)
    equipment = repo(Equipment).get(parameter.equipment_id)
    sections = []
    for level in ciq_service.list_levels(parameter):
        rows = db.session.execute(
            sa.select(CIQResult, ControlLot.lot_number, ControlLimitSet)
            .join(ControlLot, ControlLot.id == CIQResult.lot_id)
            .join(ControlLimitSet, ControlLimitSet.id == CIQResult.limit_set_id)
            .where(CIQResult.parameter_id == parameter.id, CIQResult.level_id == level.id,
                   CIQResult.run_at >= start, CIQResult.run_at < end)
            .order_by(CIQResult.run_at, CIQResult.created_at)
        ).all()
        valid = [r for r, _, _ in rows if r.voided_at is None]
        values = [r.value for r in valid]
        stats = None
        if values:
            mean = sum(values, Decimal(0)) / len(values)
            sd = statistics.stdev(values) if len(values) > 1 else None
            stats = {"n": len(values), "mean": mean, "sd": sd,
                     "cv": (sd / mean * 100) if sd is not None and mean else None,
                     "warnings": sum(1 for r in valid if r.status == "warning"),
                     "rejects": sum(1 for r in valid if r.status == "rejected")}
        chart_points = []
        prev_lot = prev_lim = None
        for r, _, lim in rows:
            if r.voided_at is not None:
                continue
            chart_points.append({"z": float(r.z_score), "status": r.status,
                                 "lot_change": prev_lot is not None and prev_lot != r.lot_id,
                                 "limits_change": prev_lim is not None and prev_lim != lim.id})
            prev_lot, prev_lim = r.lot_id, lim.id
        sections.append({"level": level, "mode": ciq_service.effective_mode(level, config), "rows": rows,
                         "stats": stats, "chart": control_chart_svg(chart_points)})
    runs = repo(CIQRun).all(CIQRun.parameter_id == parameter.id, CIQRun.run_at >= start, CIQRun.run_at < end,
                            CIQRun.status.in_(("rejected", "justified")), order_by=CIQRun.run_at)
    period = f"{MONTHS[first.month - 1]} {first.year}"
    return _render_pdf("ciq_monthly.html", f"Rapport CIQ mensuel — {parameter.name}", period,
                       {"paramètre": parameter.name, "équipement": equipment.name if equipment else ""},
                       parameter=parameter, equipment=equipment, sections=sections, runs=runs, config=config)


def equipment_report(equipment: Equipment) -> bytes:
    plans = repo(MaintenancePlan).all(MaintenancePlan.equipment_id == equipment.id, order_by=MaintenancePlan.event_type)
    events = metrology_service.equipment_history(equipment)
    since = utcnow() - timedelta(days=365)
    ciq_summary = db.session.execute(
        sa.select(CIQParameter.name, CIQResult.status, sa.func.count())
        .join(CIQResult, CIQResult.parameter_id == CIQParameter.id)
        .where(CIQParameter.equipment_id == equipment.id, CIQResult.voided_at.is_(None), CIQResult.run_at >= since)
        .group_by(CIQParameter.name, CIQResult.status)
        .order_by(CIQParameter.name)
    ).all()
    summary: dict[str, dict] = {}
    for name, status, count in ciq_summary:
        summary.setdefault(name, {"accepted": 0, "warning": 0, "rejected": 0})[status] = count
    return _render_pdf("equipment.html", f"Historique de l'équipement {equipment.name}", "Historique complet",
                       {"équipement": f"{equipment.name} ({equipment.internal_id})"},
                       equipment=equipment, plans=plans, events=events, ciq_summary=summary)


def metrology_planning(plans: list[MaintenancePlan], window: str) -> bytes:
    from app.domain.scheduling import DUE_STATE_LABELS, due_state
    from app.forms import lab_today

    today = lab_today()
    labels = {"retard": "échéances dépassées", "tous": "toutes les échéances"}
    period = labels.get(window, f"échéances sous {window} jours")
    return _render_pdf("planning.html", "Planning métrologique", period, {"filtre": period}, plans=plans,
                       today=today, due_state=due_state, due_labels=DUE_STATE_LABELS)


def nc_report(items: list, single: bool = False, **context) -> bytes:
    title = f"Fiche de non-conformité {items[0].number}" if single else "Liste des non-conformités"
    return _render_pdf("nc.html", title, context.pop("period", "—"), context.pop("filters", {}), items=items,
                       single=single, **context)

