"""Make bundled Qt DLLs discoverable before PyQt6 is imported."""

from __future__ import annotations

import os
import sys
from pathlib import Path


_dll_handles = []
_bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))

_dll_directories = (
    _bundle_root,
    _bundle_root / "_internal",
    _bundle_root / "PyQt6" / "Qt6" / "bin",
    _bundle_root / "_internal" / "PyQt6",
    _bundle_root / "_internal" / "PyQt6" / "Qt6" / "bin",
)

for _directory in _dll_directories:
    if not _directory.is_dir():
        continue
    _directory_text = os.fspath(_directory)
    if hasattr(os, "add_dll_directory"):
        _dll_handles.append(os.add_dll_directory(_directory_text))
    os.environ["PATH"] = _directory_text + os.pathsep + os.environ.get("PATH", "")
