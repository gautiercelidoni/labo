"""Envoi d'emails : SMTP en production, console en développement, mémoire en test."""
from __future__ import annotations

import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage

from flask import current_app, render_template

# Boîte d'envoi utilisée par les tests (MAIL_BACKEND=memory).
outbox: list["SentMail"] = []


@dataclass
class SentMail:
    to: list[str]
    subject: str
    body: str


def send_email(to: str | list[str], subject: str, template: str, **context) -> None:
    recipients = [to] if isinstance(to, str) else list(to)
    if not recipients:
        return
    body = render_template(f"emails/{template}.txt", **context)
    backend = current_app.config["MAIL_BACKEND"]
    if backend == "memory":
        outbox.append(SentMail(recipients, subject, body))
        return
    if backend == "console":
        current_app.logger.info("EMAIL à %s — %s\n%s", ", ".join(recipients), subject, body)
        return
    msg = EmailMessage()
    msg["From"] = current_app.config["MAIL_FROM"]
    msg["To"] = ", ".join(recipients)
    msg["Subject"] = subject
    msg.set_content(body)
    cfg = current_app.config
    try:
        if cfg["SMTP_USE_SSL"]:
            server: smtplib.SMTP = smtplib.SMTP_SSL(
                cfg["SMTP_HOST"], cfg["SMTP_PORT"], context=ssl.create_default_context(), timeout=20
            )
        else:
            server = smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=20)
            if cfg["SMTP_USE_TLS"]:
                server.starttls(context=ssl.create_default_context())
        with server:
            if cfg["SMTP_USER"]:
                server.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            server.send_message(msg)
    except (OSError, smtplib.SMTPException):
        # L'échec d'envoi ne doit pas annuler l'action métier ; il est journalisé.
        current_app.logger.exception("Échec d'envoi d'email à %s", recipients)
        raise EmailError("L'email n'a pas pu être envoyé.")


class EmailError(RuntimeError):
    pass


def external_url(path: str) -> str:
    return current_app.config["BASE_URL"].rstrip("/") + path
