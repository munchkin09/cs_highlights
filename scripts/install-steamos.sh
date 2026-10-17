#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/steamos-common.sh"

case "${1:-}" in
    -h|--help)
        cat <<'HELP'
Uso: ./scripts/install-steamos.sh [--check]

Instala Python 3.12, FFmpeg 7 (H.264/AAC), PostgreSQL 17 y CS Demo Manager
3.20.1 en ~/.local/share/cs-highlights/steamos. No necesita sudo.
--check comprueba el sistema y muestra el plan sin descargar ni escribir.
CSH_STEAMOS_HOME permite elegir otra carpeta absoluta en un disco Linux.
HELP
        exit 0 ;;
    ''|--check) ;;
    *) csh_die "Opción desconocida: $1 (consulta --help)." ;;
esac
[[ $# -le 1 ]] || csh_die 'Demasiados argumentos (consulta --help).'
csh_check_host
[[ -r /etc/os-release ]] || csh_die 'No se encuentra /etc/os-release.'
os_id="$(. /etc/os-release; printf '%s' "${ID:-}")"
[[ "$os_id" == steamos ]] || csh_die "Este instalador está dirigido a SteamOS; sistema detectado: $os_id."
for command in curl sha256sum flock mktemp; do
    command -v "$command" >/dev/null || csh_die "Falta la herramienta del sistema: $command."
done
printf 'Destino: %s\nProyecto: %s\n' "$CSH_STEAMOS_HOME" "$CSH_PROJECT_ROOT"
printf '%s\n' 'Dependencias: Python 3.12, FFmpeg 7 GPL, PostgreSQL 17, CS Demo Manager 3.20.1.'
[[ "${1:-}" != --check ]] || exit 0

umask 077
mkdir -p "$CSH_STEAMOS_HOME"
exec 8>"$CSH_STEAMOS_HOME/install.lock"
flock -n 8 || csh_die 'Ya hay otra instalación en curso.'
scratch="$(mktemp -d "$CSH_STEAMOS_HOME/.install.XXXXXX")"
trap 'rm -rf -- "$scratch"' EXIT

download() {
    local url="$1" expected="$2" output="$3"
    curl --fail --location --retry 3 --connect-timeout 30 --proto '=https' --proto-redir '=https' \
        "$url" --output "$output"
    printf '%s  %s\n' "$expected" "$output" | sha256sum --check --status ||
        csh_die "Checksum incorrecto: $url. No se ejecutará el archivo."
}

# SHA-256 from the official GitHub release assets. No remote shell installer.
mamba_version=2.9.0-0
mamba_sha=366cd9cd8be14df1ab8ed50352a82111082a36686b2d389fdb79a92c3fafb3e3
mamba="$CSH_STEAMOS_HOME/micromamba"
if [[ ! -f "$mamba" ]] || ! printf '%s  %s\n' "$mamba_sha" "$mamba" | sha256sum --check --status; then
    download "https://github.com/mamba-org/micromamba-releases/releases/download/$mamba_version/micromamba-linux-64" \
        "$mamba_sha" "$scratch/micromamba"
    chmod 700 "$scratch/micromamba"
    mv -- "$scratch/micromamba" "$mamba"
fi

if [[ ! -f "$CSH_RUNTIME/.csh-ready" ]]; then
    [[ ! -f "$CSH_PGDATA/PG_VERSION" ]] ||
        csh_die 'Existe una base de datos pero falta el runtime completo. Conserva postgres y revisa la instalación antes de reparar.'
    operation=create
    [[ ! -f "$CSH_RUNTIME/conda-meta/history" ]] || operation=install
    "$mamba" --no-rc --root-prefix "$CSH_STEAMOS_HOME/mamba" "$operation" --yes \
        --prefix "$CSH_RUNTIME" --override-channels --channel conda-forge --strict-channel-priority \
        'python=3.12' pip wheel 'setuptools>=68' 'ffmpeg=7.*=gpl*' 'postgresql=17.*'
    "$CSH_RUNTIME/bin/python" -c 'import sys; assert sys.version_info >= (3, 12)'
    "$CSH_RUNTIME/bin/psql" --version
    "$CSH_RUNTIME/bin/pg_ctl" --version
    # Exercise the encoders, not just the presence of an executable.
    "$CSH_RUNTIME/bin/ffmpeg" -v error -f lavfi -i color=size=64x64:rate=10 \
        -f lavfi -i anullsrc=r=48000:cl=stereo -t 0.2 -c:v libx264 -pix_fmt yuv420p \
        -c:a aac "$scratch/codecs.mp4"
    "$CSH_RUNTIME/bin/ffprobe" -v error -show_entries format=duration "$scratch/codecs.mp4"
    "$mamba" --no-rc list --prefix "$CSH_RUNTIME" --explicit > "$CSH_STEAMOS_HOME/runtime-packages.txt"
    touch "$CSH_RUNTIME/.csh-ready"
fi
csh_require_runtime
"$CSH_RUNTIME/bin/python" -m pip install --no-deps --no-build-isolation --editable "$CSH_PROJECT_ROOT"

if [[ ! -x "$CSH_CSDM_DIR/csdm" || ! -x "$CSH_CSDM_DIR/AppRun" ]]; then
    [[ ! -e "$CSH_CSDM_DIR" ]] || csh_die "Instalación incompleta en $CSH_CSDM_DIR; muévela antes de reintentar."
    download "https://github.com/akiver/cs-demo-manager/releases/download/v$CSH_CSDM_VERSION/CS-Demo-Manager-$CSH_CSDM_VERSION.AppImage" \
        95608b6c4aa28d0328a0212bebe0b2ee1989b215e817bb7b8d0042a2655c5115 "$scratch/csdm.AppImage"
    chmod 700 "$scratch/csdm.AppImage"
    # Extraction avoids requiring FUSE or a writable system partition.
    (cd "$scratch" && ./csdm.AppImage --appimage-extract > extraction.log)
    [[ -x "$scratch/squashfs-root/csdm" && -x "$scratch/squashfs-root/AppRun" ]] ||
        csh_die 'La AppImage no contiene los ejecutables esperados.'
    mv -- "$scratch/squashfs-root" "$CSH_CSDM_DIR"
fi
"$CSH_CSDM_DIR/csdm" help > "$scratch/csdm-help.txt"
csh_database init

"$CSH_RUNTIME/bin/python" - "$CSH_PROJECT_ROOT" "$CSH_RUNTIME" "$CSH_CSDM_DIR" <<'PY'
import json
from pathlib import Path
import re
import sys

project, runtime, csdm = map(Path, sys.argv[1:])
target = project / 'config.steamos.toml'
content = (project / 'config.example.toml').read_text()
for name, value in {'csdm': csdm / 'csdm', 'ffmpeg': runtime / 'bin/ffmpeg',
                    'ffprobe': runtime / 'bin/ffprobe', 'psql': runtime / 'bin/psql'}.items():
    content = re.sub(rf'^{name} = .*$', lambda _: f'{name} = {json.dumps(str(value), ensure_ascii=False)}', content, flags=re.M)
try:
    with target.open('x') as output:
        output.write(content)
    print(f'Creado {target}; edita player.steam_id y paths.cs2_dir.')
except FileExistsError:
    print(f'Se conserva {target}.')
PY

cat <<EOF

Instalación terminada. Configuración inicial pendiente:
1. Instala CS2 desde Steam, ábrelo una vez y ciérralo. Mantén Steam conectado.
2. Edita $CSH_PROJECT_ROOT/config.steamos.toml (SteamID64 y ruta de CS2).
3. ./scripts/steamos.sh db credentials    # datos para conectar CS Demo Manager
4. ./scripts/steamos.sh csdm              # abre la aplicación y arranca PostgreSQL
   Conecta a 127.0.0.1:55432, usuario cs_highlights, base csdm.
   En Video configura FFmpeg: $CSH_RUNTIME/bin/ffmpeg
   En Playback configura el game/cs2.sh de tu instalación.
5. ./scripts/steamos.sh doctor
6. ./scripts/steamos.sh smoke

Instrucciones completas: docs/steamos.md
EOF
