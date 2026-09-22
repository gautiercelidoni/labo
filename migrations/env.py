"""Environnement Alembic.

Les migrations s'exécutent avec le rôle PROPRIÉTAIRE (DATABASE_OWNER_URL) et non avec le rôle
applicatif. Après chaque migration, les droits du rôle applicatif (DB_APP_ROLE) sont réappliqués :
lecture/écriture sur les tables métier, mais seulement INSERT et SELECT sur audit_event.
"""
import logging
from logging.config import fileConfig

import sqlalchemy as sa
from alembic import context
from flask import current_app

config = context.config
fileConfig(config.config_file_name)
logger = logging.getLogger("alembic.env")

target_db = current_app.extensions["migrate"].db


def get_url() -> str:
    owner_url = current_app.config.get("DATABASE_OWNER_URL")
    if owner_url:
        return owner_url
    return target_db.engine.url.render_as_string(hide_password=False)


def get_metadata():
    if hasattr(target_db, "metadatas"):
        return target_db.metadatas[None]
    return target_db.metadata


def apply_app_role_grants(connection) -> None:
    role = current_app.config.get("DB_APP_ROLE")
    if not role:
        return
    quoted = connection.dialect.identifier_preparer.quote(role)
    statements = [
        f"GRANT USAGE ON SCHEMA public TO {quoted}",
        f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {quoted}",
        f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {quoted}",
        # Journal d'audit : ajout et lecture uniquement.
        f"REVOKE UPDATE, DELETE, TRUNCATE ON audit_event FROM {quoted}",
        f"REVOKE ALL ON alembic_version FROM {quoted}",
        f"GRANT SELECT ON alembic_version TO {quoted}",
    ]
    for statement in statements:
        connection.execute(sa.text(statement))
    logger.info("Droits appliqués au rôle applicatif %s", role)


def run_migrations_offline():
    context.configure(url=get_url(), target_metadata=get_metadata(), literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    def process_revision_directives(context, revision, directives):
        if getattr(config.cmd_opts, "autogenerate", False):
            script = directives[0]
            if script.upgrade_ops.is_empty():
                directives[:] = []
                logger.info("Aucune modification de schéma détectée.")

    conf_args = current_app.extensions["migrate"].configure_args
    if conf_args.get("process_revision_directives") is None:
        conf_args["process_revision_directives"] = process_revision_directives

    engine = sa.create_engine(get_url(), poolclass=sa.pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=get_metadata(), **conf_args)
        with context.begin_transaction():
            context.run_migrations()
            if connection.dialect.has_table(connection, "audit_event"):
                apply_app_role_grants(connection)
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
