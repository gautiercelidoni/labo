"""Champs et validateurs de formulaires partagés."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from flask import g
from flask_wtf import FlaskForm
from wtforms import Field, SelectField, StringField
from wtforms.validators import ValidationError
from wtforms.widgets import TextInput


# Tous les formulaires utilisent les messages de validation français de WTForms.
FlaskForm.Meta.locales = ["fr_FR", "fr"]


def parse_decimal(raw: str | None) -> Decimal | None:
    """« 1 234,5 » ou « 1234.5 » -> Decimal ; chaîne vide -> None ; ValueError si invalide."""
    if raw is None or not raw.strip():
        return None
    text = raw.strip().replace(" ", "").replace("\u202f", "").replace("\xa0", "").replace(",", ".")
    try:
        value = Decimal(text)
    except InvalidOperation:
        raise ValueError("Nombre invalide.")
    if not value.is_finite():
        raise ValueError("Nombre invalide.")
    return value


class DecimalCommaField(Field):
    """Nombre décimal acceptant la virgule française ; stocké en Decimal (jamais en float)."""

    widget = TextInput()

    def __init__(self, label=None, validators=None, **kwargs):
        kwargs.setdefault("render_kw", {"inputmode": "decimal", "autocomplete": "off"})
        super().__init__(label, validators, **kwargs)

    def _value(self):
        if self.raw_data:
            return self.raw_data[0]
        if self.data is None:
            return ""
        return format(self.data, "f").replace(".", ",")

    def process_formdata(self, valuelist):
        self.data = None
        if not valuelist or not valuelist[0].strip():
            return
        raw = valuelist[0].strip().replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            raise ValueError("Nombre invalide.")
        if not value.is_finite():
            raise ValueError("Nombre invalide.")
        self.data = value


class UUIDSelectField(SelectField):
    """Liste déroulante dont la valeur est un UUID (ou None si vide)."""

    def __init__(self, label=None, validators=None, allow_blank=True, **kwargs):
        super().__init__(label, validators, coerce=self._coerce, validate_choice=True, **kwargs)
        self.allow_blank = allow_blank

    @staticmethod
    def _coerce(value):
        if value in (None, "", "None"):
            return None
        if isinstance(value, uuid.UUID):
            return value
        try:
            return uuid.UUID(str(value))
        except ValueError:
            return None

    def pre_validate(self, form):
        if self.data is None:
            if self.allow_blank:
                return
            raise ValidationError("Choix obligatoire.")
        valid = {self._coerce(v) for v, *_ in self.iter_choices_values()}
        if self.data not in valid:
            raise ValidationError("Choix invalide.")

    def iter_choices_values(self):
        for choice in self.choices or []:
            yield (choice[0],)


class LocalDateTimeField(StringField):
    """Date et heure saisies dans le fuseau du laboratoire, converties en UTC."""

    def __init__(self, label=None, validators=None, **kwargs):
        kwargs.setdefault("render_kw", {"type": "datetime-local"})
        super().__init__(label, validators, **kwargs)

    def _value(self):
        if self.raw_data:
            return self.raw_data[0]
        if isinstance(self.data, datetime):
            return self.data.astimezone(lab_zone()).strftime("%Y-%m-%dT%H:%M")
        return ""

    def process_formdata(self, valuelist):
        self.data = None
        if not valuelist or not valuelist[0]:
            return
        try:
            local = datetime.strptime(valuelist[0][:16], "%Y-%m-%dT%H:%M")
        except ValueError:
            raise ValueError("Date et heure invalides.")
        self.data = local.replace(tzinfo=lab_zone()).astimezone(timezone.utc)


def lab_zone() -> ZoneInfo:
    lab = getattr(g, "lab", None)
    try:
        return ZoneInfo(lab.timezone) if lab else ZoneInfo("Europe/Paris")
    except Exception:
        return ZoneInfo("Europe/Paris")


def lab_today():
    return datetime.now(lab_zone()).date()


def user_choices(users, blank: str | None = "— Aucun —"):
    choices = [("", blank)] if blank is not None else []
    return choices + [(str(u.id), u.full_name) for u in users]
