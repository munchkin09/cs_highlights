#!/usr/bin/env bash
# Shared by the SteamOS entry points; does not modify the caller's shell files.

CSH_PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CSH_STEAMOS_HOME="${CSH_STEAMOS_HOME:-$HOME/.local/share/cs-highlights/steamos}"
CSH_RUNTIME="$CSH_STEAMOS_HOME/runtime"
CSH_CSDM_VERSION=3.20.1
CSH_CSDM_DIR="$CSH_STEAMOS_HOME/csdm-$CSH_CSDM_VERSION"
CSH_PGDATA="$CSH_STEAMOS_HOME/postgres"
CSH_PASSWORD_FILE="$CSH_STEAMOS_HOME/postgres-password"

csh_die() { printf 'Error: %s\n' "$*" >&2; exit 1; }

csh_check_host() {
    [[ "$(uname -s)" == Linux && "$(uname -m)" == x86_64 ]] ||
        csh_die 'Estos scripts requieren SteamOS en Linux x86_64.'
    [[ $EUID -ne 0 ]] || csh_die 'Ejecuta como tu usuario del escritorio, sin sudo.'
    [[ "$CSH_STEAMOS_HOME" == /* && "$CSH_STEAMOS_HOME" != / ]] ||
        csh_die 'CSH_STEAMOS_HOME debe ser una ruta absoluta distinta de /.'
}

csh_require_runtime() {
    [[ -f "$CSH_RUNTIME/.csh-ready" && -x "$CSH_RUNTIME/bin/python" ]] ||
        csh_die 'Primero ejecuta ./scripts/install-steamos.sh.'
    # Do not export LD_LIBRARY_PATH: it would also affect Steam and CS2.
    export PATH="$CSH_RUNTIME/bin:$CSH_CSDM_DIR:$PATH"
    export PYTHONNOUSERSITE=1
    unset PYTHONHOME PYTHONPATH
}

csh_prepare_csdm() {
    local directory="$1"
    local file
    for file in csdm AppRun cs-demo-manager resources/app.asar; do
        [[ -f "$directory/$file" ]] || csh_die "La AppImage está incompleta: falta $directory/$file."
    done
    [[ -x "$directory/AppRun" && -x "$directory/cs-demo-manager" ]] ||
        csh_die "AppRun o cs-demo-manager no se pueden ejecutar en $directory. Revisa los permisos y el disco."
    # The verified v3.20.1 AppImage ships this shell launcher as 0644.
    chmod u+x "$directory/csdm"
    [[ -x "$directory/csdm" ]] || csh_die "No se puede ejecutar $directory/csdm. Revisa si el disco usa noexec."
}

csh_database() {
    bash "$CSH_PROJECT_ROOT/scripts/steamos-postgres.sh" "$@"
}
