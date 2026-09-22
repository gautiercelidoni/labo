"""Exports CSV compatibles Excel français.

Format : UTF-8 avec BOM, séparateur point-virgule, virgule décimale, dates JJ/MM/AAAA dans le
fuseau du laboratoire. Protection contre l'injection de formules : toute cellule texte
commençant par = + - @, une tabulation ou un retour chariot est préfixée d'une apostrophe.
Les nombres sont formatés par l'application (jamais préfixés) : « -1,5 » reste un nombre.
"""
from __future__ import annotations

import csv
import io
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Iterable, Sequence

from flask import Response

from app.services import audit_service

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def sanitize_text(value: str) -> str:
    if value and value.startswith(FORMULA_PREFIXES):
        return "'" + value
    return value


def format_cell(value: Any) -> str:
    from app import format_datetime

    if value is None:
        return ""
    if isinstance(value, bool):
        return "Oui" if value else "Non"
    if isinstance(value, (int,)):
        return str(value)
    if isinstance(value, Decimal):
        return format(value.normalize() if value == value.to_integral() else value, "f").replace(".", ",")
    if isinstance(value, float):
        return repr(value).replace(".", ",")
    if isinstance(value, datetime):
        return format_datetime(value)
    if isinstance(value, date):
        return value.strftime("%d/%m/%Y")
    return sanitize_text(str(value))


def build_csv(headers: Sequence[str], rows: Iterable[Sequence[Any]]) -> bytes:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")
    writer.writerow([sanitize_text(h) for h in headers])
    for row in rows:
        writer.writerow([format_cell(v) for v in row])
    return ("﻿" + buffer.getvalue()).encode("utf-8")


def csv_response(filename: str, headers: Sequence[str], rows: Iterable[Sequence[Any]],
                 audit_filters: dict | None = None) -> Response:
    rows = list(rows)
    data = build_csv(headers, rows)
    audit_service.record("export.csv", object_type="export", object_id=filename,
                         after={"fichier": filename, "lignes": len(rows), "filtres": audit_filters or {}})
    from app.extensions import db

    db.session.commit()
    return Response(
        data,
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
