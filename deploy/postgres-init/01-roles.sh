#!/bin/sh
# Initialisation PostgreSQL (premier démarrage du volume uniquement) :
# - labq_owner : propriétaire du schéma, utilisé pour les migrations ;
# - labq_app   : rôle de l'application, sans droit DDL, INSERT/SELECT seulement sur l'audit.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<SQL
CREATE ROLE labq_owner LOGIN PASSWORD '${LABQ_OWNER_PASSWORD}';
CREATE ROLE labq_app LOGIN PASSWORD '${LABQ_APP_PASSWORD}';
CREATE DATABASE labqualite OWNER labq_owner ENCODING 'UTF8' TEMPLATE template0;
REVOKE ALL ON DATABASE labqualite FROM PUBLIC;
GRANT CONNECT ON DATABASE labqualite TO labq_app;
SQL
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname labqualite <<SQL
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO labq_owner;
SQL
