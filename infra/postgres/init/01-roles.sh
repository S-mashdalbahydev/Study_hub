#!/bin/sh
# Runs once, when the database volume is first created. Migrations create the schema;
# this only gives the two application roles their login passwords.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$STUDYHUB_APP_DB_PASSWORD" -v worker_password="$STUDYHUB_WORKER_DB_PASSWORD" <<'SQL'
CREATE ROLE studyhub_app LOGIN NOBYPASSRLS PASSWORD :'app_password';
CREATE ROLE studyhub_worker LOGIN NOBYPASSRLS PASSWORD :'worker_password';
SQL
