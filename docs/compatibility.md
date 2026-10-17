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

- Match: `game="cs2"`, `checksum`, `tickrate`, `tickCount`, `mapName`, `date`, `players`, `rounds`, `kills`.
- Player: `steamId` como string y `name`.
- Round: `number`, `startTick`, `freezetimeEndTick`, `endTick`, `endOfficiallyTick`.
- Kill: `tick`, `roundNumber`, `killerSteamId`, `victimSteamId`, `killerSide`, `victimSide`, `weaponName`, `isHeadshot`.

No se adivinan campos alternativos ni se supone tickrate=64 si falta el dato. Una exportación incompatible falla antes de guardarse como análisis reutilizable. Los datos originales se conservan completos.

El renderizador usa `CS`, `FFmpeg`, intervalos explícitos, `--focus-player` y `spec_mode 4`. Se anulan las opciones heredadas que modificarían el formato, el HUD, las voces o los rayos X. La salida `images-and-video` permite validar el MP4 antes de eliminar los archivos crudos que CS Demo Manager mueve al directorio privado del intento.

Antes de lanzar el renderizador se comprueba que `paths.cs2_dir` coincide con la ubicación configurada en CS Demo Manager, que CS2 está cerrado y que sus carpetas nativas de captura están vacías. Los bloqueos impiden dos ejecuciones de esta PoC sobre la misma carpeta de datos o instalación del juego. No bloquean aplicaciones externas: no se deben lanzar grabaciones manuales simultáneamente.

## Validación pendiente

El manifiesto conserva versiones de Python, CLI, PostgreSQL, FFmpeg/ffprobe y `steam.inf` si está disponible. `environment.json` es un diagnóstico, no un certificado de aceptación. `scripts/smoke_linux.py` genera un informe de la prueba real, con revisión visual pendiente. Falta ejecutar esa prueba en el PC Linux del usuario y verificar una partida completa con todas sus bajas.
