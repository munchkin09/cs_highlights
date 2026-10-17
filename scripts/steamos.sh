#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/steamos-common.sh"

case "${1:---help}" in
    -h|--help)
        cat <<'HELP'
Uso: ./scripts/steamos.sh {doctor|run|process RUTA.dem|smoke [RUTA.dem]|csdm|db OPERACIÓN}

Prepara el entorno local y arranca PostgreSQL antes de ejecutar la aplicación.
db admite start, stop, status y credentials; no arranca servicios al consultar.
Config predeterminado: config.steamos.toml de la carpeta del proyecto.
CSH_CONFIG permite usar otro TOML. CSH_STEAMOS_HOME cambia la carpeta instalada.
HELP
        exit 0 ;;
    doctor|run|process|smoke|csdm|db) action="$1"; shift ;;
    *) csh_die "Comando desconocido: $1 (consulta --help)." ;;
esac
csh_check_host
csh_require_runtime

case "$action" in
    db) csh_database "$@"; exit ;;
    csdm)
        [[ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] || csh_die 'Abre una terminal en el modo Escritorio de SteamOS.'
        csh_database start
        exec "$CSH_CSDM_DIR/AppRun" "$@" ;;
esac

config="${CSH_CONFIG:-$CSH_PROJECT_ROOT/config.steamos.toml}"
[[ -f "$config" ]] || csh_die "No existe $config. Ejecuta el instalador."
csh_database start
if [[ "$action" == smoke ]]; then
    exec "$CSH_RUNTIME/bin/python" "$CSH_PROJECT_ROOT/scripts/smoke_linux.py" --config "$config" "$@"
fi
exec "$CSH_RUNTIME/bin/python" -m cs_highlights --config "$config" "$action" "$@"
