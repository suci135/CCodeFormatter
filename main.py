"""Compatibility launcher; application code lives under src/ccf."""

from src.ccf.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
