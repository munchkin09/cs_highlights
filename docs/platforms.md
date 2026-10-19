# Windows, macOS y Linux

La CLI ofrece análisis y descarga en los tres sistemas. La captura de CS2 requiere Windows o Linux: CS2 no tiene versión nativa para macOS. Esta limitación también figura en la [compatibilidad de vídeo de CS Demo Manager](https://cs-demo-manager.com/docs/guides/video).

| Operación | Windows | macOS | Linux / SteamOS |
| --- | --- | --- | --- |
| `analyze DEMO` — JSON e intervalos | Sí | Sí | Sí |
| `download` — demos de la cuenta de Steam | Sí | Sí | Sí |
| `process DEMO` / `run` — MP4 | Sí | No | Sí |

En SteamOS usa el [instalador y lanzador específicos](steamos.md), que preparan también PostgreSQL, FFmpeg y CS Demo Manager en tu carpeta de usuario. No necesitas sustituirlos por los scripts siguientes.

## Dependencias externas

En Windows, macOS y otras distribuciones Linux instala primero:

1. [Python 3.12 o posterior](https://www.python.org/downloads/), con `venv` y `pip` (en algunas distribuciones Linux se instalan como paquetes separados).
2. [CS Demo Manager v3.20.1](https://github.com/akiver/cs-demo-manager/releases/tag/v3.20.1), usando el paquete nativo del sistema.
3. [PostgreSQL 17 o posterior](https://www.postgresql.org/download/), incluido el cliente `psql`. Arranca el servidor y configura su conexión en CS Demo Manager. Si la versión ofrece una base integrada, selecciona la conexión externa para esta PoC. Sigue la [guía de instalación de CS Demo Manager](https://cs-demo-manager.com/docs/installation).

Para descargar necesitas Steam nativo abierto y con la cuenta iniciada. Para analizar una demo local no se necesita Steam ni CS2.

Para capturar en Windows/Linux necesitas además CS2, FFmpeg y ffprobe. Puedes instalar FFmpeg desde CS Demo Manager. Configura su ubicación en Settings > Video y la del juego en Playback. Abre CS2 una vez y ciérralo antes de ejecutar la captura desde una sesión de escritorio.

## Instalar la CLI

Desde la carpeta del repositorio, en PowerShell de Windows:

```powershell
py -3.12 scripts/install.py
py -3.12 scripts/run.py doctor
```

En macOS o Linux:

```bash
python3.12 scripts/install.py
python3.12 scripts/run.py doctor
```

El instalador crea una `.venv` local, instala el proyecto y genera `config.local.toml` con rutas para el sistema actual. **Instala la CLI; las herramientas externas del apartado anterior se instalan por separado.** No sobrescribe una configuración existente ni borra una `.venv` incompatible. No copies la `.venv` de otro sistema; si existe una, muévela y repite la instalación en el equipo de destino.

Antes de usar `doctor`, edita `config.local.toml`: introduce tu SteamID64 entre comillas y revisa las rutas detectadas. Las rutas relativas parten de la carpeta del TOML. Para utilizar otra configuración con el lanzador, define `CSH_CONFIG` o pasa `--config ARCHIVO` antes del subcomando.

También puedes usar directamente el comando instalado `cs-highlights --config config.local.toml doctor`. El subcomando `init` genera una plantilla sin sobrescribir archivos; con la CLI directa, especifica `--config` para elegir dónde crearla.

## Rutas por sistema

| Elemento | Windows | macOS | Linux |
| --- | --- | --- | --- |
| Configuración de CS Demo Manager | `~/.csdm/settings.json` | `~/.csdm/settings.json` | `$XDG_CONFIG_HOME/csdm/settings.json` o `~/.config/csdm/settings.json` |
| Ejecutable de CS2, relativo a `paths.cs2_dir` | `game/bin/win64/cs2.exe` | No disponible | `game/cs2.sh` |
| CLI de CS Demo Manager | `csdm.cmd` en su carpeta de instalación | `/Applications/CS Demo Manager.app/Contents/Resources/csdm` | `csdm` de tu instalación |

En Windows, la CLI ejecuta el contenido del lanzador oficial mediante Electron, pasando cada argumento directamente. Puedes conservar la ruta de `csdm.cmd` en el TOML. Las bibliotecas de Steam personalizadas se detectan mediante su registro y `libraryfolders.vdf`; revisa siempre `paths.cs2_dir` y Playback.

`init` busca herramientas en `PATH` y en ubicaciones habituales. Si no las encuentra, escribe sus rutas completas en `[tools]`. En Windows puedes usar barras `/` o cadenas TOML entre comillas simples para conservar las barras `\`. La CLI añade las carpetas de las herramientas configuradas al entorno de sus procesos de CS Demo Manager. La aplicación gráfica se abre por separado: si no encuentra `psql`, añade su carpeta `bin` al `PATH` de tu usuario y reinicia la aplicación.

## Análisis y descarga sin juego

Después de instalar, usa `python` como el intérprete de tu sistema (`py -3.12` en Windows o `python3.12` en macOS/Linux):

```text
python scripts/run.py doctor --mode analysis
python scripts/run.py analyze "ruta/partida.dem"
python scripts/run.py doctor --mode download
python scripts/run.py download
```

El análisis escribe `analysis.json` e `intervals.json` bajo `data/matches/`, sin ejecutar CS2 ni FFmpeg. La descarga conserva las demos en `data/downloads/`. PostgreSQL y la configuración de CS Demo Manager siguen siendo necesarios.

`doctor` elige análisis en macOS y captura en Windows/Linux. `--mode` permite comprobar los requisitos de una operación concreta. Para generar un vídeo de una demo analizada en macOS, lleva la demo a Windows/Linux y ejecuta allí `process` con una configuración y herramientas de ese equipo.

## Captura y validación

En Windows/Linux, con Steam abierto y CS2 cerrado:

```text
python scripts/run.py doctor
python scripts/run.py process "ruta/partida.dem"
python scripts/run.py run
```

La prueba corta está disponible como `python scripts/smoke.py --config config.local.toml`, ejecutada con el Python de la `.venv` para que encuentre el paquete instalado. El nombre anterior `smoke_linux.py` sigue funcionando para los lanzadores existentes.

Las pruebas automatizadas cubren rutas, argumentos, comprobación de procesos, bloqueo entre procesos y análisis sin captura. Se incluye una matriz de GitHub Actions para Windows, macOS y Linux con Python 3.12/3.13; se ejecutará al publicar los cambios. La ejecución local de esta adaptación se ha comprobado en macOS. Las pruebas con dobles de herramientas no acreditan una grabación real en Windows: allí siguen pendientes descarga, análisis y captura con Steam y CS Demo Manager instalados. El estado de las pruebas reales de SteamOS se recoge en el README y su guía.
