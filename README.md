# CS Highlights

PoC local: descarga y analiza demos en Windows, macOS y Linux, y genera **un MP4 con tus highlights por partida en Windows o Linux**. Python coordina CS Demo Manager y FFmpeg; el modo de análisis de muertes usa Pillow para dibujar los recorridos. No hay servicios web. macOS permite analizar y descargar, pero no capturar CS2, que no tiene versión nativa para ese sistema.

```text
Steam → CS Demo Manager → demo + análisis JSON → intervalos → CS2 → clips → MP4
```

## Windows, macOS y Linux

La [guía multiplataforma](docs/platforms.md) explica las dependencias externas y la configuración de cada sistema. Tras instalarlas, `python scripts/install.py` prepara la CLI y `config.local.toml`; `python scripts/run.py doctor` comprueba el entorno. Usa `py -3.12` en Windows o `python3.12` en macOS/Linux como intérprete.

Los comandos `analyze DEMO` y `download` funcionan sin CS2 instalado. `process DEMO` y `run` requieren el juego en Windows/Linux. En SteamOS utiliza los scripts del siguiente apartado.

## Steam Machine con SteamOS

En **modo Escritorio**, abre Konsole en la carpeta del proyecto y ejecuta, como tu usuario habitual:

```bash
./scripts/install-steamos.sh
./scripts/steamos.sh db credentials
./scripts/steamos.sh csdm
```

El instalador prepara Python 3.12, FFmpeg/ffprobe, PostgreSQL 17 y CS Demo Manager 3.20.1 en tu carpeta de usuario. Genera `config.steamos.toml` con las rutas de las herramientas; edita tu SteamID64 y la ruta de CS2. Conecta CS Demo Manager a la base local usando los datos de `db credentials` y configura FFmpeg y Playback como explica la [guía de SteamOS](docs/steamos.md).

```bash
./scripts/steamos.sh doctor
./scripts/steamos.sh smoke
./scripts/steamos.sh run
```

Para actualizar una instalación existente y probar la captura por lotes:

```bash
git pull --ff-only origin main
./scripts/install-steamos.sh
./scripts/steamos.sh doctor
./scripts/steamos.sh smoke --batch
```

`smoke --batch` divide un intervalo de hasta diez segundos en dos tramos y los graba en una sola sesión de CS2. Acepta también una demo local: `./scripts/steamos.sh smoke --batch /ruta/partida.dem`. Comprueba que el juego arranca una vez y revisa la transición y el audio del `smoke.mp4` indicado. `validation.json` conserva la duración de la sesión y deja la revisión visual pendiente. El instalador actualiza las dependencias Python (incluido Pillow para el minimapa) y conserva la configuración y la base existentes.

Los lanzadores preparan el entorno y arrancan la base automáticamente. La instalación no necesita `sudo`, no desactiva la protección de escritura de SteamOS y no usa la `.venv` copiada de otro equipo. Steam y CS2 se instalan desde Steam. Consulta la guía para parar PostgreSQL, repetir la instalación y resolver fallos. **Prueba real en SteamOS completada:** descarga de ocho demos, análisis y captura de ocho segundos en 1080p60 con H.264/AAC. Queda pendiente escuchar el vídeo para comprobar la sincronización del audio.

## Preparar el PC Linux

Necesitas un escritorio Linux con GPU capaz de ejecutar CS2, Steam **nativo**, CS2 instalado y una sesión de Steam iniciada. Abre CS2 una vez para crear su configuración de vídeo y después ciérralo. Ejecuta esta CLI desde el escritorio, sin otras grabaciones de CS Demo Manager en curso.

