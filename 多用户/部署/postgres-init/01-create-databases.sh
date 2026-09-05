#!/bin/sh
set -eu

: "${LITELLM_DB_PASSWORD:?LITELLM_DB_PASSWORD is required}"
: "${SEARCH_DB_PASSWORD:?SEARCH_DB_PASSWORD is required}"

psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
  --set=litellm_password="$LITELLM_DB_PASSWORD" \
  --set=search_password="$SEARCH_DB_PASSWORD" <<'EOSQL'
SELECT format('CREATE ROLE litellm LOGIN PASSWORD %L', :'litellm_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'litellm') \gexec
SELECT format('CREATE ROLE xj_search LOGIN PASSWORD %L', :'search_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'xj_search') \gexec
SELECT 'CREATE DATABASE litellm OWNER litellm'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'litellm') \gexec
SELECT 'CREATE DATABASE xj_search OWNER xj_search'
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = 'xj_search') \gexec
REVOKE ALL PRIVILEGES ON DATABASE litellm FROM PUBLIC;
REVOKE ALL PRIVILEGES ON DATABASE xj_search FROM PUBLIC;
EOSQL
