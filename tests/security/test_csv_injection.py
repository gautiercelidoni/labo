"""Exports CSV : format Excel français et neutralisation des formules."""
from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from app.services.export_csv import build_csv, format_cell, sanitize_text


@pytest.mark.parametrize("value", ["=1+1", "+33 6 00", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd",
                                   "=HYPERLINK(\"http://evil\")"])
def test_formula_prefixes_are_neutralised(value):
    assert sanitize_text(value) == "'" + value
    assert format_cell(value) == "'" + value


def test_plain_text_untouched():
    assert format_cell("Glucose N1") == "Glucose N1"
    assert format_cell("") == ""


def test_numbers_use_decimal_comma_and_are_not_prefixed():
    assert format_cell(Decimal("-1.500000")) == "-1,500000"
    assert format_cell(Decimal("12")) == "12"
    assert format_cell(3) == "3"
    assert format_cell(True) == "Oui"
    assert format_cell(None) == ""


def test_dates_french_format(app):
    with app.test_request_context():
        assert format_cell(date(2026, 3, 9)) == "09/03/2026"
        assert format_cell(datetime(2026, 3, 9, 7, 5, tzinfo=timezone.utc)) == "09/03/2026 08:05"  # Europe/Paris


def test_csv_file_has_bom_and_semicolons():
    data = build_csv(["Nom", "Valeur"], [["=cmd|' /C calc'!A0", Decimal("2.5")], ["N2; lot", Decimal("-1")]])
    text = data.decode("utf-8")
    assert text.startswith("﻿")
    lines = text[1:].split("\r\n")
    assert lines[0] == "Nom;Valeur"
    assert lines[1].startswith("'=cmd")
    assert lines[1].endswith(";2,5")
    assert lines[2] == '"N2; lot";-1'


def test_export_endpoint_neutralises_injection(app, client, world):
    from app.extensions import db
    from app.security.tenancy import tenant_context
    from tests import factories
    from tests.conftest import login

    with app.app_context(), tenant_context(world.lab_a):
        factories.make_equipment("=HYPERLINK(\"http://x\")", "@EVIL")
        db.session.commit()
    login(client, world.emails["reader"])
    response = client.get("/metrologie/equipements/export.csv")
    text = response.data.decode("utf-8-sig")
    assert "'@EVIL" in text and "'=HYPERLINK" in text
    assert response.headers["Content-Type"].startswith("text/csv")
