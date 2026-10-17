#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/steamos-common.sh"

case "${1:-}" in
    init|start|stop|status|credentials) ;;
    *) printf 'Uso: ./scripts/steamos.sh db {start|stop|status|credentials}\n'; exit 2 ;;
esac
[[ $# -eq 1 ]] || csh_die 'La operación de base de datos no admite más argumentos.'
csh_check_host
csh_require_runtime
umask 077
exec 9>"$CSH_STEAMOS_HOME/postgres.lock"
flock -x 9

if [[ "$1" == init ]]; then
    if [[ ! -f "$CSH_PGDATA/PG_VERSION" ]]; then
        [[ ! -e "$CSH_PGDATA" ]] || csh_die "Existe $CSH_PGDATA sin PG_VERSION; no se sobrescribe."
        if [[ ! -f "$CSH_PASSWORD_FILE" ]]; then
            "$CSH_RUNTIME/bin/python" -c 'import secrets; print(secrets.token_hex(24))' > "$CSH_PASSWORD_FILE"
        fi
        [[ -s "$CSH_PASSWORD_FILE" ]] || csh_die "La contraseña está vacía: $CSH_PASSWORD_FILE."
        chmod 600 "$CSH_PASSWORD_FILE"
        staging="$(mktemp -d "$CSH_STEAMOS_HOME/.postgres-init.XXXXXX")"
        trap 'rm -rf -- "$staging"' EXIT
        "$CSH_RUNTIME/bin/initdb" -D "$staging" --username=cs_highlights \
            --pwfile="$CSH_PASSWORD_FILE" --auth-host=scram-sha-256 --auth-local=scram-sha-256 \
            --encoding=UTF8 --locale=C
        cat >> "$staging/postgresql.conf" <<'CONF'

# CS Highlights: private instance, TCP loopback only.
listen_addresses = '127.0.0.1'
port = 55432
unix_socket_directories = ''
CONF
        mv -- "$staging" "$CSH_PGDATA"
    fi
fi

[[ -f "$CSH_PGDATA/PG_VERSION" && "$(cat "$CSH_PGDATA/PG_VERSION")" == 17 ]] ||
    csh_die 'No existe el clúster PostgreSQL 17 esperado. Ejecuta el instalador; no se reinicializa una base existente.'
[[ -s "$CSH_PASSWORD_FILE" ]] || csh_die "Falta la contraseña en $CSH_PASSWORD_FILE; no se genera otra para la base existente."

case "$1" in
    init) printf 'PostgreSQL inicializado; se conserva cualquier base existente.\n' ;;
    start)
        if ! "$CSH_RUNTIME/bin/pg_ctl" -D "$CSH_PGDATA" status >/dev/null 2>&1; then
            # Close the lock FD in the server so subsequent commands can acquire it.
            "$CSH_RUNTIME/bin/pg_ctl" -D "$CSH_PGDATA" -l "$CSH_STEAMOS_HOME/postgres.log" \
                -w -t 30 start 9>&- || csh_die "No se pudo arrancar PostgreSQL; consulta $CSH_STEAMOS_HOME/postgres.log (puerto 55432)."
        fi
        # CSDM creates its schema; only create the empty database here.
        for variable in "${!PG@}"; do unset "$variable"; done
        export PGHOST=127.0.0.1 PGPORT=55432 PGUSER=cs_highlights PGDATABASE=postgres PGCONNECT_TIMEOUT=5
        export PGPASSWORD="$(cat "$CSH_PASSWORD_FILE")"
        exists="$("$CSH_RUNTIME/bin/psql" -X -w -A -t -v ON_ERROR_STOP=1 -c "SELECT 1 FROM pg_database WHERE datname = 'csdm'")"
        if [[ "$exists" != 1 ]]; then
            "$CSH_RUNTIME/bin/psql" -X -w -v ON_ERROR_STOP=1 -c 'CREATE DATABASE csdm'
        fi ;;
    stop)
        if "$CSH_RUNTIME/bin/pg_ctl" -D "$CSH_PGDATA" status >/dev/null 2>&1; then
            "$CSH_RUNTIME/bin/pg_ctl" -D "$CSH_PGDATA" -w -t 30 stop -m fast
        else
            printf 'PostgreSQL ya está parado.\n'
        fi ;;
    status) "$CSH_RUNTIME/bin/pg_ctl" -D "$CSH_PGDATA" status ;;
    credentials)
        printf 'Servidor: 127.0.0.1\nPuerto: 55432\nUsuario: cs_highlights\nBase de datos: csdm\nContraseña: '
        cat "$CSH_PASSWORD_FILE" ;;
esac
