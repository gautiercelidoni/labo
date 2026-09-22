"""Configuration par environnement, lue exclusivement depuis les variables d'environnement."""
from __future__ import annotations

import os
from datetime import timedelta


def _bool(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "oui", "on"}


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    return int(value) if value not in (None, "") else default


class BaseConfig:
    ENV_NAME = "base"
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "postgresql+psycopg://labq_app:app@localhost:5432/labqualite"
    )
    # Rôle propriétaire utilisé uniquement pour les migrations (droits DDL).
    DATABASE_OWNER_URL = os.environ.get("DATABASE_OWNER_URL", "")
    # Rôle applicatif auquel les migrations accordent les droits (INSERT/SELECT seulement sur l'audit).
    DB_APP_ROLE = os.environ.get("DB_APP_ROLE", "")
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = False
    SESSION_COOKIE_NAME = "labq_session"
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=_int("SESSION_HOURS", 12))
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None
    WTF_CSRF_HEADERS = ["X-CSRFToken"]
    # Messages de validation en français (traductions intégrées à WTForms, sans Babel).
    WTF_I18N_ENABLED = False

    BASE_URL = os.environ.get("BASE_URL", "http://localhost:5000")
    HSTS_ENABLED = False

    # Authentification
    LOGIN_MAX_FAILURES = _int("LOGIN_MAX_FAILURES", 5)
    LOGIN_MAX_FAILURES_PER_IP = _int("LOGIN_MAX_FAILURES_PER_IP", 30)
    LOGIN_WINDOW_MINUTES = _int("LOGIN_WINDOW_MINUTES", 15)
    PASSWORD_MIN_LENGTH = _int("PASSWORD_MIN_LENGTH", 12)
    PASSWORD_RESET_TTL_MINUTES = _int("PASSWORD_RESET_TTL_MINUTES", 60)
    INVITATION_TTL_DAYS = _int("INVITATION_TTL_DAYS", 7)
    SELF_SIGNUP_ENABLED = _bool("SELF_SIGNUP_ENABLED", False)

    # Emails
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "smtp")  # smtp | console | memory
    SMTP_HOST = os.environ.get("SMTP_HOST", "localhost")
    SMTP_PORT = _int("SMTP_PORT", 587)
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_USE_TLS = _bool("SMTP_USE_TLS", True)
    SMTP_USE_SSL = _bool("SMTP_USE_SSL", False)
    MAIL_FROM = os.environ.get("MAIL_FROM", "LabQualité <no-reply@example.org>")

    # Fichiers
    STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "local")  # local | s3
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.abspath("uploads"))
    MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 15)
    S3_BUCKET = os.environ.get("S3_BUCKET", "")
    S3_ENDPOINT_URL = os.environ.get("S3_ENDPOINT_URL", "")
    S3_REGION = os.environ.get("S3_REGION", "fr-par")
    S3_ACCESS_KEY_ID = os.environ.get("S3_ACCESS_KEY_ID", "")
    S3_SECRET_ACCESS_KEY = os.environ.get("S3_SECRET_ACCESS_KEY", "")
    S3_PRESIGN_SECONDS = _int("S3_PRESIGN_SECONDS", 60)

    # Stripe
    STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "")
    STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "")
    STRIPE_PRICE_MONTHLY = os.environ.get("STRIPE_PRICE_MONTHLY", "")
    STRIPE_PRICE_YEARLY = os.environ.get("STRIPE_PRICE_YEARLY", "")
    STRIPE_TAX_ENABLED = _bool("STRIPE_TAX_ENABLED", False)
    VAT_EXEMPTION_MENTION = os.environ.get(
        "VAT_EXEMPTION_MENTION", "TVA non applicable, art. 293 B du CGI"
    )
    INVOICE_ISSUER_NAME = os.environ.get("INVOICE_ISSUER_NAME", "")
    INVOICE_ISSUER_SIRET = os.environ.get("INVOICE_ISSUER_SIRET", "")
    INVOICE_ISSUER_ADDRESS = os.environ.get("INVOICE_ISSUER_ADDRESS", "")
    TRIAL_DAYS = _int("TRIAL_DAYS", 30)
    GRACE_DAYS = _int("GRACE_DAYS", 14)
    DEFAULT_MAX_ACTIVE_USERS = _int("DEFAULT_MAX_ACTIVE_USERS", 15)

    # Divers
    ITEMS_PER_PAGE = _int("ITEMS_PER_PAGE", 25)
    LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
    LOG_JSON = _bool("LOG_JSON", True)
    SHOW_DEMO_CREDENTIALS = False


class DevelopmentConfig(BaseConfig):
    ENV_NAME = "development"
    DEBUG = True
    LOG_JSON = _bool("LOG_JSON", False)
    MAIL_BACKEND = os.environ.get("MAIL_BACKEND", "console")
    SHOW_DEMO_CREDENTIALS = True


class TestConfig(BaseConfig):
    ENV_NAME = "test"
    TESTING = True
    SECRET_KEY = "cle-de-test-non-secrete"
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "TEST_DATABASE_URL", "postgresql+psycopg://labq_app:app@localhost:5432/labqualite_test"
    )
    DATABASE_OWNER_URL = os.environ.get(
        "TEST_DATABASE_OWNER_URL",
        "postgresql+psycopg://labq_owner:owner@localhost:5432/labqualite_test",
    )
    DB_APP_ROLE = os.environ.get("TEST_DB_APP_ROLE", "labq_app")
    # La protection CSRF est activée ponctuellement dans le test dédié.
    WTF_CSRF_ENABLED = False
    MAIL_BACKEND = "memory"
    LOG_JSON = False
    STRIPE_WEBHOOK_SECRET = "whsec_test_secret"
    STRIPE_PRICE_MONTHLY = "price_test_monthly"
    STRIPE_PRICE_YEARLY = "price_test_yearly"
    SERVER_NAME = "localhost"


class ProductionConfig(BaseConfig):
    ENV_NAME = "production"
    SESSION_COOKIE_SECURE = True
    REMEMBER_COOKIE_SECURE = True
    HSTS_ENABLED = True
    PREFERRED_URL_SCHEME = "https"


CONFIGS = {
    "development": DevelopmentConfig,
    "test": TestConfig,
    "production": ProductionConfig,
}


def get_config(name: str | None) -> type[BaseConfig]:
    name = name or os.environ.get("APP_ENV", "development")
    if name not in CONFIGS:
        raise RuntimeError(f"Environnement inconnu : {name}")
    return CONFIGS[name]
