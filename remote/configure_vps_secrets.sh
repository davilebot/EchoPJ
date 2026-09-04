#!/bin/sh
set -eu

secrets_dir=/srv/plataforma-receita
env_file="$secrets_dir/app.env"
access_file="$secrets_dir/access.txt"
data_dir="$secrets_dir/data"
easypanel_secret_dir=/etc/easypanel/secrets/plataforma-receita
easypanel_env_file="$easypanel_secret_dir/app.env"

install -d -m 700 "$secrets_dir"
install -d -m 700 "$data_dir"
install -d -m 700 "$easypanel_secret_dir"
umask 077

if [ -f "$env_file" ]; then
  install -m 600 "$env_file" "$easypanel_env_file"
  echo "Configuracao existente preservada."
  exit 0
fi

db_password=$(openssl rand -hex 32)
app_password=$(openssl rand -hex 20)

{
  printf "SELECT format('CREATE ROLE plataforma_receita_ro LOGIN PASSWORD %%L', '%s') WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='plataforma_receita_ro') \\gexec\n" "$db_password"
  printf "ALTER ROLE plataforma_receita_ro PASSWORD '%s';\n" "$db_password"
  printf "ALTER ROLE plataforma_receita_ro SET default_transaction_read_only = on;\n"
  printf "GRANT CONNECT ON DATABASE cnpj TO plataforma_receita_ro;\n"
  printf "GRANT USAGE ON SCHEMA public TO plataforma_receita_ro;\n"
  printf "GRANT SELECT ON ALL TABLES IN SCHEMA public TO plataforma_receita_ro;\n"
  printf "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO plataforma_receita_ro;\n"
} | docker exec -i radar-receita_plataforma-postgres-1 sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

{
  printf "POSTGRES_DSN=postgresql://plataforma_receita_ro:%s@postgres:5432/cnpj\n" "$db_password"
  printf "APP_USERNAME=admin\n"
  printf "APP_PASSWORD=%s\n" "$app_password"
  printf "WEBSITE_CACHE_PATH=/data/website-cache.sqlite\n"
  printf "WEBSITE_TIMEOUT_SECONDS=3\n"
  printf "WEBSITE_WORKERS=10\n"
  printf "DATABASE_WORKERS=8\n"
  printf "DATABASE_STATEMENT_TIMEOUT_MS=1800\n"
  printf "MAX_BATCH_SIZE=200\n"
  printf "MAX_JOB_SIZE=10000\n"
  printf "JOB_DATABASE_PATH=/data/jobs.sqlite\n"
  printf "AUTH_DATABASE_PATH=/data/auth.sqlite\n"
  printf "AUTH_SESSION_DAYS=30\n"
} > "$env_file"
install -m 600 "$env_file" "$easypanel_env_file"

{
  printf "Plataforma Receita\n"
  printf "URL: https://plataforma-receita-matcher.ztnbow.easypanel.host/\n"
  printf "Usuario: admin\n"
  printf "Senha: %s\n" "$app_password"
  printf "Mantenha este arquivo privado.\n"
} > "$access_file"

chmod 600 "$env_file" "$access_file"
echo "Credenciais e usuario somente leitura configurados."
