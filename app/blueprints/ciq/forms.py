from flask_wtf import FlaskForm
from wtforms import BooleanField, DateField, IntegerField, SelectField, SelectMultipleField, StringField, TextAreaField
from wtforms.validators import DataRequired, Length, NumberRange, Optional

from app.domain.ciq_rules.types import RULE_LABELS, SHEWHART_RULES, WESTGARD_RULES
from app.forms import DecimalCommaField, LocalDateTimeField, UUIDSelectField
from app.models.ciq import LIMIT_SOURCE_LABELS, MODE_LABELS

MODE_CHOICES = list(MODE_LABELS.items())
SEVERITY_CHOICES = [("off", "Désactivée"), ("warning", "Avertissement"), ("reject", "Rejet")]


class ParameterForm(FlaskForm):
    equipment_id = UUIDSelectField("Équipement", allow_blank=False)
    name = StringField("Paramètre / analyte", validators=[DataRequired(), Length(max=120)])
    unit = StringField("Unité", validators=[Optional(), Length(max=40)])
    decimals = IntegerField("Décimales affichées", default=2, validators=[NumberRange(min=0, max=6)])
    mode = SelectField("Mode d'évaluation par défaut", choices=MODE_CHOICES)
    is_active = BooleanField("Actif", default=True)


def make_rule_config_form():
    class RuleConfigForm(FlaskForm):
        mode = SelectField("Mode d'évaluation par défaut", choices=MODE_CHOICES)
        comment_required_on = SelectMultipleField(
            "Commentaire obligatoire en cas de", choices=[("warning", "Alerte"), ("reject", "Rejet")]
        )
        min_reference_points = IntegerField("Nombre minimum de valeurs de référence (Shewhart)", default=20,
                                            validators=[NumberRange(min=5, max=500)])
        chain_lots = BooleanField("Enchaîner les séquences lors d'un changement de lot (via le z-score)")

    for rule in WESTGARD_RULES + SHEWHART_RULES:
        setattr(RuleConfigForm, f"rule_{rule}", SelectField(RULE_LABELS[rule], choices=SEVERITY_CHOICES))
    return RuleConfigForm


RuleConfigForm = make_rule_config_form()


class LevelForm(FlaskForm):
    label = StringField("Libellé du niveau (N1, N2, bas, haut…)", validators=[DataRequired(), Length(max=40)])
    sort_order = IntegerField("Ordre", default=1, validators=[NumberRange(min=0, max=99)])
    mode = SelectField("Mode d'évaluation", choices=[("", "Celui du paramètre")] + MODE_CHOICES)
    is_active = BooleanField("Actif", default=True)


class LotForm(FlaskForm):
    lot_number = StringField("Numéro de lot", validators=[DataRequired(), Length(max=80)])
    manufacturer = StringField("Fabricant", validators=[Optional(), Length(max=120)])
    expires_on = DateField("Péremption", validators=[Optional()])
    in_use_from = DateField("Utilisé à partir du", validators=[Optional()])
    in_use_to = DateField("Utilisé jusqu'au", validators=[Optional()])


class LimitsForm(FlaskForm):
    mode = SelectField("Mode", choices=MODE_CHOICES)
    mean = DecimalCommaField("Cible / moyenne", validators=[DataRequired()])
    sd = DecimalCommaField("Écart-type", validators=[DataRequired()])
    source = SelectField("Origine", choices=[(k, v) for k, v in LIMIT_SOURCE_LABELS.items() if k != "computed"])
    reason = TextAreaField("Motif (obligatoire si un jeu de limites est déjà actif)",
                           validators=[Optional(), Length(max=2000)])


class ReferenceForm(FlaskForm):
    mode = SelectField("Mode", choices=MODE_CHOICES, default="shewhart")
    ref_from = LocalDateTimeField("Début de la période de référence", validators=[DataRequired()])
    ref_to = LocalDateTimeField("Fin de la période de référence", validators=[DataRequired()])
    reason = TextAreaField("Motif du calcul", validators=[DataRequired(), Length(max=2000)])
    accept_insufficient = BooleanField("Je confirme malgré un nombre de valeurs inférieur au minimum")


class RunEntryForm(FlaskForm):
    """Champs communs de la série ; les valeurs par niveau sont lues dynamiquement."""

    run_at = LocalDateTimeField("Date et heure de la série", validators=[DataRequired()])
    operator_id = UUIDSelectField("Technicien", allow_blank=False)


class VoidForm(FlaskForm):
    reason = TextAreaField("Motif de l'annulation", validators=[DataRequired(), Length(max=2000)])


class ReentryForm(FlaskForm):
    level_id = UUIDSelectField("Niveau", allow_blank=False)
    lot_id = UUIDSelectField("Lot", allow_blank=False)
    value = DecimalCommaField("Valeur", validators=[DataRequired()])
    comment = TextAreaField("Commentaire", validators=[Optional(), Length(max=2000)])


class JustifyForm(FlaskForm):
    justification = TextAreaField("Justification / analyse du rejet", validators=[DataRequired(), Length(max=4000)])
    existing_action_id = UUIDSelectField("Action corrective existante")
    action_description = TextAreaField("Nouvelle action corrective", validators=[Optional(), Length(max=4000)])
    responsible_id = UUIDSelectField("Responsable de l'action")
    due_on = DateField("Échéance de l'action", validators=[Optional()])
