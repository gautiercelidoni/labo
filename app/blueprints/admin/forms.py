from zoneinfo import available_timezones

from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import BooleanField, EmailField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Email, Length, NumberRange, Optional

from app.forms import UUIDSelectField
from app.models.tenant import ROLE_LABELS

ROLE_CHOICES = list(ROLE_LABELS.items())
TIMEZONES = sorted(tz for tz in available_timezones() if tz.startswith(("Europe/", "America/", "Indian/", "Pacific/", "Africa/")))


class LabSettingsForm(FlaskForm):
    name = StringField("Nom du laboratoire", validators=[DataRequired(), Length(max=200)])
    timezone = SelectField("Fuseau horaire", choices=[(t, t) for t in TIMEZONES])
    siret = StringField("SIRET", validators=[Optional(), Length(max=20)])
    address = TextAreaField("Adresse", validators=[Optional(), Length(max=500)])
    accreditation = StringField("N° d'accréditation (facultatif)", validators=[Optional(), Length(max=80)])
    retention_days = IntegerField("Durée de conservation des données (jours)",
                                  validators=[DataRequired(), NumberRange(min=365, max=36500)])
    notify_by_email = BooleanField("Envoyer les alertes par email")
    logo = FileField("Logo (PNG ou JPEG)")


class InvitationForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=254)])
    role = SelectField("Rôle", choices=ROLE_CHOICES)
    team_id = UUIDSelectField("Équipe / poste")


class MembershipForm(FlaskForm):
    role = SelectField("Rôle", choices=ROLE_CHOICES)
    team_id = UUIDSelectField("Équipe / poste")


class TeamForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=120)])
    description = TextAreaField("Description", validators=[Optional(), Length(max=1000)])


class CategoryForm(FlaskForm):
    name = StringField("Nom de la catégorie", validators=[DataRequired(), Length(max=120)])
