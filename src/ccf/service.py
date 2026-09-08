"""Application services shared by the CLI and the desktop UI."""

from __future__ import annotations

from pathlib import Path

from .checker import check_source
from .files import read_source
from .models import CheckResult


def inspect_file(path: Path) -> CheckResult:
    """Read one supported source file and run every enabled local check."""
    path = Path(path)
    source = read_source(path)
    return CheckResult(path, source, tuple(check_source(path, source)))
