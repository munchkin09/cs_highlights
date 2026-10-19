from dataclasses import dataclass
import os
from pathlib import Path
import signal
import subprocess
import tempfile

from .errors import HighlightsError
from . import platforms


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str = ""


class Runner:
    """Run argv directly. Stream verbose output to disk, never accumulate video logs in RAM."""

    def run(self, argv: list[str], *, log: Path | None = None, timeout: int = 60,
            check: bool = True, env: dict | None = None, cwd: Path | None = None) -> Result:
        if log:
            log.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryFile() as stdout, (log.open("a+b") if log else tempfile.TemporaryFile()) as stderr:
            stderr_start = stderr.tell()
            windows = platforms.system() == "Windows"
            if windows and Path(argv[0]).suffix.lower() in {".bat", ".cmd"}:
                raise HighlightsError("No se ejecutan scripts de cmd.exe directamente; configura un ejecutable nativo.")
            options = {"creationflags": 0x00000200} if windows else {"start_new_session": True}
            try:
                process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL,
                                           cwd=cwd, env=env, **options)
            except OSError as exc:
                raise HighlightsError(f"No se pudo ejecutar {argv[0]}: {exc}") from exc

            def read_output():
                stdout.seek(0)
                output = stdout.read(8 * 1024 * 1024).decode("utf-8", errors="replace")
                stderr.seek(max(stderr_start, stderr.seek(0, os.SEEK_END) - 65536))
                errors = stderr.read().decode("utf-8", errors="replace")
                if log:
                    stderr.seek(0, os.SEEK_END)
                    stderr.write(output.encode())
                    stderr.flush()
                return output, errors

            try:
                process.wait(timeout=timeout)
            except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
                # The CSDM daemon is separate; its queued recording may continue.
                # The next render checks the daemon queue before launching anything.
                if windows:
                    # Target only our PID and descendants, never all CS2/Electron processes.
                    try:
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                    except (OSError, subprocess.TimeoutExpired):
                        pass
                    finally:
                        if process.poll() is None:
                            process.kill()
                        process.wait()
                else:
                    try:
                        os.killpg(process.pid, signal.SIGINT)
                        process.wait(timeout=10)
                    except (ProcessLookupError, subprocess.TimeoutExpired):
                        if process.poll() is None:
                            os.killpg(process.pid, signal.SIGKILL)
                            process.wait()
                # Preserve the recording/conversion diagnostics even when interrupted.
                read_output()
                if isinstance(exc, KeyboardInterrupt):
                    raise
                raise HighlightsError(f"Tiempo agotado en {argv[0]}. Revisa la cola de CS Demo Manager; "
                                      "su trabajo puede seguir activo.") from exc
            # JSON download/queue results are small. Large exports go directly to files.
            output, errors = read_output()
            result = Result(process.returncode, output, errors)
            if check and result.returncode:
                detail = f" Revisa {log}." if log else ""
                raise HighlightsError(f"{Path(argv[0]).name} terminó con código {result.returncode}.{detail}")
            return result
