from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import BooleanField, DateField, IntegerField, SelectField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.forms import UUIDSelectField
from app.models.equipment import (
    CRITICALITY_LABELS,
    EQUIPMENT_STATUS_LABELS,
    EVENT_TYPE_LABELS,
    OUTCOME_LABELS,
    PERIOD_UNIT_LABELS,
)


class EquipmentForm(FlaskForm):
    name = StringField("Nom", validators=[DataRequired(), Length(max=200)])
    internal_id = StringField("Identifiant interne", validators=[DataRequired(), Length(max=60)])
    category_id = UUIDSelectField("Catégorie")
    manufacturer = StringField("Fabricant", validators=[Optional(), Length(max=120)])
    model = StringField("Modèle", validators=[Optional(), Length(max=120)])
    serial_number = StringField("N° de série", validators=[Optional(), Length(max=120)])
    location = StringField("Localisation", validators=[Optional(), Length(max=120)])
    commissioned_on = DateField("Mise en service", validators=[Optional()])
    status = SelectField("Statut", choices=list(EQUIPMENT_STATUS_LABELS.items()))
    criticality = SelectField("Criticité", choices=list(CRITICALITY_LABELS.items()), default="medium")
    responsible_id = UUIDSelectField("Responsable")
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=4000)])


class PlanForm(FlaskForm):
    event_type = SelectField("Type d'événement", choices=list(EVENT_TYPE_LABELS.items()))
    period_value = IntegerField("Périodicité", validators=[DataRequired(), NumberRange(min=1, max=1000)])
    period_unit = SelectField("Unité", choices=list(PERIOD_UNIT_LABELS.items()), default="year")
    provider = StringField("Prestataire", validators=[Optional(), Length(max=200)])
    responsible_id = UUIDSelectField("Responsable interne")
    last_done_on = DateField("Dernière réalisation", validators=[Optional()])
    next_due_on = DateField("Prochaine échéance (calculée si vide)", validators=[Optional()])
    comment = TextAreaField("Commentaire", validators=[Optional(), Length(max=2000)])
    is_active = BooleanField("Plan actif", default=True)


class EventForm(FlaskForm):
    plan_id = UUIDSelectField("Plan concerné (vide : intervention non planifiée)")
    event_type = SelectField("Type d'événement", choices=list(EVENT_TYPE_LABELS.items()))
    performed_on = DateField("Date effective de réalisation", validators=[DataRequired()])
    outcome = SelectField("Résultat", choices=[("", "— Sans objet —")] + list(OUTCOME_LABELS.items()))
    provider = StringField("Prestataire", validators=[Optional(), Length(max=200)])
    comment = TextAreaField("Compte rendu", validators=[Optional(), Length(max=4000)])
    attachment = FileField("Certificat ou compte rendu (PDF, image, DOCX, XLSX)")


class StatusForm(FlaskForm):
    status = SelectField("Nouveau statut", choices=list(EQUIPMENT_STATUS_LABELS.items()))
    reason = TextAreaField("Motif", validators=[DataRequired(), Length(max=2000)])


class ArchiveForm(FlaskForm):
    reason = TextAreaField("Motif de l'archivage", validators=[DataRequired(), Length(max=2000)])


class UploadForm(FlaskForm):
    file = FileField("Pièce jointe", validators=[DataRequired()])
