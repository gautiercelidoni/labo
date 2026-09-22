from flask_wtf import FlaskForm
from wtforms import BooleanField, EmailField, PasswordField, StringField
from wtforms.validators import DataRequired, Email, EqualTo, Length


class LoginForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=254)],
                       render_kw={"autocomplete": "username", "autofocus": True})
    password = PasswordField("Mot de passe", validators=[DataRequired(), Length(max=200)],
                             render_kw={"autocomplete": "current-password"})
    remember = BooleanField("Rester connecté sur ce poste")


class ForgotPasswordForm(FlaskForm):
    email = EmailField("Email", validators=[DataRequired(), Email(), Length(max=254)])


class NewPasswordForm(FlaskForm):
    password = PasswordField("Nouveau mot de passe", validators=[DataRequired(), Length(max=200)],
                             render_kw={"autocomplete": "new-password"})
    confirm = PasswordField("Confirmation", validators=[DataRequired(), EqualTo("password", "Les mots de passe diffèrent.")],
                            render_kw={"autocomplete": "new-password"})


class ChangePasswordForm(NewPasswordForm):
    current = PasswordField("Mot de passe actuel", validators=[DataRequired()],
                            render_kw={"autocomplete": "current-password"})


class AcceptInvitationForm(NewPasswordForm):
    full_name = StringField("Nom complet", validators=[DataRequired(), Length(max=200)])


class SignupForm(NewPasswordForm):
    lab_name = StringField("Nom du laboratoire", validators=[DataRequired(), Length(max=200)])
    full_name = StringField("Votre nom complet", validators=[DataRequired(), Length(max=200)])
    email = EmailField("Email professionnel", validators=[DataRequired(), Email(), Length(max=254)])


class EmptyForm(FlaskForm):
    """Formulaire sans champ : jeton CSRF des boutons d'action."""
