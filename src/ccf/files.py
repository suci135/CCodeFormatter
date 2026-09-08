"""File validation and output for the formatter."""

from __future__ import annotations

import os
import hashlib
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .formatter import SUPPORTED_EXTENSIONS, TemplateStyle, format_c_code


@dataclass(frozen=True)
class SourceMetadata:
    """Encoding and newline details needed to write a source file safely."""

    encoding: str
    bom: bytes
    newline: str
    trailing_newline: bool


def _decode_source(data: bytes) -> tuple[str, SourceMetadata]:
    bom = b""
    if data.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8"
        bom = b"\xef\xbb\xbf"
    elif data.startswith(b"\xff\xfe\x00\x00"):
        encoding = "utf-32-le"
        bom = b"\xff\xfe\x00\x00"
    elif data.startswith(b"\x00\x00\xfe\xff"):
        encoding = "utf-32-be"
        bom = b"\x00\x00\xfe\xff"
    elif data.startswith(b"\xff\xfe"):
        encoding = "utf-16-le"
        bom = b"\xff\xfe"
    elif data.startswith(b"\xfe\xff"):
        encoding = "utf-16-be"
        bom = b"\xfe\xff"
    else:
        encoding = next(
            (candidate for candidate in ("utf-8", "gb18030", "big5", "cp1252")
             if _can_decode(data, candidate)),
            "utf-8",
        )

    # Detect line endings after decoding. Counting byte patterns breaks
    # UTF-16/UTF-32 because every character contains embedded zero bytes.
    text = data[len(bom):].decode(encoding, errors="replace")
    crlf = text.count("\r\n")
    lf_only = text.count("\n") - crlf
    cr_only = text.count("\r") - crlf
    newline = "\r\n" if crlf >= lf_only and crlf >= cr_only and crlf else "\n"
    if not crlf and cr_only > lf_only:
        newline = "\r"
    return text, SourceMetadata(encoding, bom, newline, text.endswith(("\n", "\r")))


def _can_decode(data: bytes, encoding: str) -> bool:
    try:
        data.decode(encoding)
    except UnicodeDecodeError:
        return False
    return True


def read_source_with_metadata(path: Path) -> tuple[str, SourceMetadata]:
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"只支持 .c 和 .h 文件：{path.name}")
    if not path.is_file():
        raise FileNotFoundError(path)
    return _decode_source(path.read_bytes())


def read_source(path: Path) -> str:
    return read_source_with_metadata(path)[0]


def source_digest(path: Path) -> str:
    """Return a content fingerprint used to reject stale overwrite previews."""
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _encoded_content(content: str, metadata: SourceMetadata) -> bytes:
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    if not metadata.trailing_newline:
        normalized = normalized.rstrip("\n")
    if metadata.newline != "\n":
        normalized = normalized.replace("\n", metadata.newline)
    # Never replace characters silently: a lossy write would destroy source
    # text while looking like a successful format operation.
    return metadata.bom + normalized.encode(metadata.encoding)


def write_source_preserving(path: Path, content: str, metadata: SourceMetadata | None = None) -> None:
    path = Path(path)
    if metadata is None:
        _, metadata = read_source_with_metadata(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(_encoded_content(content, metadata))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            shutil.copymode(path, temporary_name)
        except OSError:
            pass
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def format_file(path: Path, template_dir: Path | None = None, output: Path | None = None) -> Path:
    path = Path(path)
    output = output or path.with_name(f"{path.stem}.formatted{path.suffix}")
    source, metadata = read_source_with_metadata(path)
    if template_dir is None:
        from .templates import template_dir_for_use
        template_dir = template_dir_for_use(path)
    content = format_c_code(source, TemplateStyle.from_template(template_dir, path.suffix))
    write_source_preserving(output, content, metadata)
    return output


def overwrite_file(path: Path, content: str, metadata: SourceMetadata | None = None) -> None:
    """Write a user-confirmed formatted result back to its source file."""
    path = Path(path)
    write_source_preserving(path, content, metadata)


def save_source(path: Path, content: str, metadata: SourceMetadata | None = None) -> Path:
    """Save formatted source to a path chosen by the user."""
    path = Path(path)
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"只支持保存为 .c 或 .h 文件：{path.name}")
    if metadata is None:
        metadata = SourceMetadata("utf-8", b"", "\n", True)
    write_source_preserving(path, content, metadata)
    return path
