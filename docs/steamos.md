# Instalación en SteamOS (Steam Machine)

Estos scripts están dirigidos a **SteamOS x86_64**, con Steam nativo y una sesión de escritorio. Instala CS2 desde Steam, ábrelo una vez para generar su configuración y ciérralo. Mantén Steam abierto y conectado a tu cuenta durante las capturas.

## Instalar las dependencias

Guarda el proyecto en una carpeta de tu usuario dentro de un disco Linux (por ejemplo `~/Projects/cs_highlights`). En SteamOS cambia a **modo Escritorio**, abre Konsole y entra en esa carpeta:

```bash
cd ~/Projects/cs_highlights
./scripts/install-steamos.sh --check
./scripts/install-steamos.sh
```

`--check` comprueba plataforma y herramientas básicas sin escribir ni descargar. La instalación requiere Internet, `bash`, `curl`, `sha256sum` y `flock`, herramientas del sistema. Se ejecuta **sin sudo**. Reserva varios GiB para el entorno y su caché, además del espacio para demos y capturas que explica el README.

Se instala bajo `~/.local/share/cs-highlights/steamos`:

| Componente | Instalación |
| --- | --- |
| micromamba 2.9.0-0 | Binario oficial con SHA-256 fijado en el script |
| Python 3.12, pip, setuptools | Entorno independiente de conda-forge |
| FFmpeg 7 GPL y ffprobe | Incluye libx264 y AAC; se prueba una codificación breve |
| PostgreSQL 17 y psql | Instancia privada, contraseña aleatoria, autenticación SCRAM |
| CS Demo Manager 3.20.1 | AppImage oficial verificada y extraída, sin necesitar FUSE |
| CLI del proyecto | Instalación editable en ese Python; conserva el proyecto en su ubicación |

Las versiones mayores de Python, FFmpeg y PostgreSQL están acotadas; las revisiones y dependencias se resuelven al instalar. `runtime-packages.txt` registra los paquetes exactos. Las descargas del entorno también pasan por las comprobaciones de integridad de micromamba.

Los scripts escriben dentro de su carpeta de instalación y crean `config.steamos.toml` en el proyecto. No modifican `.bashrc`, los paquetes de SteamOS, la `.venv` del proyecto ni una instalación previa de CS Demo Manager. Tampoco necesitan Docker, Flatpak ni desactivar el modo de solo lectura. La base queda inicializada pero parada al acabar el instalador.

## Configurar tu cuenta y CS Demo Manager

1. Edita `config.steamos.toml`: `player.steam_id` debe ser tu **SteamID64 de 17 dígitos entre comillas**. `paths.cs2_dir` es la raíz que contiene `game/cs2.sh`. Si instalaste CS2 en otro disco o biblioteca, cambia esa ruta.
2. Muestra los datos de conexión y abre CS Demo Manager:

   ```bash
   ./scripts/steamos.sh db credentials
   ./scripts/steamos.sh csdm
   ```

3. En CS Demo Manager introduce servidor `127.0.0.1`, puerto `55432`, usuario `cs_highlights`, base `csdm` y la contraseña del comando anterior. Si aparece un selector de modo, elige **external**. La aplicación crea su esquema al conectar. Las credenciales quedan en su configuración habitual; el instalador no edita esa configuración.
4. En **Video**, habilita la ubicación personalizada de FFmpeg y selecciona `/home/TU_USUARIO/.local/share/cs-highlights/steamos/runtime/bin/ffmpeg`. Usa la ruta absoluta que imprime el instalador; sustituye `TU_USUARIO` por tu usuario real. Esto también es necesario aunque el TOML ya tenga la ruta.
5. En **Playback**, habilita la ubicación personalizada de CS2 y selecciona el `game/cs2.sh` de la misma instalación que pusiste en el TOML. Comprueba que la aplicación puede reproducir una demo y después cierra CS2.

Abre esta copia de CS Demo Manager mediante `./scripts/steamos.sh csdm` para que encuentre su `psql`. Si tenías otra copia abierta, ciérrala completamente, incluido su icono de la bandeja, antes de usar el lanzador.

## Ejecutar y parar

Desde el proyecto:

```bash
./scripts/steamos.sh doctor
./scripts/steamos.sh smoke
./scripts/steamos.sh run
./scripts/steamos.sh process /ruta/partida.dem
```

`doctor`, `smoke`, `run`, `process` y `csdm` preparan el `PATH` y arrancan PostgreSQL si hace falta. Siempre usan `config.steamos.toml` de la carpeta del proyecto, incluso al ejecutar el script desde otra carpeta. La prueba `smoke` admite también una ruta de demo. Revisa visualmente su MP4 antes de procesar partidas completas.

