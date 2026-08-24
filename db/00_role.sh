#!/bin/bash
# ============================================================================
# The app role — created with a password from the ENVIRONMENT, never a literal.
#
# This replaces the hard-coded password in 00_role.sql. That file shipped
# `CREATE ROLE techdeva_app LOGIN PASSWORD 'techdeva'`, so every deployment that
# ever ran it shares one password that is also published in the repository. With
# Postgres reachable from the internet, that is the whole database.
#
# Postgres runs *.sh from docker-entrypoint-initdb.d the same way it runs *.sql,
# so this executes on first database init and can read env vars — which .sql
# files cannot.
#
#   APP_DB_PASSWORD   required. No default, on purpose: a missing value fails the
#                     init loudly instead of silently recreating the known one.
#
# Generate one with:  openssl rand -base64 32
# For an EXISTING database, rotate instead — see SECURITY.md.
# ============================================================================
set -euo pipefail

if [ -z "${APP_DB_PASSWORD:-}" ]; then
  echo "FATAL: APP_DB_PASSWORD is not set. Refusing to create techdeva_app with a" >&2
  echo "       default password. Set it in .env (openssl rand -base64 32)." >&2
  exit 1
fi

# Note on the CREATE ROLE below: psql does NOT interpolate :variables inside a
# dollar-quoted DO block, so the password is built with format(%L) in a plain
# SELECT and executed with \gexec. %L quotes it correctly whatever characters it
# contains.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
     -v app_password="$APP_DB_PASSWORD" <<-'EOSQL'
	SELECT format('CREATE ROLE techdeva_app LOGIN PASSWORD %L', :'app_password')
	WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'techdeva_app')
	\gexec
	GRANT USAGE ON SCHEMA public TO techdeva_app;
	ALTER DEFAULT PRIVILEGES IN SCHEMA public
	  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO techdeva_app;
EOSQL

echo "techdeva_app created with the password from APP_DB_PASSWORD"