1. Instala Python 3.12 o posterior, FFmpeg/ffprobe y PostgreSQL **17 o posterior**, con su cliente `psql`. Sigue las instrucciones de [PostgreSQL para tu distribución](https://www.postgresql.org/download/linux/).
2. Instala [CS Demo Manager v3.20.1](https://github.com/akiver/cs-demo-manager/releases/tag/v3.20.1), abre la aplicación y configura su conexión a PostgreSQL. Si tu build ofrece base de datos integrada, selecciona **external** para esta PoC. Las credenciales permanecen en la configuración de CS Demo Manager. [Guía de instalación](https://cs-demo-manager.com/docs/installation).
3. En CS Demo Manager, instala FFmpeg desde Video o configura su ubicación personalizada, por ejemplo `/usr/bin/ffmpeg`. El programa comprueba su propia instalación aunque la CLI reciba una ruta explícita.
4. Comprueba que CS Demo Manager reproduce una demo de CS2. Si usas una biblioteca de Steam personalizada, configura Playback para que use el `game/cs2.sh` correcto. El plugin de reproducción y CS2 deben ser compatibles con la demo.

La descarga obtiene las últimas **ocho** partidas ofrecidas por Valve, con demos todavía disponibles, de la cuenta abierta en Steam. Steam Flatpak no sirve para esta integración. La app no pide tu contraseña de Steam ni una API key. [Descargas de CS Demo Manager](https://cs-demo-manager.com/docs/guides/downloads).

## Instalar la CLI

Desde la carpeta del proyecto:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp config.example.toml config.toml
```

Edita `config.toml`:

- `player.steam_id`: tu SteamID64, **entre comillas**, de la cuenta abierta en Steam.
- `paths.cs2_dir`: raíz de CS2, la carpeta que contiene `game/cs2.sh`.
- `tools.csdm`: ruta al ejecutable `csdm`, por ejemplo `/opt/CS Demo Manager/csdm`; no un alias del shell.
- `paths.data_dir`: dónde conservar demos, análisis y vídeos. Las rutas relativas se resuelven respecto al TOML.

```bash
cs-highlights --config config.toml doctor
cs-highlights --config config.toml run
cs-highlights --config config.toml process /ruta/partida.dem
```

Sin `--config`, la CLI directa usa `config.toml` del directorio actual o `cs-highlights/config.toml` dentro de la carpeta de configuración del usuario: `$XDG_CONFIG_HOME` (por defecto `~/.config`) en Linux, `~/Library/Application Support` en macOS y `%LOCALAPPDATA%` en Windows. `--config` debe ir antes del subcomando. El lanzador `scripts/run.py` usa `config.local.toml` del repositorio.

`doctor` comprueba ejecutables, opciones compatibles de la CLI, PostgreSQL autenticado, Steam abierto, CS2 cerrado, sesión gráfica, rutas y escritura. Guarda `data/environment.json` con versiones y diagnósticos, sin credenciales. Detectar el proceso de Steam no garantiza una sesión autenticada; el acceso al Game Coordinator se comprueba al descargar.

## Primera prueba real

Antes de procesar varias partidas, ejecuta:

```bash
python scripts/smoke_linux.py --config config.toml
```

Descarga las demos disponibles, analiza una con bajas tuyas y graba un fragmento de hasta diez segundos. También admite una demo ya descargada:

```bash
python scripts/smoke_linux.py --config config.toml /ruta/partida.dem
```

El resultado y `validation.json` quedan en `data/smoke/<id>/`. Abre el MP4 y comprueba que muestra tu perspectiva, la baja seleccionada y el audio sincronizado. ffprobe comprueba automáticamente formato, codecs, resolución, fps y duraciones; no puede verificar visualmente la cámara o el contenido. Después usa `run` para generar los resúmenes completos.

**Estado de validación (17 de octubre de 2026):** 58 pruebas automatizadas correctas. En una Steam Machine con SteamOS se han descargado ocho demos de Valve, analizado una partida real de Nuke y generado un MP4 de ocho segundos, 1920×1080 a 60 fps, H.264/AAC. Se ha revisado un fotograma con la perspectiva del jugador; queda pendiente reproducir el vídeo para comprobar movimiento y sincronización del audio. Los resultados locales están en `data/smoke/` y no se incluyen en Git. El 18 de octubre se completó además un resumen de Nuke con nueve clips de ocho segundos, unidos y validados automáticamente.

## Selección y vídeo

- Jugador identificado por SteamID64; se usan los bandos de cada baja, incluidos cambios de lado y prórrogas.
- Se excluyen bajas de compañeros, suicidios, espectadores y eventos fuera de rondas jugables, incluido calentamiento.
- Ventana predeterminada: 5 segundos antes y 3 después. Se limita al final del freeze time y al final oficial de la ronda; si falta este último, al final de la ronda. Los intervalos que se tocan o solapan se fusionan dentro de la misma ronda.
- Los intervalos están expresados en ticks, con final exclusivo. Se conserva el tick del evento incluso con margen posterior cero.
- Los clutches ganados de 1 contra 2, 3, 4 o 5 se incluyen desde el inicio del clutch hasta la resolución de la ronda, con los márgenes configurados. También se incluyen si se ganan sin bajas; se excluyen los perdidos y los 1 contra 1.
- Si una baja propia contra un enemigo precede a un defuse propio en la misma ronda, se prolonga el clip de la última baja hasta completar la desactivación y su margen posterior. No se añaden defuses aislados ni de compañeros. Los bloques solapados se fusionan.
- Salida: H.264/AAC en MP4, 1080p60, HUD normal, audio de juego, sin voces ni rayos X. La cámara sigue a tu jugador; después de su muerte depende del comportamiento espectador de CS2.
- Cada clip tiene su propio MP4 y se une cronológicamente al vídeo final. CS2 permanece abierto entre los clips de un lote: por defecto hasta cuatro, manteniendo los tramos de hasta diez segundos. Al finalizar el lote se cierra para que CS Demo Manager convierta sus imágenes y audio. El tamaño del lote se reduce automáticamente según el espacio disponible en CS2 y en la salida, con margen para la copia. La velocidad depende del equipo; la mejora reduce arranques, sin cambiar resolución ni fps.
- Las jugadas largas se capturan en tramos equilibrados de hasta diez segundos, sin huecos entre sus ticks, y se concatenan para conservar el bloque completo con un uso acotado de temporales. `intervals.json` distingue las jugadas (`intervals`) de los tramos (`capture_segments`). La selección nueva usa `pipeline_version=3`: al volver a procesar crea otra variante y conserva los vídeos anteriores. Un proceso ya iniciado conserva la selección con la que arrancó.

Todos los MP4 empiezan con hasta cinco segundos de la cámara de presentación del inicio de la primera ronda, antes de las jugadas. Se captura desde la demo sin fijar una cámara de jugador; si la demo no conserva esa escena, el vídeo muestra la cámara predeterminada disponible. Cambia `[intro].duration_seconds` o usa `0` para desactivarla. En el modo `deaths`, el audio de esa apertura se silencia y el audio de las jugadas sigue siendo el de la perspectiva del ejecutor.

En `[runtime]`, `capture_batch_size = 4` limita los clips por sesión (entre 1 y 32). Usa `1` para recuperar el arranque por clip. La opción se aplica también a configuraciones antiguas que no incluyan esa clave y no invalida clips existentes. Cada lote queda registrado en `manifest.json` (`capture_batches`) con sus clips, duración grabada y tiempo transcurrido. Si se interrumpe una conversión, la siguiente ejecución recupera los MP4 terminados y graba solo los pendientes. TrueView requiere lotes independientes: la apertura de `deaths` se captura en una sesión y las perspectivas de los jugadores en las siguientes.

### Modo de análisis de muertes

El modo opcional `deaths` revisa cada muerte causada por un rival con tu cámara y la del ejecutor sincronizadas en pantalla dividida. Incluye diez segundos previos, tres posteriores, el radar animado con recorridos y un informe local en español. Los márgenes se pueden cambiar en `[deaths]` sin alterar `[selection]` ni las variantes de highlights existentes.

```bash
cs-highlights --config config.toml analyze --mode deaths /ruta/partida.dem
cs-highlights --config config.toml process --mode deaths /ruta/partida.dem
cs-highlights --config config.toml run --mode deaths
```

La primera ejecución analiza posiciones con CS Demo Manager y puede reanalizar la demo si aún no están guardadas. Este modo consulta la base PostgreSQL externa de CS Demo Manager y necesita los radares oficiales del mapa en su instalación. El MP4 muestra ambas cámaras arriba, el minimapa y las evidencias abajo; conserva el audio de la perspectiva del rival. El informe distingue hechos de hipótesis: pasos, audibilidad, visión entre obstáculos y comunicaciones no se pueden confirmar automáticamente con estas reglas. En `analyze`, genera posiciones, recorridos e informe sin capturar vídeo, también en macOS.

El resultado y la recuperación quedan en `data/matches/<sha256-demo>/<huella-deaths>-<hash-recursos-mapa>/`: `deaths.mp4`, `deaths.json`, `positions.json`, los vídeos de ambas perspectivas y `manifest.json`. Los cambios de radar o metadatos generan una variante y conservan el MP4 anterior. TrueView se solicita a CS Demo Manager para los dos vídeos y su compatibilidad sigue dependiendo de la demo y la versión del juego.

## Archivos y recuperación

```text
data/
  environment.json
  downloads/
    partida.dem
    partida.dem.info
    last-download.json
    download.log
  matches/<sha256-demo>/
    demo.dem
    demo.dem.info                  # si la entrada lo tenía
    <huella-configuración>/
      raw.json                    # exportación original de CS Demo Manager
      analysis.json               # contrato normalizado, schema_version=2
      intervals.json              # decisiones de selección
      manifest.json               # estado, versiones y hashes
      clips/0001.mp4
      highlights.mp4
      logs/
```

Vuelve a ejecutar el mismo comando para reanudar. Se reutilizan el análisis y los clips validados; también se recupera un clip que terminó de generarse antes de una interrupción. Cambiar selección, jugador o formato crea otra variante y conserva el resultado anterior. El nombre de la partida o la ruta original no determinan su identidad: se usa el SHA-256 del contenido.

Las descargas se realizan en una carpeta de entrada. Una descarga interrumpida queda en `downloads/interrupted-*` y se vuelve a descargar; nunca se considera completa por la mera existencia de un `.dem`. En la CLI estable, su `.dem.info` acredita que terminó la descompresión. Las descargas fallidas no impiden procesar las demos que sí se completaron.

Una partida sin bajas termina como `no_highlights`, sin un MP4 vacío. Un SteamID ausente se considera un error de configuración. Los fallos se guardan con la etapa en `manifest.json`; `run` continúa con las demás partidas.

La aplicación solo limpia sus carpetas de captura tras validar y conservar el MP4. **CS Demo Manager limpia por su cuenta** `game/csgo/movie` y `game/csgo/csdm/movie`, también en algunas rutas de error. La PoC impide arrancar si encuentra contenido previo o enlaces simbólicos en esas carpetas. Si quedan archivos tras un fallo, muévelos a otra ubicación antes de reanudar; no se borran automáticamente desde la PoC. Evita otras capturas simultáneas en el mismo juego.

En builds recientes, CS Demo Manager usa una cola en un proceso separado: interrumpir la CLI puede dejar el trabajo activo. La PoC consulta la cola antes de enviar otra grabación y se detiene si encuentra trabajo pendiente. En v3.20.1 la grabación corre en el proceso CLI; si queda CS2 abierto tras un fallo, ciérralo antes de reintentar. Un cambio de versión de CS2/plugin puede requerir actualizar CS Demo Manager o usar una demo reciente.

Códigos de salida: `0` éxito (incluye ausencia de bajas o partidas), `1` algún fallo, `2` uso incorrecto de argumentos, `130` interrupción. No hay monitor ni tarea programada.

## Desarrollo y futuro agente

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

La suite no necesita Steam ni PostgreSQL. La prueba de medios se omite si no existen FFmpeg/ffprobe; el resto usa la biblioteca estándar.

Los módulos separan adquisición, normalización, selección y renderizado. `select_kills(Analysis, steam_id, before, after)` devuelve `Interval`: inicio/final, ronda, jugador, motivo y ticks de las bajas. Un agente futuro podrá consumir `analysis.json` y los datos completos de `raw.json`, y producir intervalos equivalentes. Antes de aceptar una selección externa habrá que validar sus límites, jugador y coste de captura; la PoC todavía no ejecuta agentes.

Los contratos y las diferencias entre versiones se describen en [docs/compatibility.md](docs/compatibility.md).
