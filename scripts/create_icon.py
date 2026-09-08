"""Rasterize the bundled SVG into a Windows multi-size ICO."""

from __future__ import annotations

import struct
from pathlib import Path

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage, QPainter
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication


ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "assets" / "icons" / "app-icon.svg"
ICO_PATH = ROOT / "assets" / "icons" / "app-icon.ico"


def main() -> None:
    app = QApplication([])
    renderer = QSvgRenderer(str(SVG_PATH))
    images: list[tuple[int, bytes]] = []
    for size in (16, 32, 48, 64, 128, 256):
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, "png")
        images.append((size, bytes(buffer.data())))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = 6 + len(images) * 16
    entries: list[bytes] = []
    payload: list[bytes] = []
    for size, data in images:
        entries.append(struct.pack(
            "<BBBBHHII", 0 if size == 256 else size, 0 if size == 256 else size,
            0, 0, 1, 32, len(data), offset,
        ))
        payload.append(data)
        offset += len(data)
    ICO_PATH.write_bytes(header + b"".join(entries) + b"".join(payload))
    print(f"Icon generated: {ICO_PATH}")


if __name__ == "__main__":
    main()