```bash
./scripts/steamos.sh db status
./scripts/steamos.sh db stop
./scripts/steamos.sh db start
```

La base sigue funcionando después de cerrar la CLI hasta que la pares o apagues el equipo. No se configura un servicio ni arranque automático al iniciar sesión. Para pararla, termina primero las capturas y cierra CS Demo Manager: `db stop` desconecta los clientes de esta instancia. Nunca gestiona otras instalaciones de PostgreSQL.

La base escucha únicamente en `127.0.0.1:55432`. Su contraseña está en `postgres-password`, con permisos de lectura solo para tu usuario. Solo `db credentials` la muestra en la terminal. Los datos persistentes están en `postgres/` y los mensajes del servidor en `postgres.log`.

## Repetir, personalizar y resolver problemas

Puedes repetir `./scripts/install-steamos.sh`: reutiliza el entorno completo y la AppImage extraída, reinstala la CLI editable y conserva el TOML, la contraseña y el clúster existentes. **No actualiza PostgreSQL a otra versión mayor ni borra datos.** Una instalación parcial del entorno se puede reintentar mientras no exista un clúster; si encuentra estado incompatible, se detiene con su ubicación para evitar sobrescribirlo.

Para usar otra carpeta de instalación, establece la misma variable tanto al instalar como al ejecutar:

```bash
export CSH_STEAMOS_HOME="$HOME/Applications/cs-highlights"
./scripts/install-steamos.sh
./scripts/steamos.sh doctor
```

Usa una ruta absoluta en un disco con permisos de ejecución y enlaces simbólicos; evita exFAT/NTFS. La variable solo dura en la terminal actual. El entorno no se puede mover una vez creado. Si cambias la ubicación, revisa las rutas de `config.steamos.toml` y FFmpeg en CS Demo Manager; los archivos existentes se conservan.

Puedes seleccionar otro TOML con `CSH_CONFIG`:

```bash
CSH_CONFIG="$HOME/mis-partidas.toml" ./scripts/steamos.sh doctor
```

- **El puerto 55432 está ocupado:** consulta `postgres.log` e identifica el proceso que lo usa. Los scripts no detienen procesos ajenos ni conectan deliberadamente a otra base.
- **Error de checksum:** la descarga se descarta sin ejecutarse; reintenta. No omitas la verificación.
- **«La AppImage no contiene los ejecutables esperados» con el instalador anterior:** la AppImage oficial 3.20.1 incluye `csdm` con permisos `0644`. El instalador actualizado le añade permiso de ejecución. Copia los scripts actualizados al equipo y repite `./scripts/install-steamos.sh`; reutilizará el entorno ya instalado. Si faltan archivos de verdad, ahora indica su ruta concreta.
- **Faltan bibliotecas al ejecutar CS Demo Manager o falla el sandbox de Electron:** conserva el error y comprueba las actualizaciones de SteamOS en sus ajustes. Los scripts no desactivan el sandbox ni instalan paquetes en la raíz. La AppImage depende de las bibliotecas gráficas del escritorio.
- **`doctor` falla:** revisa sus diagnósticos de Steam, CS2, sesión gráfica, rutas, base y FFmpeg. Instalar dependencias no autentica Steam ni configura el juego por ti.
- **Has copiado el proyecto desde macOS:** la `.venv` no es portable; estos scripts usan su propio Python para Linux. Si los scripts perdieron permisos, ejecuta `bash scripts/install-steamos.sh` y `bash scripts/steamos.sh doctor`.
- **Quieres quitarlo:** para primero la base, guarda una copia de `postgres/`, `postgres-password` y tus vídeos, y elimina manualmente la carpeta de instalación cuando ya no necesites esos datos. No se incluye un borrado automático.

## Alcance de la validación y referencias

Los scripts se comprueban con análisis de sintaxis y pruebas con ejecutables simulados. Este entorno de desarrollo es macOS: todavía falta instalar las dependencias y ejecutar una captura real en SteamOS. La comprobación de códecs sí se ejecuta durante la instalación en tu máquina; `doctor` y `smoke` completan la comprobación local de la integración.

- [Instalación de micromamba](https://mamba.readthedocs.io/en/latest/installation/micromamba-installation.html) y [release fijada](https://github.com/mamba-org/micromamba-releases/releases/tag/2.9.0-0).
- [Instalación de CS Demo Manager](https://cs-demo-manager.com/docs/installation), [CLI](https://cs-demo-manager.com/docs/cli) y [release fijada](https://github.com/akiver/cs-demo-manager/releases/tag/v3.20.1).
- [PostgreSQL en conda-forge](https://anaconda.org/conda-forge/postgresql).
