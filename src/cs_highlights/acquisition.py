import os
from pathlib import Path
import re
import uuid

from .csdm import CSDM, json_result
from .errors import HighlightsError
from .storage import now, write_json


def discover_and_download(csdm: CSDM) -> tuple[list[Path], bool]:
    """Publish only downloads acknowledged by CSDM; quarantine interrupted downloads."""
    root = csdm.config.data_dir / "downloads"
    root.mkdir(parents=True, exist_ok=True)
    staging = root / "incoming"
    if staging.exists():
        # A killed downloader may leave a truncated .dem. Never allow dl-valve to skip it.
        staging.rename(root / f"interrupted-{uuid.uuid4().hex}")
    staging.mkdir()
    for demo in root.glob("*.dem"):
        # CSDM checks existence only; these links suppress downloading the known complete demos.
        os.link(demo, staging / demo.name)
    log = root / "download.log"
    argv = ["dl-valve", "--output", str(staging)]
    if csdm.modern:
        argv.append("--json")
    result = csdm.run(argv,
                             log=log, timeout=csdm.config.timeout_seconds, check=False)
    if csdm.modern:
        try:
            summary = json_result(result.stdout, "dl-valve")
        except HighlightsError as exc:
            raise HighlightsError(f"No se pudieron recuperar las partidas. Comprueba Steam y la conexión. "
                                  f"Consulta {log}.") from exc
    else:
        # In the published v3.20.1, .dem.info is written only after decompression finishes.
        # The exit code alone is insufficient: Steam failures can incorrectly return zero.
        completed = [p for p in staging.glob("*.dem") if p.with_suffix(".dem.info").is_file()
                     and p.with_suffix(".dem.info").stat().st_size > 0]
        errors = re.search(r"\berror\b|expired|demo url not found|matches info file not found|not running|not logged|not connected|needs to be restarted",
                           result.stdout + result.stderr, re.IGNORECASE)
        summary = {"downloaded": [{"demoPath": str(p)} for p in completed],
                   "skipped": [{"demoPath": str(p)} for p in staging.glob("*.dem") if (root / p.name).exists()],
                   "failed": [{"error": f"Consulta {log}"}] if errors or result.returncode else []}
    if any(not isinstance(summary.get(k), list) for k in ("downloaded", "skipped", "failed")):
        raise HighlightsError(f"Resumen de descarga incompatible. Consulta {log}.")
    for item in summary["downloaded"]:
        if not isinstance(item, dict) or not isinstance(item.get("demoPath"), str):
            raise HighlightsError("La descarga devolvió una ruta de demo inválida.")
        demo = Path(item["demoPath"])
        if demo.parent.resolve() != staging.resolve() or demo.is_symlink() or demo.suffix != ".dem":
            raise HighlightsError("La descarga devolvió una demo fuera de su directorio.")
        if not demo.is_file() or not demo.stat().st_size:
            raise HighlightsError(f"Demo descargada vacía o inexistente: {demo}")
        os.replace(demo, root / demo.name)
        info = demo.with_suffix(".dem.info")
        if info.exists():
            os.replace(info, root / info.name)
    write_json(root / "last-download.json", {"date": now(), "exit_code": result.returncode, **summary})
    # Only remove our staging links and empty directory after a completed response.
    for path in staging.iterdir():
        if path.name in {p.name for p in root.glob("*.dem")} and path.is_file():
            path.unlink()
    if not any(staging.iterdir()):
        staging.rmdir()
    return sorted(root.glob("*.dem")), bool(result.returncode or summary["failed"])
