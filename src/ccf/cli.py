"""Command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .files import format_file
from .service import inspect_file


def _write_gui_startup_diagnostic(error: ImportError) -> None:
    """Leave actionable details beside the exe when Qt cannot load."""
    bundle_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    candidates = [
        bundle_root / "PyQt6" / "Qt6" / "bin",
        bundle_root / "_internal" / "PyQt6" / "Qt6" / "bin",
        bundle_root,
    ]
    lines = [
        "CCodeFormatter GUI startup failed",
        f"error: {error}",
        f"executable: {sys.executable}",
        f"frozen: {getattr(sys, 'frozen', False)}",
        f"bundle_root: {bundle_root}",
        f"cwd: {Path.cwd()}",
        f"qt_dll_directories: {[str(path) for path in candidates if path.is_dir()]}",
        f"qtcore_candidates: {[str(path / 'Qt6Core.dll') for path in candidates if (path / 'Qt6Core.dll').is_file()]}",
        f"path_head: {os.environ.get('PATH', '').split(os.pathsep)[:5]}",
    ]
    report = "\n".join(lines) + "\n"
    try:
        (Path(sys.executable).parent / "CCodeFormatter-startup.log").write_text(report, encoding="utf-8")
    except OSError:
        try:
            (Path.cwd() / "CCodeFormatter-startup.log").write_text(report, encoding="utf-8")
        except OSError:
            pass
    print("界面启动失败，详细诊断已写入 CCodeFormatter-startup.log", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description="按 C 模板格式化 .c/.h 文件")
    parser.add_argument("files", nargs="*", type=Path, help="要格式化的 C 文件")
    parser.add_argument("--format", action="store_true", help="格式化文件（默认操作）")
    parser.add_argument("--check", action="store_true", help="只检查文件，不生成格式化文件")
    parser.add_argument("--output", type=Path, help="单文件格式化输出路径")
    args = parser.parse_args()
    if not args.files:
        try:
            from .ui import run_gui
        except ImportError as error:
            if "QtCore" in str(error) or "PyQt6" in str(error):
                _write_gui_startup_diagnostic(error)
                return 1
            raise

        return run_gui()
    try:
        if args.check:
            has_issues = False
            for path in args.files:
                result = inspect_file(path)
                if not result.diagnostics:
                    print(f"{path}: 检查通过")
                    continue
                has_issues = True
                for diagnostic in result.diagnostics:
                    print(
                        f"{diagnostic.path}:{diagnostic.line}:{diagnostic.column}: "
                        f"{diagnostic.severity} {diagnostic.rule_id}: {diagnostic.message}"
                    )
                    if diagnostic.expected:
                        print(f"  应当：{diagnostic.expected}")
            return 1 if has_issues else 0
        if args.output and len(args.files) != 1:
            parser.error("--output 只能与一个输入文件一起使用")
        for path in args.files:
            print(format_file(path, output=args.output))
        return 0
    except Exception as error:
        print(f"格式化失败：{error}", file=sys.stderr)
        return 1
