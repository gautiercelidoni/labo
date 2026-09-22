"""Commandes Flask : données de démo, laboratoires, notifications (cron), échéances, RGPD."""
from __future__ import annotations

import os
import uuid
from datetime import date

import click
import sqlalchemy as sa
from flask import Flask, current_app

from app.extensions import db
from app.security.tenancy import system_context, tenant_context


def register_cli(app: Flask) -> None:
    @app.cli.command("seed-demo")
    @click.option("--force", is_flag=True, help="Autorise l'exécution hors environnement de développement.")
    def seed_demo(force: bool):
        """Crée deux laboratoires de démonstration (isolation) dont un complet."""
        if current_app.config["ENV_NAME"] == "production" and not force:
            raise click.ClickException("Refusé en production (utilisez --force en connaissance de cause).")
        from app.demo import seed

        credentials = seed()
        click.echo("Données de démonstration créées.")
        if current_app.config["SHOW_DEMO_CREDENTIALS"]:
            click.echo("Comptes (mot de passe commun : " + credentials["password"] + ") :")
            for email, desc in credentials["users"]:
                click.echo(f"  {email:<40} {desc}")

    @app.cli.command("create-lab")
    @click.option("--name", required=True, help="Nom du laboratoire")
    @click.option("--admin-email", required=True)
    @click.option("--admin-name", required=True)
    @click.option("--timezone", "tz", default="Europe/Paris")
    def create_lab(name: str, admin_email: str, admin_name: str, tz: str):
        """Crée un laboratoire, son premier administrateur et un essai gratuit."""
        from app.services.auth_service import AuthError, create_laboratory, find_user_by_email

        password = None
        if find_user_by_email(admin_email) is None:
            password = click.prompt("Mot de passe du nouvel administrateur", hide_input=True,
                                    confirmation_prompt=True)
        try:
            lab, user = create_laboratory(name, admin_email, admin_name, password, timezone_name=tz)
            db.session.commit()
        except AuthError as e:
            db.session.rollback()
            raise click.ClickException(str(e))
        click.echo(f"Laboratoire « {lab.name} » créé ({lab.id}). Administrateur : {user.email}")

    @app.cli.command("create-platform-admin")
    @click.option("--email", required=True)
    @click.option("--name", required=True)
    def create_platform_admin(email: str, name: str):
        """Crée (ou promeut) un administrateur de la plateforme."""
        from app.models.user import User
        from app.security.passwords import hash_password
        from app.services.auth_service import check_password_policy, find_user_by_email

        user = find_user_by_email(email)
        if user is None:
            password = click.prompt("Mot de passe", hide_input=True, confirmation_prompt=True)
            check_password_policy(password)
            user = User(email=email.strip().lower(), full_name=name, password_hash=hash_password(password))
            db.session.add(user)
        user.is_platform_admin = True
        db.session.commit()
        click.echo(f"{user.email} est administrateur de la plateforme.")

    @app.cli.group("notifications")
    def notifications_group():
        """Notifications planifiées."""

    @notifications_group.command("run")
    @click.option("--date", "on", default=None, help="Date de référence AAAA-MM-JJ (tests)")
    def notifications_run(on: str | None):
        """Génère les alertes (J-30, J-7, J0, retards) et envoie les emails. À lancer par cron."""
        from app.services.notification_service import run_all

        stats = run_all(date.fromisoformat(on) if on else None)
        click.echo(" ; ".join(f"{k} : {v}" for k, v in stats.items()))

    @app.cli.group("metrologie")
    def metrology_group():
        """Métrologie."""

    @metrology_group.command("recalcul-echeances")
    def recompute():
        """Recalcule la prochaine échéance de tous les plans actifs, pour tous les laboratoires."""
        from app.models.tenant import Laboratory
        from app.services.metrology_service import recompute_due_dates

        with system_context():
            lab_ids = db.session.scalars(sa.select(Laboratory.id)).all()
        total = 0
        for lab_id in lab_ids:
            with tenant_context(lab_id):
                total += recompute_due_dates()
        click.echo(f"{total} échéance(s) recalculée(s).")

    @app.cli.group("rgpd")
    def rgpd_group():
        """Protection des données."""

    @rgpd_group.command("anonymiser-utilisateur")
    @click.argument("email")
    @click.option("--yes", is_flag=True, help="Ne pas demander de confirmation")
    def anonymize(email: str, yes: bool):
        """Anonymise un utilisateur : identité effacée, accès désactivés, traces d'audit conservées."""
        from app.models.base import utcnow
        from app.models.tenant import Membership
        from app.security.passwords import hash_password
        from app.services import audit_service
        from app.services.auth_service import find_user_by_email

        user = find_user_by_email(email)
        if user is None:
            raise click.ClickException("Utilisateur introuvable.")
        if not yes:
            click.confirm(f"Anonymiser définitivement {user.email} ?", abort=True)
        with system_context():
            memberships = db.session.scalars(sa.select(Membership).where(Membership.user_id == user.id)).all()
            for m in memberships:
                m.is_active = False
            user.email = f"anonyme-{uuid.uuid4().hex[:12]}@invalid.local"
            user.full_name = "Utilisateur anonymisé"
            user.password_hash = hash_password(os.urandom(32).hex())
            user.anonymized_at = utcnow()
            user.session_version += 1
            for m in memberships:
                audit_service.record("user.anonymized", object_type="user", object_id=user.id, tenant_id=m.tenant_id)
            db.session.commit()
        click.echo("Utilisateur anonymisé.")

    @rgpd_group.command("purger")
    def purge():
        """Purge des données techniques : tentatives de connexion > 30 jours, jetons expirés."""
        from app.models.base import utcnow
        from app.models.user import AuthToken
        from app.security.rate_limit import purge_old_attempts

        n = purge_old_attempts()
        t = db.session.execute(sa.delete(AuthToken).where(AuthToken.expires_at < utcnow())).rowcount
        db.session.commit()
        click.echo(f"{n} tentative(s) et {t} jeton(s) supprimé(s).")
