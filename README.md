# CS Highlights

PoC local para Linux: descubre tus partidas de Valve, descarga las demos, extrae sus datos y genera **un MP4 con todas tus bajas por partida**. Python coordina CS Demo Manager y FFmpeg; no hay servicios web ni dependencias Python de ejecución.

```text
Steam → CS Demo Manager → demo + análisis JSON → intervalos → CS2 → clips → MP4
```

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

Sin `--config`, se usa `config.toml` del directorio actual o `$XDG_CONFIG_HOME/cs-highlights/config.toml` (por defecto `~/.config/cs-highlights/config.toml`). `--config` debe ir antes del subcomando.

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

**Estado de validación:** las pruebas automatizadas usan datos de partida sintéticos y dobles de CS Demo Manager. La prueba de FFmpeg sí genera y une MP4 reales. La descarga, el parseo y la captura con una partida real de Steam **no se han validado en este entorno macOS**. No hay una demo real incluida ni un vídeo de juego generado.

## Selección y vídeo

- Jugador identificado por SteamID64; se usan los bandos de cada baja, incluidos cambios de lado y prórrogas.
- Se excluyen bajas de compañeros, suicidios, espectadores y eventos fuera de rondas jugables, incluido calentamiento.
- Ventana predeterminada: 5 segundos antes y 3 después. Se limita al final del freeze time y al final oficial de la ronda; si falta este último, al final de la ronda. Los intervalos que se tocan o solapan se fusionan dentro de la misma ronda.
- Los intervalos están expresados en ticks, con final exclusivo. Se conserva el tick del evento incluso con margen posterior cero.
- Salida: H.264/AAC en MP4, 1080p60, HUD normal, audio de juego, sin voces ni rayos X. La cámara sigue a tu jugador; después de su muerte depende del comportamiento espectador de CS2.
- Cada clip se graba por separado y los clips se unen cronológicamente. En Linux la captura genera imágenes y audio sin comprimir; puede necesitar varios GiB por clip. Se comprueba espacio tanto en CS2 como en la salida y se reserva margen para la copia. La velocidad depende del equipo.

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
      analysis.json               # contrato normalizado, schema_version=1
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
