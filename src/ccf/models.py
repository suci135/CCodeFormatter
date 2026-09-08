"""Small data models shared by the checker and the UI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Diagnostic:
    """One source-level rule violation."""

    path: Path
    line: int
    column: int
    end_line: int
    end_column: int
    severity: str
    rule_id: str
    message: str
    expected: str = ""
    fixable: bool = False


@dataclass(frozen=True)
class CheckResult:
    """Source and diagnostics returned by the application service."""

    path: Path
    source: str
    diagnostics: tuple[Diagnostic, ...]
