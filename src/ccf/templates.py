"""Loading and saving the editable C/H reference templates."""

from __future__ import annotations

import os
import json
import re
import sys
from pathlib import Path

from .default_templates import DEFAULT_TEMPLATES
from .formatter import SUPPORTED_EXTENSIONS


PROJECT_DIR = Path(__file__).resolve().parents[2]
if getattr(sys, "frozen", False):
    APP_DATA_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "CCodeFormatter"
    CUSTOM_TEMPLATE_DIR = APP_DATA_DIR / "profiles" / "custom"
else:
    CUSTOM_TEMPLATE_DIR = PROJECT_DIR / "profiles" / "custom"

PROFILE_ROOT = CUSTOM_TEMPLATE_DIR.parent
PROJECT_CONFIG_NAME = ".ccfconfig.json"
DEFAULT_PROFILE = "default"


def _is_template_dir(directory: Path) -> bool:
    return all((Path(directory) / f"template{suffix}").is_file() for suffix in SUPPORTED_EXTENSIONS)


def profile_template_dir(name: str) -> Path | None:
    if name == DEFAULT_PROFILE:
        return None
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ValueError("模板名称只能包含字母、数字、点、下划线和短横线。")
    return PROFILE_ROOT / name


def list_profiles() -> list[str]:
    names = [DEFAULT_PROFILE]
    if PROFILE_ROOT.is_dir():
        names.extend(
            path.name
            for path in PROFILE_ROOT.iterdir()
            if path.is_dir() and _is_template_dir(path)
        )
    return sorted(set(names), key=lambda name: (name != DEFAULT_PROFILE, name.casefold()))


def save_profile_templates(name: str, c_source: str, h_source: str) -> Path | None:
    directory = profile_template_dir(name)
    if directory is None:
        raise ValueError("内置默认模板不能直接修改，请另存为新的模板。")
    return save_templates(c_source, h_source, directory)


def project_config_path(path: Path) -> Path:
    path = Path(path)
    if path.name == PROJECT_CONFIG_NAME:
        return path
    return path / PROJECT_CONFIG_NAME if path.is_dir() else path.parent / PROJECT_CONFIG_NAME


def load_project_profile(path: Path) -> str | None:
    start = Path(path)
    if start.is_file():
        start = start.parent
    for directory in (start, *start.parents):
        config = directory / PROJECT_CONFIG_NAME
        if not config.is_file():
            continue
        try:
            value = json.loads(config.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        profile = value.get("profile") if isinstance(value, dict) else None
        return profile if isinstance(profile, str) else None
    return None


def save_project_config(path: Path, profile: str) -> Path:
    if profile not in list_profiles():
        raise ValueError(f"模板不存在：{profile}")
    target = project_config_path(Path(path))
    target.write_text(
        json.dumps({"profile": profile}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return target


def template_dir_for_use(path: Path | None = None) -> Path | None:
    """Use a project-selected profile, then custom templates, then embedded defaults."""
    profile = load_project_profile(path) if path is not None else None
    if profile == DEFAULT_PROFILE:
        return None
    if profile:
        try:
            directory = profile_template_dir(profile)
        except ValueError:
            # A hand-edited or stale project config must not prevent the
            # formatter from opening. Fall back to the embedded/default path.
            directory = None
        if directory is not None and _is_template_dir(directory):
            return directory
    if _is_template_dir(CUSTOM_TEMPLATE_DIR):
        return CUSTOM_TEMPLATE_DIR
    return None


def load_template(extension: str, directory: Path | None = None) -> str:
    """Load one custom template or the embedded application default."""
    suffix = extension.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"不支持的模板类型：{extension}")
    if directory is not None:
        path = Path(directory) / f"template{suffix}"
        if path.is_file():
            return path.read_text(encoding="utf-8-sig")
    return DEFAULT_TEMPLATES[suffix]


def save_templates(c_source: str, h_source: str, directory: Path = CUSTOM_TEMPLATE_DIR) -> Path:
    """Persist both editable templates and return their containing directory."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "template.c").write_text(c_source, encoding="utf-8", newline="\n")
    (directory / "template.h").write_text(h_source, encoding="utf-8", newline="\n")
    return directory
