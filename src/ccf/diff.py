"""Small, UI-independent helpers for comparing formatted source text."""

from __future__ import annotations

import difflib
import re


def diff_opcodes(
    before: str,
    after: str,
    ignore_whitespace: bool = False,
) -> list[tuple[str, int, int, int, int]]:
    """Return stable line-level diff operations for the compare view."""
    before_lines = before.splitlines()
    after_lines = after.splitlines()
    if ignore_whitespace:
        before_keys = [re.sub(r"\s+", "", line) for line in before_lines]
        after_keys = [re.sub(r"\s+", "", line) for line in after_lines]
    else:
        before_keys = before_lines
        after_keys = after_lines
    return difflib.SequenceMatcher(
        None,
        before_keys,
        after_keys,
        autojunk=False,
    ).get_opcodes()


def unified_diff(before: str, after: str, path: str, context: int = 3) -> str:
    """Return a UTF-8-friendly unified diff for review or version control."""
    result = difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{path} (before)",
        tofile=f"{path} (after)",
        n=max(0, int(context)),
        lineterm="\n",
    )
    return "".join(result)
