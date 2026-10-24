# Contrato con CS Demo Manager

Inspección de código realizada el 17 de octubre de 2026. No equivale a una prueba de reproducción real.

## Versiones consultadas

- Release publicada: [v3.20.1](https://github.com/akiver/cs-demo-manager/releases/tag/v3.20.1), commit `8961f50`.
- Rama de desarrollo: commit [`7270bc95c8cab87b7f9ddd490fea13c6eda3ace7`](https://github.com/akiver/cs-demo-manager/tree/7270bc95c8cab87b7f9ddd490fea13c6eda3ace7), posterior a la release aunque package.json conserva `3.20.1`.

Por eso se detectan capacidades mediante ayuda de la CLI; comparar únicamente el número de versión sería insuficiente. La versión estable es el objetivo inicial. No se exige compilar CS Demo Manager.

## Descarga y control de procesos

`csdm dl-valve --output DIR` funciona en la release. Omite archivos existentes. Escribe `.dem.info` únicamente después de terminar la descompresión; algunos errores de Steam producen salida de error con código cero. La PoC publica solo pares completos de demo/info, inspecciona los mensajes de error y conserva archivos incompletos fuera de la caché de demos completas.

En desarrollo se añade `--json`, con `downloaded`, `skipped` y `failed`. La PoC lo usa cuando está disponible y comprueba también el código de salida.

La release ejecuta `generateVideo` directamente. La rama de desarrollo usa un daemon y permite `csdm video list --json`; la PoC rechaza una cola que contenga estados distintos de `success` o `error`. Nunca reanuda una cola ajena para introducir su vídeo.

Los subprocesos reciben listas de argumentos sin shell. La opción FFmpeg que empieza por `-` se pasa como `--ffmpeg-output-parameters=...`, compatible con el parser de argumentos de Node. Los logs de procesos largos van a disco y los comandos tienen tiempo límite.

## JSON y grabación

Se usa el contrato de `src/common/types/{match,round,kill,match-player}.ts` y `src/node/json/export-matches-to-json.ts` de CS Demo Manager:

- Match: `game="CS2"` en la exportación real de v3.20.1 (también se acepta `"cs2"`), `checksum`, `tickrate`, `tickCount`, `mapName`, `date`, `players`, `rounds`, `kills`.
- Player: `steamId` como string y `name`.
- Round: `number`, `startTick`, `freezetimeEndTick`, `endTick`, `endOfficiallyTick`.
- Kill: `tick`, `roundNumber`, `killerSteamId`, `victimSteamId`, `killerSide`, `victimSide`, `weaponName`, `isHeadshot`.
- Clutch (`clutches`): `tick` de inicio, `roundNumber`, `clutcherSteamId`, `opponentCount`, `won` booleano. Se seleccionan los ganados por el jugador con 2–5 rivales hasta `round.endTick`, con margen posterior limitado por el final oficial.
- Defuse (`bombsDefused`): `tick` de desactivación completada, `roundNumber`, `defuserSteamId`. Prolonga la última baja seleccionable del jugador anterior al defuse en esa misma ronda. Si faltan las listas opcionales de objetivos, se mantiene la selección de bajas disponible.

El análisis normalizado usa `schema_version=2` e incluye `clutches`, `defuses` y `rounds[].outcome_tick`. La huella de highlights usa `pipeline_version=3` para conservar las variantes anteriores, incluida la apertura añadida al principio. Las jugadas se dividen para captura en segmentos de hasta diez segundos que cubren exactamente los ticks seleccionados; después se concatenan. Esta selección se ha contrastado con los JSON reales de dudu, incluidos dos clutches ganados y dos rondas adicionales con baja y defuse; la nueva captura segmentada todavía no se ha comprobado con CS2 porque el lote anterior sigue activo.

No se adivinan campos alternativos ni se supone tickrate=64 si falta el dato. Una exportación incompatible falla antes de guardarse como análisis reutilizable. Los datos originales se conservan completos.

Las jugadas usan `CS`, `FFmpeg`, intervalos explícitos, `--focus-player` y `spec_mode 4`. La apertura inicial usa el rango del comienzo de la primera ronda sin `--focus-player`, para dejar que la demo reproduzca su cámara predeterminada. Se anulan las opciones heredadas que modificarían el formato, el HUD, las voces o los rayos X. La salida `images-and-video` permite validar el MP4 antes de eliminar los archivos crudos que CS Demo Manager mueve al directorio privado del intento.

### Sesiones con varios clips (25 de octubre de 2026)

Se ha comprobado el contrato de [`video-command.ts`](https://github.com/akiver/cs-demo-manager/blob/v3.20.1/src/cli/commands/video-command.ts), [`generate-video.ts`](https://github.com/akiver/cs-demo-manager/blob/v3.20.1/src/node/video/generation/generate-video.ts) y [`create-cs2-video-json-file.ts`](https://github.com/akiver/cs-demo-manager/blob/v3.20.1/src/node/video/generation/create-cs2-video-json-file.ts). La conversión espera la salida del proceso de CS2: omitir el cierre en una invocación por clip impediría terminar normalmente. Para evitar los arranques repetidos se usa `csdm video --config-file recording.json` con varias `sequences`, `concatenateSequences=false` y `closeGameAfterRecording=true`. El cierre se programa después de la última secuencia del lote.

El archivo configura explícitamente resolución, fps, sistema de captura, codecs y opciones por secuencia; en v3.20.1, `--config-file` omite el procesamiento del resto de las opciones. Se escapan los delimitadores de comentarios JSONC dentro de rutas para que caracteres como `#` no las trunquen. Se conserva el identificador de secuencia aunque CS Demo Manager ordene la captura por tick: cada archivo `sequence-N-tick-START-to-END.mp4` se asigna a su propio clip/perspectiva mediante `capture-plan.json`.

El valor predeterminado de `runtime.capture_batch_size` es 4 y se reduce para respetar el espacio disponible. El modo `1` usa la invocación anterior. `doctor` comprueba `--config-file` cuando hay lotes habilitados. El manifiesto registra cada sesión y conserva intentos anteriores para recuperar los MP4 terminados tras una interrupción. Los temporales de un lote solo se eliminan después de publicar y registrar todos sus clips. La huella de salida no cambia por esta opción de ejecución. La prueba `smoke --batch` permite verificar dos tramos consecutivos en una sesión; su prueba real y la mejora de tiempo en SteamOS siguen pendientes.

## Modo de análisis de muertes

CS Demo Manager v3.20.1 admite `csdm analyze DEMO --analyze-positions --force`. Las posiciones se guardan en la tabla `player_positions`; los metadatos de radar de la tabla `maps` y los PNG de `static/images/maps/cs2/radars` proporcionan la transformación de coordenadas y las plantas. La exportación JSON de v3.20.1 deja `playerPositions` vacío, por eso este modo consulta las posiciones directamente con `psql` y las credenciales que ya están en `settings.json`.

El modo genera una grabación focalizada por perspectiva con `--true-view`, después sincroniza ambos vídeos con la animación del radar mediante FFmpeg. La configuración se guarda junto al informe y la salida bajo una huella propia, separada de la salida de highlights.

Antes de lanzar el renderizador se comprueba que `paths.cs2_dir` coincide con la ubicación configurada en CS Demo Manager, que CS2 está cerrado y que sus carpetas nativas de captura están vacías. Los bloqueos impiden dos ejecuciones de esta PoC sobre la misma carpeta de datos o instalación del juego. No bloquean aplicaciones externas: no se deben lanzar grabaciones manuales simultáneamente.

## Validación pendiente

El manifiesto conserva versiones de Python, CLI, PostgreSQL, FFmpeg/ffprobe y `steam.inf` si está disponible. `environment.json` es un diagnóstico, no un certificado de aceptación. `scripts/smoke_linux.py` genera un informe de la prueba real, con revisión visual pendiente. La prueba corta se completó el 17 de octubre de 2026 en SteamOS. El 18 de octubre se generó un resumen completo de Nuke con los nueve intervalos seleccionados y se validó su concatenación automáticamente. Queda pendiente la revisión audiovisual completa.
