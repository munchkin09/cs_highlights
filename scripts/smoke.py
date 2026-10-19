"""Platform-neutral entry point; keep smoke_linux.py for existing SteamOS launchers."""
from smoke_linux import main


if __name__ == "__main__":
    raise SystemExit(main())
