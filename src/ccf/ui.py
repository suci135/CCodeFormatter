"""PyQt6 Apple-style desktop interface for CCodeFormatter."""

from __future__ import annotations

import sys
import ctypes
from ctypes import wintypes
import difflib
import html
import json
import os
import re
from collections import Counter
from pathlib import Path

import numpy as np

from PyQt6.QtCore import QFileSystemWatcher, QDir, QPoint, QRect, QRectF, QSize, QSettings, QSortFilterProxyModel, QThread, QThreadPool, QTimer, QUrl, Qt, QObject, QRunnable, pyqtSignal
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QCloseEvent,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFileSystemModel,
    QIcon,
    QKeyEvent,
    QLinearGradient,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
    QKeySequence,
    QSyntaxHighlighter,
    QTextCursor,
    QTextCharFormat,
    QTextFormat,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFileIconProvider,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMainWindow,
    QMessageBox,
    QInputDialog,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSizeGrip,
    QSplitter,
    QStackedWidget,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

try:
    # pyglass-qt provides the actual refraction/Fresnel pass. Keep the import
    # optional so the source UI still has a safe fallback on older installs.
    from pyglass.backdrop import ScreenBackdrop, WidgetBackdrop
    from pyglass.refract import GlassMaterial, array_to_qimage
    from pyglass.effect import GlassRenderer
except ImportError:  # pragma: no cover - exercised only without the optional package
    WidgetBackdrop = None
    ScreenBackdrop = None
    GlassMaterial = None
    array_to_qimage = None
    GlassRenderer = None

from .design import ThemePalette, palette_for
from .diff import diff_opcodes, unified_diff
from .files import (
    SUPPORTED_EXTENSIONS,
    SourceMetadata,
    format_file,
    overwrite_file,
    read_source,
    read_source_with_metadata,
    save_source,
    source_digest,
)
from .formatter import TemplateStyle, format_c_code, safe_repair_c_code
from .models import Diagnostic
from .service import inspect_file
from .templates import (
    DEFAULT_PROFILE,
    list_profiles,
    load_template,
    load_project_profile,
    profile_template_dir,
    save_profile_templates,
    save_project_config,
    save_templates,
    template_dir_for_use,
)


ITEM_KIND_ROLE = int(Qt.ItemDataRole.UserRole) + 1


FONT_CANDIDATES = ("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Segoe UI")
CODE_FONT_CANDIDATES = ("JetBrains Mono", "Cascadia Code", "Cascadia Mono", "Consolas", "Courier New")
_frozen_root = getattr(sys, "_MEIPASS", None)
_resource_candidates = (
    Path(_frozen_root) if _frozen_root else Path(__file__).resolve().parents[2],
    Path(_frozen_root) / "_internal" if _frozen_root else Path(__file__).resolve().parents[2],
)
_RESOURCE_ROOT = next(
    (candidate for candidate in _resource_candidates if (candidate / "assets" / "icons").is_dir()),
    _resource_candidates[0],
)
ASSET_DIR = _RESOURCE_ROOT / "assets" / "icons"
APP_ICON_PATH = ASSET_DIR / "app-icon.ico"
DROPDOWN_ICON_PATH = ASSET_DIR / "tabler-chevron-down.svg"
EFFECT_MODES = ("none", "liquid", "frosted")
GENERATED_DIR_NAMES = frozenset({
    ".git",
    ".hg",
    ".svn",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
})


def _rgba(color: str, alpha: int) -> str:
    value = QColor(color)
    return f"rgba({value.red()}, {value.green()}, {value.blue()}, {max(0, min(255, alpha))})"


class GlassCanvas(QWidget):
    """Paint the material only inside the rounded application silhouette."""

    def __init__(self) -> None:
        super().__init__()
        self._dark = False
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_dark(self, dark: bool) -> None:
        if self._dark == dark:
            return
        self._dark = dark
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self._dark:
            base = QColor(28, 30, 35, 255)
        else:
            base = QColor(246, 247, 249, 255)
        # Keep the desktop visible only outside the actual rounded window;
        # painting the whole canvas creates a second square outer frame.
        window_margin = 10.0
        window_rect = QRectF(
            window_margin,
            window_margin,
            max(0.0, self.width() - window_margin * 2),
            max(0.0, self.height() - window_margin * 2),
        )
        window_path = QPainterPath()
        window_path.addRoundedRect(window_rect, 20.0, 20.0)
        painter.setClipPath(window_path)
        painter.fillRect(self.rect(), base)


def _apply_native_glass(window: QWidget, dark: bool, effect_mode: str = "liquid") -> bool:
    """Ask Windows DWM for Acrylic; failure safely leaves the Qt glass fallback."""
    if sys.platform != "win32" or os.environ.get("QT_QPA_PLATFORM", "").casefold() in {"offscreen", "minimal"}:
        return False
    try:
        hwnd = wintypes.HWND(int(window.winId()))
        dwmapi = ctypes.windll.dwmapi
        dwmapi.DwmSetWindowAttribute.argtypes = [
            wintypes.HWND,
            wintypes.DWORD,
            wintypes.LPCVOID,
            wintypes.DWORD,
        ]
        dwmapi.DwmSetWindowAttribute.restype = wintypes.HRESULT
        # DWMSBT_NONE disables the system material; TRANSIENTWINDOW enables
        # the inexpensive Windows Acrylic backdrop used by frosted mode.
        value = ctypes.c_int(1 if effect_mode == "none" else 3)
        result = dwmapi.DwmSetWindowAttribute(hwnd, 38, ctypes.byref(value), ctypes.sizeof(value))
        dark_value = ctypes.c_int(1 if dark else 0)
        dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark_value), ctypes.sizeof(dark_value))
        corner = ctypes.c_int(2)  # DWMWCP_ROUND.
        dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        return result == 0
    # This is an optional native enhancement.  In particular, Windows can
    # raise ``ctypes.ArgumentError`` while a frameless window is changing
    # state (restore/maximize/fullscreen).  Never let that abort Qt's event
    # loop; the painted fallback remains perfectly usable.
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        return False


def _scan_folder_code_files(
    folder: Path,
    interrupted=None,
    on_error=None,
    on_file=None,
    skip_generated_dirs: bool = False,
) -> list[Path]:
    """Find C sources without making the GUI thread walk a large tree."""
    found: list[Path] = []
    for root, directories, filenames in os.walk(
        str(folder),
        topdown=True,
        onerror=on_error,
        followlinks=False,
    ):
        if interrupted is not None and interrupted():
            break
        if skip_generated_dirs:
            directories[:] = [
                name for name in directories
                if name.casefold() not in GENERATED_DIR_NAMES
            ]
        directories.sort(key=str.casefold)
        for filename in sorted(filenames, key=str.casefold):
            if Path(filename).suffix.lower() in SUPPORTED_EXTENSIONS:
                path = Path(root) / filename
                found.append(path)
                if on_file is not None:
                    on_file(path)
    return found


def _diagnostic_matches_query(diagnostic: Diagnostic, query: str) -> bool:
    """Match diagnostics by file identity as well as rule text.

    File names are not guaranteed to be unique in a project, so include both
    the stem and normalized full path.  Splitting the query makes searches
    such as ``drivers LAN8671`` useful without introducing a second filter
    control into the compact check panel.
    """
    haystack = " ".join(
        (
            diagnostic.path.name,
            diagnostic.path.stem,
            str(diagnostic.path),
            diagnostic.path.as_posix(),
            diagnostic.rule_id,
            diagnostic.message,
            diagnostic.expected,
            str(diagnostic.line),
        )
    ).casefold()
    return all(token in haystack for token in query.casefold().split())


class CCodeHighlighter(QSyntaxHighlighter):
    """Small C highlighter with separate light and VS Code Dark palettes."""

    MAX_HIGHLIGHT_CHARS = 250_000

    DARK_COLORS = {
        "text": "#D4D4D4",
        "keyword": "#569CD6",
        "type": "#4EC9B0",
        "string": "#CE9178",
        "comment": "#6A9955",
        "number": "#B5CEA8",
        "preprocessor": "#C586C0",
        "function": "#DCDCAA",
        "operator": "#D4D4D4",
    }
    LIGHT_COLORS = {
        "text": "#1F2328",
        "keyword": "#0000FF",
        "type": "#267F99",
        "string": "#A31515",
        "comment": "#008000",
        "number": "#098658",
        "preprocessor": "#AF00DB",
        "function": "#795E26",
        "operator": "#1F2328",
    }

    def __init__(self, document, dark: bool = False):
        super().__init__(document)
        colors = self.DARK_COLORS if dark else self.LIGHT_COLORS
        self.formats = {name: self._make_format(color) for name, color in colors.items()}
        self._keywords = {
            "if", "else", "for", "while", "do", "switch", "case", "default", "break", "continue",
            "return", "goto", "sizeof", "typedef", "struct", "union", "enum", "const", "static",
            "extern", "volatile", "register", "inline", "restrict", "auto", "signed", "unsigned",
        }
        self._types = {
            "void", "char", "short", "int", "long", "float", "double", "_Bool", "size_t",
            "uint8_t", "uint16_t", "uint32_t", "uint64_t", "int8_t", "int16_t", "int32_t", "int64_t",
        }
        self._enabled = True
        self._patterns = (
            (re.compile(r"\b(?:" + "|".join(sorted(self._types, key=len, reverse=True)) + r")\b"), "type"),
            (re.compile(r"\b(?:" + "|".join(sorted(self._keywords, key=len, reverse=True)) + r")\b"), "keyword"),
            (re.compile(r"\b(?:0[xX][0-9A-Fa-f]+|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\b"), "number"),
            (re.compile(r"\b[A-Za-z_]\w*(?=\s*\()"), "function"),
            (re.compile(r"[+\-*/%=&|!<>?:~^]+"), "operator"),
        )

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    @staticmethod
    def _make_format(color: str, bold: bool = False) -> QTextCharFormat:
        text_format = QTextCharFormat()
        text_format.setForeground(QColor(color))
        if bold:
            text_format.setFontWeight(QFont.Weight.Bold)
        return text_format

    def highlightBlock(self, text: str) -> None:
        if not self._enabled:
            return
        masked = list(text)

        def paint(start: int, end: int, category: str) -> None:
            if end <= start:
                return
            self.setFormat(start, end - start, self.formats[category])
            masked[start:end] = " " * (end - start)

        position = 0
        in_block_comment = self.previousBlockState() == 1
        while position < len(text):
            if in_block_comment:
                end = text.find("*/", position)
                if end < 0:
                    paint(position, len(text), "comment")
                    self.setCurrentBlockState(1)
                    return
                paint(position, end + 2, "comment")
                position = end + 2
                in_block_comment = False
                continue
            if text.startswith("//", position):
                paint(position, len(text), "comment")
                break
            if text.startswith("/*", position):
                end = text.find("*/", position + 2)
                if end < 0:
                    paint(position, len(text), "comment")
                    in_block_comment = True
                    break
                paint(position, end + 2, "comment")
                position = end + 2
                continue
            if text[position] in {"\"", "'"}:
                quote = text[position]
                end = position + 1
                while end < len(text):
                    if text[end] == "\\":
                        end += 2
                        continue
                    if text[end] == quote:
                        end += 1
                        break
                    end += 1
                paint(position, end, "string")
                position = end
                continue
            position += 1
        self.setCurrentBlockState(1 if in_block_comment else 0)

        if text.lstrip().startswith("#"):
            start = len(text) - len(text.lstrip())
            directive_end = text.find(" ", start)
            directive_end = len(text) if directive_end < 0 else directive_end
            paint(start, directive_end, "preprocessor")

        masked_text = "".join(masked)
        for pattern, category in self._patterns:
            for match in pattern.finditer(masked_text):
                self.setFormat(match.start(), match.end() - match.start(), self.formats[category])


class LineNumberArea(QWidget):
    def __init__(self, editor: "CodeView") -> None:
        super().__init__(editor)
        self.editor = editor
        self.setObjectName("lineNumberArea")

    def sizeHint(self) -> QSize:
        return QSize(self.editor.line_number_area_width(), 0)

    def paintEvent(self, event) -> None:
        self.editor.paint_line_numbers(event)


class CodeView(QPlainTextEdit):
    """Read-only code view with lightweight line numbers for comparison and checks."""

    def __init__(self) -> None:
        super().__init__()
        self.line_number_area = LineNumberArea(self)
        self.blockCountChanged.connect(self.update_line_number_area_width)
        self.updateRequest.connect(self.update_line_number_area)
        self.update_line_number_area_width(0)

    def setPlainText(self, text: str) -> None:
        """Keep very large files responsive by skipping expensive colorization."""
        highlighter = getattr(self, "_ccf_highlighter", None)
        if highlighter is not None:
            highlighter.set_enabled(len(text) <= CCodeHighlighter.MAX_HIGHLIGHT_CHARS)
        super().setPlainText(text)

    def set_code_font_size(self, point_size: int) -> None:
        font = QFont(self.font())
        font.setPointSize(max(8, int(point_size)))
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance("    ") or 32)
        self.update_line_number_area_width(0)
        self.line_number_area.update()

    def line_number_area_width(self) -> int:
        digits = len(str(max(1, self.blockCount())))
        return 12 + self.fontMetrics().horizontalAdvance("9") * digits

    def update_line_number_area_width(self, _block_count: int) -> None:
        self.setViewportMargins(self.line_number_area_width(), 0, 0, 0)

    def update_line_number_area(self, rect: QRect, dy: int) -> None:
        if dy:
            self.line_number_area.scroll(0, dy)
        else:
            self.line_number_area.update(0, rect.y(), self.line_number_area.width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self.update_line_number_area_width(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        contents = self.contentsRect()
        self.line_number_area.setGeometry(
            QRect(contents.left(), contents.top(), self.line_number_area_width(), contents.height())
        )

    def paint_line_numbers(self, event) -> None:
        painter = QPainter(self.line_number_area)
        painter.fillRect(event.rect(), self.palette().alternateBase())
        painter.setPen(self.palette().mid().color())
        block = self.firstVisibleBlock()
        block_number = block.blockNumber()
        top = round(self.blockBoundingGeometry(block).translated(self.contentOffset()).top())
        bottom = top + round(self.blockBoundingRect(block).height())
        while block.isValid() and top <= event.rect().bottom():
            if block.isVisible() and bottom >= event.rect().top():
                painter.drawText(
                    0,
                    top,
                    self.line_number_area.width() - 6,
                    bottom - top,
                    Qt.AlignmentFlag.AlignRight,
                    str(block_number + 1),
                )
            block = block.next()
            top = bottom
            bottom = top + round(self.blockBoundingRect(block).height())
            block_number += 1


def preferred_font_family() -> str:
    available = set(QFontDatabase.families())
    return next((family for family in FONT_CANDIDATES if family in available), "Sans Serif")


def preferred_code_font_family() -> str:
    available = set(QFontDatabase.families())
    return next((family for family in CODE_FONT_CANDIDATES if family in available), "Consolas")


def tinted_icon(path: Path, color: str, size: int = 18) -> QIcon:
    """Render a monochrome SVG using the current semantic theme color."""
    renderer = QSvgRenderer(str(path))
    if not renderer.isValid():
        return QIcon()
    source = QPixmap(size, size)
    source.fill(Qt.GlobalColor.transparent)
    painter = QPainter(source)
    renderer.render(painter, QRectF(0, 0, size, size))
    painter.end()
    result = QPixmap(size, size)
    result.fill(Qt.GlobalColor.transparent)
    painter = QPainter(result)
    painter.drawPixmap(0, 0, source)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
    painter.fillRect(result.rect(), QColor(color))
    painter.end()
    return QIcon(result)


def _glass_material_for_mode(mode: str):
    if GlassMaterial is None:
        return None
    frosted = mode == "frosted"
    return GlassMaterial(
        thickness=0.14 if frosted else 0.16,
        frost=0.68 if frosted else 0.18,
        strength=10.0 if frosted else 14.0,
        bevel=28.0,
        pad=36.0,
        disp_glow=12.0 if frosted else 30.0,
        reflect=0.42,
        max_frost_sigma=5.0 if frosted else 2.5,
    )


class _GlassRenderSignals(QObject):
    completed = pyqtSignal(object, int, float)
    failed = pyqtSignal(int)


class _GlassRenderTask(QRunnable):
    """Render one latest-only glass frame away from the GUI thread."""

    def __init__(self, pixels, origin: QPoint, dpr: float, size: tuple[int, int], mode: str, request_id: int):
        super().__init__()
        self.pixels = pixels
        self.origin = origin
        self.dpr = dpr
        self.size = size
        self.mode = mode
        self.request_id = request_id
        self.signals = _GlassRenderSignals()

    def run(self) -> None:
        try:
            material = _glass_material_for_mode(self.mode)
            if material is None or array_to_qimage is None or GlassRenderer is None:
                self.signals.failed.emit(self.request_id)
                return
            renderer = GlassRenderer(material, *self.size, 20.0)
            renderer._ensure_kernel(self.dpr)
            pad, panel_w, panel_h = renderer._pad, renderer._pw, renderer._ph
            grid_w, grid_h = panel_w + 2 * pad, panel_h + 2 * pad
            grid_x = int(self.origin.x() * self.dpr) - pad
            grid_y = int(self.origin.y() * self.dpr) - pad
            height, width = self.pixels.shape[:2]
            if width < 1 or height < 1:
                self.signals.failed.emit(self.request_id)
                return
            if 0 <= grid_x <= width - grid_w and 0 <= grid_y <= height - grid_h:
                padded = self.pixels[grid_y:grid_y + grid_h, grid_x:grid_x + grid_w]
            else:
                xs = np.clip(np.arange(grid_x, grid_x + grid_w), 0, width - 1)
                ys = np.clip(np.arange(grid_y, grid_y + grid_h), 0, height - 1)
                padded = self.pixels[np.ix_(ys, xs)]
            result = renderer._kernel.apply(padded, scatter=self.mode == "frosted")
            image = array_to_qimage(result)
            image.setDevicePixelRatio(self.dpr)
            self.signals.completed.emit(image, self.request_id, self.dpr)
        except Exception:
            self.signals.failed.emit(self.request_id)


class GlassBar(QFrame):
    """A small chrome surface with selectable visual material."""

    def __init__(
        self,
        parent: QWidget | None = None,
        screen_backdrop=None,
        edge: str = "top",
        effect_mode: str = "liquid",
    ) -> None:
        super().__init__(parent)
        self._screen_backdrop = screen_backdrop
        self._edge = edge if edge in {"top", "bottom"} else "top"
        self._effect_mode = effect_mode if effect_mode in EFFECT_MODES else "liquid"
        self._backdrop = None
        self._refracted = None
        self._render_in_progress = False
        self._render_request_id = 0
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self._refresh_refraction)
        if self._screen_backdrop is not None:
            self._screen_backdrop.changed.connect(self._on_screen_backdrop_changed)

    def _on_screen_backdrop_changed(self) -> None:
        # ScreenBackdrop already coalesces unchanged frames. Rebuild the
        # small chrome surface only when a new desktop frame arrives.
        owner = QWidget.window(self)
        if (
            not getattr(owner, "_glass_capture_suspended", False)
            and self._effect_mode in {"liquid", "frosted"}
        ):
            self._refresh_timer.start(0)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if self._render_in_progress:
            return
        if (
            not getattr(QWidget.window(self), "_glass_capture_suspended", False)
            and self._effect_mode in {"liquid", "frosted"}
        ):
            self._refresh_timer.start(0)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Cache once after a resize; never perform a full pixel resample per
        # paint or per editor scroll.
        if (
            not getattr(QWidget.window(self), "_glass_capture_suspended", False)
            and self._effect_mode in {"liquid", "frosted"}
        ):
            self._refresh_timer.start(140)

    def _refresh_refraction(self) -> None:
        if (
            self._effect_mode not in {"liquid", "frosted"}
            or getattr(QWidget.window(self), "_glass_capture_suspended", False)
        ):
            return
        self._render_request_id += 1
        request_id = self._render_request_id
        if GlassMaterial is None or GlassRenderer is None:
            return
        if self.width() < 2 or self.height() < 2:
            return
        if self._render_in_progress:
            return
        try:
            backdrop = self._screen_backdrop
            screen_pixels = backdrop.array() if backdrop is not None else None
            if screen_pixels is not None:
                self._backdrop = backdrop
                origin = self.mapToGlobal(QPoint(0, 0)) - backdrop.global_origin()
            else:
                # Safe fallback for non-Windows/offscreen runs.
                host = self.parentWidget()
                if host is None or WidgetBackdrop is None:
                    return
                if self._backdrop is None or self._backdrop is backdrop:
                    self._backdrop = WidgetBackdrop(host, exclude=self)
                self._backdrop.refresh()
                screen_pixels = self._backdrop.array()
                origin = self.pos()
            if screen_pixels is None:
                return
            dpr = self._backdrop.dpr()
            size = (self.width(), self.height())
            task = _GlassRenderTask(screen_pixels, origin, dpr, size, self._effect_mode, request_id)
            task.signals.completed.connect(self._on_render_completed)
            task.signals.failed.connect(self._on_render_failed)
            self._render_in_progress = True
            pool = getattr(QWidget.window(self), "_glass_pool", QThreadPool.globalInstance())
            pool.start(task)
        except Exception:
            # Native/optional rendering must never prevent the workbench from
            # opening; the translucent QPainter fallback remains available.
            self._refracted = None

    def _on_render_completed(self, image, request_id: int, dpr: float) -> None:
        self._render_in_progress = False
        if request_id != self._render_request_id or self._effect_mode not in {"liquid", "frosted"}:
            if (
                self._effect_mode in {"liquid", "frosted"}
                and not getattr(QWidget.window(self), "_glass_capture_suspended", False)
            ):
                self._refresh_timer.start(0)
            return
        pixmap = QPixmap.fromImage(image)
        pixmap.setDevicePixelRatio(dpr)
        self._refracted = pixmap
        self.update()

    def _on_render_failed(self, request_id: int) -> None:
        self._render_in_progress = False
        if request_id == self._render_request_id:
            self._refracted = None
        if (
            self._effect_mode in {"liquid", "frosted"}
            and request_id != self._render_request_id
            and not getattr(QWidget.window(self), "_glass_capture_suspended", False)
        ):
            self._refresh_timer.start(0)

    def suspend_rendering(self) -> None:
        """Cancel pending chrome renders while the top-level window is resized."""
        self._render_request_id += 1
        self._refresh_timer.stop()
        # The cached pixmap has the previous width/height. Remove it now so
        # the paint fallback fills the entire new bar instead of leaving a
        # visibly incomplete glass strip during the resize gesture.
        self._refracted = None
        self.update()

    def resume_rendering(self) -> None:
        """Request one fresh chrome frame after a resize settles."""
        if (
            self.isVisible()
            and self._effect_mode in {"liquid", "frosted"}
            and not getattr(QWidget.window(self), "_glass_capture_suspended", False)
        ):
            self._refresh_timer.start(0)

    def set_effect_mode(self, mode: str) -> None:
        mode = mode if mode in EFFECT_MODES else "liquid"
        if mode == self._effect_mode:
            return
        self._effect_mode = mode
        self._render_request_id += 1
        self._refracted = None
        self._refresh_timer.stop()
        if (
            mode in {"liquid", "frosted"}
            and self.isVisible()
            and not getattr(QWidget.window(self), "_glass_capture_suspended", False)
        ):
            self._refresh_timer.start(0)
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        # TitleBar keeps a legacy ``window`` attribute for drag actions, so
        # call QWidget.window explicitly instead of resolving that shadowed
        # attribute as a method.
        owner = QWidget.window(self)
        dark = bool(getattr(owner, "dark_mode", False))
        gradient = QLinearGradient(0, 0, 0, max(1, self.height()))
        refractive = self._effect_mode == "liquid" and self._refracted is not None
        if self._effect_mode == "none":
            base = QColor(28, 30, 35, 255) if dark else QColor(246, 247, 249, 255)
            gradient.setColorAt(0.0, base)
            gradient.setColorAt(0.5, base)
            gradient.setColorAt(1.0, base)
            highlight = QColor(255, 255, 255, 0)
            depth = QColor(0, 0, 0, 0)
        elif dark:
            if self._effect_mode == "frosted":
                gradient.setColorAt(0.0, QColor(47, 55, 70, 88))
                gradient.setColorAt(0.46, QColor(29, 34, 45, 68))
                gradient.setColorAt(1.0, QColor(17, 21, 29, 104))
                highlight = QColor(255, 255, 255, 84)
                depth = QColor(0, 0, 0, 34)
            else:
                gradient.setColorAt(0.0, QColor(47, 55, 70, 56 if refractive else 208))
                gradient.setColorAt(0.46, QColor(29, 34, 45, 28 if refractive else 150))
                gradient.setColorAt(1.0, QColor(17, 21, 29, 64 if refractive else 172))
                highlight = QColor(255, 255, 255, 72 if refractive else 52)
                depth = QColor(0, 0, 0, 46 if refractive else 58)
        else:
            if self._effect_mode == "frosted":
                gradient.setColorAt(0.0, QColor(255, 255, 255, 94))
                gradient.setColorAt(0.46, QColor(247, 249, 253, 68))
                gradient.setColorAt(1.0, QColor(232, 234, 238, 104))
                highlight = QColor(255, 255, 255, 126)
                depth = QColor(70, 76, 84, 26)
            else:
                gradient.setColorAt(0.0, QColor(255, 255, 255, 58 if refractive else 226))
                gradient.setColorAt(0.46, QColor(247, 249, 253, 22 if refractive else 150))
                gradient.setColorAt(1.0, QColor(232, 234, 238, 56 if refractive else 172))
                highlight = QColor(255, 255, 255, 118 if refractive else 108)
                depth = QColor(70, 76, 84, 32 if refractive else 38)

        # A child widget is rectangular even when its parent has a rounded
        # stylesheet. Clip the custom material explicitly, otherwise this
        # paint pass fills the four transparent window corners.
        radius = min(20.0, max(0.0, self.height() / 2.0))
        width = float(self.width())
        height = float(self.height())
        path = QPainterPath()
        if self._edge == "bottom":
            path.moveTo(0.0, 0.0)
            path.lineTo(width, 0.0)
            path.lineTo(width, max(0.0, height - radius))
            path.quadTo(width, height, width - radius, height)
            path.lineTo(radius, height)
            path.quadTo(0.0, height, 0.0, max(0.0, height - radius))
            path.closeSubpath()
        else:
            path.moveTo(0.0, height)
            path.lineTo(0.0, radius)
            path.quadTo(0.0, 0.0, radius, 0.0)
            path.lineTo(width - radius, 0.0)
            path.quadTo(width, 0.0, width, radius)
            path.lineTo(width, height)
            path.closeSubpath()
        painter.setClipPath(path)
        if self._refracted is not None:
            if self._effect_mode == "frosted":
                painter.setOpacity(0.72 if dark else 0.76)
            else:
                painter.setOpacity(0.48 if dark else 0.43)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            painter.drawPixmap(0, 0, self._refracted)
            painter.setOpacity(1.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawRect(self.rect())
        painter.setPen(QPen(highlight, 1))
        painter.drawLine(0, 0, self.width(), 0)
        if self._edge == "bottom":
            painter.setPen(QPen(depth, 1))
            painter.drawLine(0, self.height() - 1, self.width(), self.height() - 1)
        painter.end()


class TitleBar(GlassBar):
    """Small frameless title bar with familiar macOS traffic-light controls."""

    def __init__(self, window: QMainWindow, screen_backdrop=None, effect_mode: str = "liquid"):
        super().__init__(window, screen_backdrop, effect_mode=effect_mode)
        self.window = window
        self.setObjectName("titleBar")
        self.setFixedHeight(50)
        self._drag_offset: QPoint | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 0, 14, 0)
        layout.setSpacing(8)
        controls = QHBoxLayout()
        controls.setSpacing(8)
        for text, color, callback in (
            ("", "#ED6A5E", window.close),
            ("", "#F4BF4F", self._minimize_window),
            ("", "#61C554", window.toggle_maximize),
        ):
            button = QPushButton(text)
            button.setFixedSize(13, 13)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setStyleSheet(
                f"QPushButton {{ background: {color}; border: none; border-radius: 6px; }}"
                f"QPushButton:hover {{ border: 2px solid rgba(255,255,255,0.75); }}"
            )
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        layout.addStretch()
        title = QLabel("CCodeFormatter")
        title.setObjectName("windowTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)
        layout.addStretch()

    def _minimize_window(self) -> None:
        """Minimize frameless windows through the native window state."""
        self._drag_offset = None
        self.window.setWindowState(self.window.windowState() | Qt.WindowState.WindowMinimized)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.window.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.window.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self._drag_offset = None
        event.accept()


class DropArea(QFrame):
    files_dropped = pyqtSignal(list)
    clicked = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("dropArea")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        self.files_dropped.emit(paths)
        event.acceptProposedAction()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()


class ModeSegment(QFrame):
    """Compact segmented control, closer to a native desktop toolbar than a combo box."""

    mode_changed = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setObjectName("modeSegment")
        self._mode = "overwrite"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for text, mode in (("对比后覆盖", "overwrite"), ("对比后另存", "save_as")):
            button = QPushButton(text)
            button.setObjectName("modeSegmentButton")
            button.setCheckable(True)
            button.setProperty("mode", mode)
            button.clicked.connect(lambda checked, selected_mode=mode: self._select(selected_mode))
            group.addButton(button)
            self._buttons[mode] = button
            layout.addWidget(button)
            if mode == self._mode:
                button.setChecked(True)

    def _select(self, mode: str) -> None:
        self._mode = mode
        self.mode_changed.emit()

    def currentData(self) -> str:
        return self._mode

    def setCurrentData(self, mode: str) -> None:
        if mode not in {"overwrite", "save_as"} or mode == self._mode:
            return
        self._buttons[mode].setChecked(True)
        self._select(mode)


class EffectSegment(QFrame):
    """Three lightweight material choices; changing mode never blocks the UI."""

    effect_changed = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setObjectName("effectSegment")
        self._mode = "liquid"
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 3, 3, 3)
        layout.setSpacing(2)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._buttons: dict[str, QPushButton] = {}
        for text, mode in (("无特效", "none"), ("液态玻璃", "liquid"), ("毛玻璃", "frosted")):
            button = QPushButton(text)
            button.setObjectName("effectSegmentButton")
            button.setCheckable(True)
            button.clicked.connect(lambda checked, selected_mode=mode: self._select(selected_mode))
            group.addButton(button)
            self._buttons[mode] = button
            layout.addWidget(button)
            if mode == self._mode:
                button.setChecked(True)

    def _select(self, mode: str) -> None:
        if mode == self._mode:
            return
        self._mode = mode
        self.effect_changed.emit(mode)

    def currentData(self) -> str:
        return self._mode

    def setCurrentData(self, mode: str) -> None:
        if mode not in EFFECT_MODES or mode == self._mode:
            return
        self._buttons[mode].setChecked(True)
        self._select(mode)

    def set_allowed_modes(self, modes: set[str]) -> None:
        """Enable only the materials safe for the current window state."""
        for mode, button in self._buttons.items():
            button.setEnabled(mode in modes)


class DiffOverview(QWidget):
    """Clickable two-sided location map for the comparison view."""

    difference_selected = pyqtSignal(int)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("diffOverview")
        # Two independent tracks need a real gap.  At 24 px the old
        # coordinates overlapped, so the active outline looked like a second
        # scrollbar and colored blocks were painted on top of each other.
        self.setFixedWidth(34)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._blocks: list[tuple[str, int, int, int, int]] = []
        self._before_lines = 1
        self._after_lines = 1
        self._current = -1
        self._dark = False
        self._viewport_fraction = 0.0
        self._viewport_size = 1.0

    def set_differences(
        self,
        blocks: list[tuple[str, int, int, int, int]],
        before_lines: int,
        after_lines: int,
        current: int,
    ) -> None:
        self._blocks = blocks
        self._before_lines = max(before_lines, 1)
        self._after_lines = max(after_lines, 1)
        self._current = current
        self.update()

    def set_dark(self, dark: bool) -> None:
        self._dark = dark
        self.update()

    def set_viewport(self, value: int, maximum: int, page_step: int) -> None:
        """Move the visible-window marker with the code editor scrollbars."""
        total = max(maximum + page_step, page_step, 1)
        self._viewport_fraction = min(max(value / total, 0.0), 1.0)
        self._viewport_size = min(max(page_step / total, 0.05), 1.0)
        self.update()

    def _block_rect(self, start: int, end: int, total: int, x: float, width: float) -> QRectF:
        top_padding = 7.0
        usable_height = max(float(self.height()) - top_padding * 2, 1.0)
        top = top_padding + usable_height * min(max(start, 0), total) / total
        bottom = top_padding + usable_height * min(max(end, start + 1), total) / total
        return QRectF(x, top, width, max(bottom - top, 3.0))

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        usable_height = max(float(self.height()) - 14.0, 1.0)
        palette = palette_for(self._dark)
        background = QColor(palette.button)
        track = QColor(palette.border)
        colors = {
            "replace": QColor(palette.warning),
            "delete": QColor(palette.danger),
            "insert": QColor(palette.success),
        }
        accent = QColor(palette.accent)
        painter.fillRect(self.rect(), background)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(track)
        left_x = 6.0
        track_width = 7.0
        right_x = float(self.width()) - left_x - track_width
        painter.drawRoundedRect(QRectF(left_x, 7.0, track_width, max(self.height() - 14, 1)), 3.5, 3.5)
        painter.drawRoundedRect(QRectF(right_x, 7.0, track_width, max(self.height() - 14, 1)), 3.5, 3.5)

        viewport_height = max(10.0, usable_height * self._viewport_size)
        viewport_top = 7.0 + usable_height * self._viewport_fraction
        viewport_top = min(viewport_top, 7.0 + usable_height - viewport_height)
        viewport_color = QColor(palette.accent)
        viewport_color.setAlpha(42 if self._dark else 30)
        painter.setBrush(viewport_color)
        painter.setPen(QPen(accent, 1.0))
        painter.drawRoundedRect(
            QRectF(2.0, viewport_top, self.width() - 4.0, viewport_height),
            4.0,
            4.0,
        )
        painter.setPen(Qt.PenStyle.NoPen)

        for index, (tag, before_start, before_end, after_start, after_end) in enumerate(self._blocks):
            color = colors.get(tag, colors["replace"])
            painter.setBrush(color)
            before_rect = self._block_rect(before_start, before_end, self._before_lines, left_x, track_width)
            after_rect = self._block_rect(after_start, after_end, self._after_lines, right_x, track_width)
            painter.drawRoundedRect(before_rect, 2.5, 2.5)
            painter.drawRoundedRect(after_rect, 2.5, 2.5)
            if index == self._current:
                top = min(before_rect.top(), after_rect.top()) - 1.0
                bottom = max(before_rect.bottom(), after_rect.bottom()) + 1.0
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(accent)
                painter.drawRoundedRect(QRectF(3.0, top, self.width() - 6.0, max(bottom - top, 4.0)), 3.0, 3.0)
                painter.setPen(Qt.PenStyle.NoPen)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self._blocks or event.button() != Qt.MouseButton.LeftButton:
            return
        usable_height = max(float(self.height()) - 14.0, 1.0)
        location = min(max((event.position().y() - 7.0) / usable_height, 0.0), 1.0)

        def distance(block: tuple[str, int, int, int, int]) -> float:
            _, before_start, before_end, after_start, after_end = block
            before_middle = (before_start + before_end) / (2 * self._before_lines)
            after_middle = (after_start + after_end) / (2 * self._after_lines)
            return abs(location - (before_middle + after_middle) / 2)

        self.difference_selected.emit(min(range(len(self._blocks)), key=lambda index: distance(self._blocks[index])))


class PickerIconProvider(QFileIconProvider):
    def __init__(self, folder_icon: QIcon, code_icon: QIcon) -> None:
        super().__init__()
        self.folder_icon = folder_icon
        self.code_icon = code_icon

    def icon(self, info) -> QIcon:
        if hasattr(info, "isDir") and info.isDir():
            return self.folder_icon
        if hasattr(info, "isFile") and info.isFile() and Path(info.fileName()).suffix.lower() in SUPPORTED_EXTENSIONS:
            return self.code_icon
        return super().icon(info)


class FolderFirstProxy(QSortFilterProxyModel):
    def lessThan(self, left, right) -> bool:
        model = self.sourceModel()
        left_is_dir = model.isDir(left)
        right_is_dir = model.isDir(right)
        if left_is_dir != right_is_dir:
            return left_is_dir
        return model.fileName(left).casefold() < model.fileName(right).casefold()


class FolderFirstItem(QTreeWidgetItem):
    def __lt__(self, other) -> bool:
        left_is_folder = self.data(0, ITEM_KIND_ROLE) == "folder"
        right_is_folder = other.data(0, ITEM_KIND_ROLE) == "folder"
        if left_is_folder != right_is_folder:
            return left_is_folder
        left_path = str(self.data(0, Qt.ItemDataRole.UserRole) or self.text(0))
        right_path = str(other.data(0, Qt.ItemDataRole.UserRole) or other.text(0))
        return Path(left_path).name.casefold() < Path(right_path).name.casefold()


GUIDE_SECTIONS = (
    (
        "快速开始",
        """
        <h1>CCodeFormatter 使用指南</h1>
        <p class='lead'>这是一个本地运行的 C 代码检查、模板格式化和安全输出工具。</p>
        <h2>最短操作路径</h2>
        <ol>
          <li>点击“选择文件 / 文件夹”，或把 .c/.h 文件、文件夹拖到中央区域。</li>
          <li>左侧工作区会展开目录；点击文件后会自动切换当前文件并触发检查。</li>
          <li>右侧“检查格式”用于只读发现问题；“格式化预览”只生成左右对比，不会立即写文件。</li>
          <li>确认右侧结果后，再选择覆盖原文件或另存为新文件。</li>
          <li>底部可切换“无特效 / 液态玻璃 / 毛玻璃”；液态玻璃和毛玻璃都会实时采样背景，毛玻璃会额外进行 NumPy 柔化。</li>
        </ol>
        <div class='tip'><b>安全原则：</b>格式化、检查和写入是三个分开的动作。任何覆盖操作都需要明确确认。</div>
        """,
    ),
    (
        "检查格式",
        """
        <h1>检查格式</h1>
        <p>检查是只读分析，不修改源文件，也不会生成 .formatted 文件。</p>
        <h2>检查内容</h2>
        <ul>
          <li>词法分析：字符串、字符常量、注释、预处理指令、运算符和无法识别字符。</li>
          <li>语法分析：括号/方括号/大括号、return/break/continue 上下文、条件编译配对和分支顺序。</li>
          <li>规则检查：头文件引用顺序、include guard、命名、缩进、控制语句大括号、宏、指针、浮点比较、变量初始化等。</li>
        </ul>
        <h2>处理问题</h2>
        <p>右侧列表可以按文件名、规则或文字搜索，也可以按错误/警告筛选。点击问题会自动切换文件、定位行列并显示“违反”和“应当”。左侧文件树会显示文件问题数，文件夹显示子文件累计问题数。</p>
        <p>只有明确安全的缩进和控制语句大括号问题允许生成安全修复预览；修复仍然必须经过对比确认。</p>
        """,
    ),
    (
        "格式化与模板",
        """
        <h1>格式化与模板</h1>
        <p>格式化会读取当前 .c/.h 文件，并按照当前模板推断缩进、括号、运算符、逗号和宏对齐方式。</p>
        <h2>模板优先级</h2>
        <ol>
          <li>当前文件所在项目或上级目录的 .ccfconfig.json 指定模板。</li>
          <li>用户保存的自定义模板。</li>
          <li>程序内部嵌入的默认 template.c / template.h。</li>
        </ol>
        <h2>自定义格式</h2>
        <p>点击“模板：…”打开模板编辑器，分别修改 template.c 和 template.h，保存为新的模板名称。模板中的空格、换行、缩进和括号风格会用于后续格式化；将模板应用到当前项目后，会写入 .ccfconfig.json。</p>
        <div class='tip'><b>发布说明：</b>默认模板已经嵌入程序，发送单文件 EXE 时不需要额外发送模板文件。自定义模板属于用户配置，不会覆盖内置默认模板。</div>
        """,
    ),
    (
        "对比与审查",
        """
        <h1>对比与审查</h1>
        <p>中央对比页左边是格式化前，右边是模板结果。删除、插入和替换会按行高亮，替换行在小范围差异中还会按字符细分。</p>
        <ul>
          <li>两边垂直滚动和水平滚动同步；行数不同也会按比例保持阅读位置。</li>
          <li>左侧差异概览条可以点击跳到对应差异；“上一处/下一处”用于逐处审查。</li>
          <li>“忽略空白”只影响对比显示，不会改变实际输出内容。</li>
          <li>“复制格式化结果”复制右侧全文；“导出差异补丁”保存标准 UTF-8 unified diff，可用于审查或 Git。</li>
        </ul>
        <p>大文件会自动降低字符级高亮密度，保留行级差异，以避免界面卡顿。</p>
        """,
    ),
    (
        "输出与安全",
        """
        <h1>输出与安全</h1>
        <h2>对比后覆盖</h2>
        <p>覆盖前会检查源文件指纹。如果源文件在生成对比后被其他程序修改，旧结果会被拒绝写入。覆盖原文件前会再次确认；覆盖操作不可撤销，请先确认对比结果。</p>
        <h2>对比后另存</h2>
        <p>选择新的 .c/.h 路径保存，不会修改原文件。目标已存在时会再次确认。程序会记住最近一次输出目录。</p>
        <h2>批量生成</h2>
        <ul>
          <li>“批量生成 .formatted 文件”让你选择输出目录，后台执行且可取消。</li>
          <li>可只生成最近一次检查发现问题的文件。</li>
          <li>可保留选中文件夹的目录结构；同名结果会自动使用冲突安全名称。</li>
          <li>“打开输出目录”可以快速打开最近的另存或批量输出位置。</li>
        </ul>
        <div class='tip'><b>编码保护：</b>读取和写入会保留 UTF-8/GB18030 等可识别编码、BOM、CRLF/LF/CR 换行风格和文件权限；无法无损编码时会拒绝写入。</div>
        """,
    ),
    (
        "工作区与性能",
        """
        <h1>工作区与性能</h1>
        <ul>
          <li>选择文件和文件夹使用同一个入口，文件夹会递归加载 .c/.h 文件，文件夹优先显示并可展开/收起。</li>
          <li>左侧搜索框只隐藏不删除项目项；右键文件或文件夹可以复制路径、打开所在文件夹或从工作区移除。</li>
          <li>工作区选择会自动记住，下次启动时恢复仍存在的路径；清空文件会清除记忆。</li>
          <li>大型工程可勾选“跳过常见生成目录”，扫描时忽略 .git、build、dist、.venv、__pycache__、node_modules 等目录；选项会被记住。</li>
          <li>文件夹扫描、批量检查、批量生成和单文件预览都在后台线程执行，窗口不会因为大工程计算而主动卡住。</li>
          <li>检查完成后可打开“查看文件总览”，按状态筛选或搜索文件；双击文件会直接切换到代码并定位首个问题。</li>
          <li>可开启“文件变化时自动重新检查”；外部修改后旧对比结果会失效。</li>
        </ul>
        <p>窗口支持拖动、最大化、还原和右下角缩放；底部按钮切换浅色/深色主题。代码区支持 Ctrl + = 放大、Ctrl + - 缩小、Ctrl + 0 恢复字体大小。Windows 11 会启用系统 Acrylic 液态玻璃；旧系统或关闭透明效果时使用安全的 Qt 回退样式。</p>
        """,
    ),
    (
        "快捷键",
        """
        <h1>快捷键</h1>
        <table><tr><th>快捷键</th><th>作用</th></tr>
          <tr><td>Ctrl + O</td><td>选择 C 文件或文件夹</td></tr>
          <tr><td>Ctrl + F</td><td>聚焦当前页面的文件筛选或问题搜索</td></tr>
          <tr><td>Ctrl + G</td><td>跳转到代码行</td></tr>
          <tr><td>F5</td><td>重新检查已选文件</td></tr>
          <tr><td>Ctrl + Enter</td><td>生成当前文件对比</td></tr>
          <tr><td>Ctrl + = / Ctrl + -</td><td>放大 / 缩小代码字体</td></tr>
          <tr><td>Ctrl + 0</td><td>恢复代码字体大小</td></tr>
          <tr><td>Ctrl + Shift + P</td><td>打开命令面板</td></tr>
        </table>
        """,
    ),
    (
        "规则边界",
        """
        <h1>规则边界</h1>
        <p>程序内置的检查器是本地、可解释的词法和结构分析器，不是假装替代 GCC/Clang 的完整编译器。它会尽量在语法错误后继续分析，并给出文件、行、列、规则编号、违反说明和建议。</p>
        <p>你的格式化规则和自定义模板是最高优先级：新增功能只围绕展示、检查、预览、安全写入和工程管理扩展，不会擅自改变模板定义的排版意图。</p>
        <p>如果需要确认真正能否编译，请使用项目自己的编译器、静态分析器或 CI；CCodeFormatter 负责格式和规则审查。</p>
        """,
    ),
    (
        "EXE 与故障排查",
        """
        <h1>EXE 与故障排查</h1>
        <ul>
          <li>发布版本是单文件 dist/CCodeFormatter.exe，默认模板和图标已打包进去。</li>
          <li>启动单文件 EXE 时出现 _MEIxxxxx 临时目录是 PyInstaller 的正常解压目录，程序退出后会清理。</li>
          <li>如果旧 EXE 报 QtCore DLL 错误，请重新运行 scripts/build_exe.bat 或 scripts/build_exe.ps1 生成最新版本，不要混用旧构建目录。</li>
          <li>只支持 .c 和 .h；无法读取时会在检查结果中显示文件读取错误，不会静默覆盖。</li>
          <li>如果输出按钮不可用，先选择文件并生成对比；如果覆盖按钮不可用，说明还没有完成对比确认。</li>
        </ul>
        <p>程序默认不联网，源代码和配置只在本机处理。自定义模板、项目配置和最近选择路径分别保存在用户配置或项目目录中。</p>
        """,
    ),
)


class UsageGuideDialog(QDialog):
    """Searchable in-app guide kept alongside the implemented UI features."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._dark_mode = bool(getattr(parent, "dark_mode", False))
        self.setObjectName("usageGuide")
        self.setWindowTitle("CCodeFormatter 使用指南")
        self.resize(960, 680)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("使用指南")
        title.setObjectName("guideTitle")
        header.addWidget(title)
        header.addStretch()
        version = QLabel("功能说明 · 操作流程 · 安全边界")
        version.setObjectName("secondaryText")
        header.addWidget(version)
        layout.addLayout(header)

        self.search = QLineEdit()
        self.search.setObjectName("guideSearch")
        self.search.setPlaceholderText("搜索指南内容，例如：模板、批量、编码、快捷键…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_sections)
        layout.addWidget(self.search)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        self.sections = QListWidget()
        self.sections.setObjectName("guideSections")
        self.sections.setMinimumWidth(170)
        self.sections.setMaximumWidth(230)
        self.view = QTextEdit()
        self.view.setObjectName("guideView")
        self.view.setReadOnly(True)
        self.view.setAcceptRichText(True)
        self.view.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        splitter.addWidget(self.sections)
        splitter.addWidget(self.view)
        splitter.setSizes([200, 700])
        layout.addWidget(splitter, 1)

        close_button = QPushButton("完成")
        close_button.setObjectName("primaryButton")
        close_button.clicked.connect(self.accept)
        close_button.setMinimumHeight(36)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)

        self._sections = GUIDE_SECTIONS
        for title_text, _html in self._sections:
            self.sections.addItem(title_text)
        self.sections.currentRowChanged.connect(self._show_section)
        self.sections.setCurrentRow(0)

    def _show_section(self, row: int) -> None:
        if 0 <= row < len(self._sections):
            palette = palette_for(self._dark_mode)
            style = f"""
            <style>
              body {{ color: {palette.text}; font-family: 'Microsoft YaHei UI', sans-serif; font-size: 10pt; line-height: 1.55; }}
              h1 {{ color: {palette.text}; font-size: 18pt; margin: 0 0 12px 0; }}
              h2 {{ color: {palette.text}; font-size: 12pt; margin: 18px 0 7px 0; }}
              p, li {{ color: {palette.text}; }}
              .lead {{ color: {palette.secondary_text}; font-size: 11pt; }}
              .tip {{ color: {palette.text}; background: {palette.selection}; border: 1px solid {palette.border}; padding: 9px 11px; }}
              table {{ border-collapse: collapse; }} th, td {{ border-bottom: 1px solid {palette.separator}; padding: 7px 18px 7px 0; text-align: left; }}
              th {{ color: {palette.secondary_text}; }}
            </style>
            """
            self.view.setHtml(style + self._sections[row][1])
            self.view.verticalScrollBar().setValue(0)

    def _filter_sections(self, text: str) -> None:
        query = text.strip().casefold()
        for row, (title_text, content) in enumerate(self._sections):
            item = self.sections.item(row)
            item.setHidden(bool(query and query not in f"{title_text} {content}".casefold()))
        current = self.sections.currentItem()
        if current is None or current.isHidden():
            for row in range(self.sections.count()):
                item = self.sections.item(row)
                if not item.isHidden():
                    self.sections.setCurrentRow(row)
                    return
            self.view.setHtml("<h2>没有找到匹配内容</h2><p>请换一个关键词。</p>")


class CommandPaletteDialog(QDialog):
    """Keyboard-first command search for the growing feature set."""

    def __init__(self, parent: QWidget, commands: list[tuple[str, str, object]]) -> None:
        super().__init__(parent)
        self.setObjectName("commandPalette")
        self.setWindowTitle("命令面板")
        self.setModal(True)
        self.resize(560, 420)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(9)
        hint = QLabel("输入命令名称，按 Enter 执行")
        hint.setObjectName("secondaryText")
        layout.addWidget(hint)
        self.search = QLineEdit()
        self.search.setObjectName("commandSearch")
        self.search.setPlaceholderText("搜索命令…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_commands)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setObjectName("commandList")
        self.list.setUniformItemSizes(True)
        self.list.itemActivated.connect(self._run_selected)
        layout.addWidget(self.list, 1)
        self._commands = list(commands)
        self._visible_indices: list[int] = []
        self._filter_commands("")
        self.search.setFocus()

    def _filter_commands(self, text: str) -> None:
        query = text.strip().casefold()
        self.list.clear()
        self._visible_indices = []
        for index, (label, shortcut, _callback) in enumerate(self._commands):
            if query and query not in f"{label} {shortcut}".casefold():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, index)
            if shortcut:
                item.setToolTip(shortcut)
                item.setText(f"{label}    {shortcut}")
            self.list.addItem(item)
            self._visible_indices.append(index)
        if self.list.count():
            self.list.setCurrentRow(0)

    def _run_selected(self, item: QListWidgetItem) -> None:
        index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index, int) or not 0 <= index < len(self._commands):
            return
        callback = self._commands[index][2]
        if not callable(callback):
            return
        self.accept()
        QTimer.singleShot(0, callback)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            item = self.list.currentItem()
            if item is not None:
                self._run_selected(item)
                return
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            return
        super().keyPressEvent(event)


class CheckOverviewDialog(QDialog):
    """Compact, read-only overview of the latest workspace check."""

    def __init__(
        self,
        parent: QWidget,
        paths: list[Path],
        diagnostics: list[Diagnostic],
        on_open: object,
        checked_paths: list[Path] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("checkOverview")
        self.setWindowTitle("检查结果总览")
        self.setModal(True)
        self.resize(820, 540)
        self._on_open = on_open
        self._rows: list[tuple[Path, str, int]] = []
        checked = {
            Path(path).resolve()
            for path in (checked_paths if checked_paths is not None else paths)
        }
        grouped: dict[Path, list[Diagnostic]] = {}
        for diagnostic in diagnostics:
            grouped.setdefault(Path(diagnostic.path).resolve(), []).append(diagnostic)
        for path in paths:
            normalized = Path(path).resolve()
            items = grouped.get(normalized, [])
            if normalized not in checked:
                status = "未检查"
            elif any(item.severity == "error" for item in items):
                status = "错误"
            elif any(item.severity == "warning" for item in items):
                status = "警告"
            elif items:
                status = "提示"
            else:
                status = "通过"
            self._rows.append((normalized, status, len(items)))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 16)
        layout.setSpacing(9)
        title_row = QHBoxLayout()
        title = QLabel("本次检查")
        title.setObjectName("dialogTitle")
        title_row.addWidget(title)
        title_row.addStretch()
        self.summary = QLabel()
        self.summary.setObjectName("secondaryText")
        title_row.addWidget(self.summary)
        layout.addLayout(title_row)

        filter_row = QHBoxLayout()
        filter_row.setSpacing(7)
        self.search = QLineEdit()
        self.search.setObjectName("overviewSearch")
        self.search.setPlaceholderText("搜索文件名或路径…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter_rows)
        filter_row.addWidget(self.search, 1)
        self.status_filter = QComboBox()
        self.status_filter.setObjectName("overviewStatus")
        self.status_filter.addItem("全部状态", "")
        for label in ("错误", "警告", "提示", "通过", "未检查"):
            self.status_filter.addItem(label, label)
        self.status_filter.currentIndexChanged.connect(lambda _index: self._filter_rows())
        filter_row.addWidget(self.status_filter)
        layout.addLayout(filter_row)

        self.table = QTableWidget(0, 4)
        self.table.setObjectName("overviewTable")
        self.table.setHorizontalHeaderLabels(["文件", "状态", "问题", "完整路径"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._open_row)
        self.table.itemActivated.connect(lambda item: self._open_row(item.row(), item.column()))
        layout.addWidget(self.table, 1)

        hint = QLabel("双击文件可切换到代码并定位首个问题。此窗口只读，不会修改文件。")
        hint.setObjectName("secondaryText")
        layout.addWidget(hint)
        close_button = QPushButton("关闭")
        close_button.setObjectName("secondaryButton")
        close_button.setMinimumHeight(34)
        close_button.clicked.connect(self.reject)
        layout.addWidget(close_button, 0, Qt.AlignmentFlag.AlignRight)
        self._filter_rows()
        self.search.setFocus()

    def _filter_rows(self, _text: str = "") -> None:
        query = self.search.text().strip().casefold()
        selected_status = self.status_filter.currentData()
        self.table.setUpdatesEnabled(False)
        try:
            self.table.setRowCount(0)
            visible = 0
            counts = Counter(status for _path, status, _issues in self._rows)
            for path, status, issue_count in self._rows:
                path_text = str(path)
                haystack = f"{path.name} {path_text} {status}".casefold()
                if query and query not in haystack:
                    continue
                if selected_status and status != selected_status:
                    continue
                row = self.table.rowCount()
                self.table.insertRow(row)
                file_item = QTableWidgetItem(path.name)
                file_item.setData(Qt.ItemDataRole.UserRole, str(path))
                file_item.setToolTip(path_text)
                self.table.setItem(row, 0, file_item)
                status_item = QTableWidgetItem(status)
                status_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                status_item.setForeground(QColor(self._status_color(status)))
                self.table.setItem(row, 1, status_item)
                issues_item = QTableWidgetItem(str(issue_count))
                issues_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.table.setItem(row, 2, issues_item)
                self.table.setItem(row, 3, QTableWidgetItem(path_text))
                visible += 1
            total = len(self._rows)
            summary = " · ".join(
                f"{label} {counts.get(label, 0)}"
                for label in ("错误", "警告", "提示", "通过", "未检查")
                if counts.get(label, 0)
            ) or "暂无文件"
            self.summary.setText(f"显示 {visible} / {total} · {summary}")
        finally:
            self.table.setUpdatesEnabled(True)

    def _status_color(self, status: str) -> str:
        palette = palette_for(getattr(self.parent(), "dark_mode", False))
        return {
            "错误": palette.danger,
            "警告": palette.accent,
            "提示": palette.secondary_text,
            "通过": palette.success,
            "未检查": palette.tertiary_text,
        }.get(status, palette.text)

    def _open_row(self, row: int, _column: int) -> None:
        item = self.table.item(row, 0)
        if item is None or not callable(self._on_open):
            return
        self.accept()
        QTimer.singleShot(0, lambda: self._on_open(Path(item.data(Qt.ItemDataRole.UserRole))))


class BatchCheckThread(QThread):
    """Run the existing file inspection service without blocking the UI."""

    progress_changed = pyqtSignal(int, int, str)
    completed = pyqtSignal(object, object, object, bool, int)

    def __init__(self, paths: list[Path]) -> None:
        super().__init__()
        self.paths = list(paths)

    def run(self) -> None:
        sources: dict[Path, str] = {}
        diagnostics: list[Diagnostic] = []
        failures: list[str] = []
        processed = 0
        total = len(self.paths)
        for path in self.paths:
            if self.isInterruptionRequested():
                break
            self.progress_changed.emit(processed, total, path.name)
            try:
                result = inspect_file(path)
            except Exception as error:
                failures.append(f"{path.name}: {error}")
                diagnostics.append(Diagnostic(
                    path, 1, 1, 1, 2, "error", "file.read", "文件无法读取。", "请检查文件编码和权限。"
                ))
            else:
                sources[path] = result.source
                diagnostics.extend(result.diagnostics)
            processed += 1
            self.progress_changed.emit(processed, total, path.name)
        self.completed.emit(
            sources,
            diagnostics,
            failures,
            self.isInterruptionRequested(),
            processed,
        )


class FolderScanThread(QThread):
    """Enumerate selected folders away from the GUI thread."""

    progress_changed = pyqtSignal(str, int)
    completed = pyqtSignal(object, object, bool)

    def __init__(self, folders: list[Path], skip_generated_dirs: bool = False) -> None:
        super().__init__()
        self.folders = list(folders)
        self.skip_generated_dirs = skip_generated_dirs

    def run(self) -> None:
        results: list[tuple[Path, list[Path]]] = []
        failures: list[str] = []
        for folder in self.folders:
            if self.isInterruptionRequested():
                break
            try:
                scanned_files = 0

                def record_error(error: OSError) -> None:
                    failures.append(f"{error.filename or folder.name}: {error.strerror or error}")

                def report_progress(_path: Path) -> None:
                    nonlocal scanned_files
                    scanned_files += 1
                    if scanned_files == 1 or scanned_files % 100 == 0:
                        self.progress_changed.emit(folder.name, scanned_files)

                files = _scan_folder_code_files(
                    folder,
                    self.isInterruptionRequested,
                    record_error,
                    report_progress,
                    self.skip_generated_dirs,
                )
            except OSError as error:
                failures.append(f"{folder.name}: {error}")
                files = []
            self.progress_changed.emit(folder.name, len(files))
            results.append((folder, files))
        self.completed.emit(results, failures, self.isInterruptionRequested())


class BatchFormatThread(QThread):
    """Generate .formatted files in a user-selected directory."""

    progress_changed = pyqtSignal(int, int, str)
    completed = pyqtSignal(object, object, bool, int)

    def __init__(
        self,
        paths: list[Path],
        output_dir: Path,
        preserve_structure: bool = False,
        source_roots: list[Path] | None = None,
    ) -> None:
        super().__init__()
        self.paths = list(paths)
        self.output_dir = Path(output_dir)
        self.preserve_structure = preserve_structure
        self.source_roots = list(source_roots or [])
        self.jobs = list(zip(
            self.paths,
            self.output_paths(
                self.paths,
                self.output_dir,
                preserve_structure=self.preserve_structure,
                source_roots=self.source_roots,
            ),
        ))

    @staticmethod
    def output_paths(
        paths: list[Path],
        output_dir: Path,
        *,
        preserve_structure: bool = False,
        source_roots: list[Path] | None = None,
    ) -> list[Path]:
        """Create output names, optionally retaining selected folder structure."""
        output_dir = Path(output_dir)
        roots = [Path(root).resolve() for root in (source_roots or []) if Path(root).is_dir()]
        used: set[Path] = set()
        outputs: list[Path] = []
        for path in paths:
            path = Path(path)
            relative_parent = Path()
            if preserve_structure:
                matching_roots = [root for root in roots if path.resolve() == root or root in path.resolve().parents]
                if matching_roots:
                    root = max(matching_roots, key=lambda item: len(item.parts))
                    relative_parent = Path(root.name) / path.resolve().relative_to(root)
                    relative_parent = relative_parent.parent
            candidate = output_dir / relative_parent / f"{path.stem}.formatted{path.suffix}"
            number = 2
            while candidate in used:
                candidate = output_dir / relative_parent / f"{path.stem}.{number}.formatted{path.suffix}"
                number += 1
            used.add(candidate)
            outputs.append(candidate)
        return outputs

    def run(self) -> None:
        outputs: list[Path] = []
        failures: list[str] = []
        processed = 0
        total = len(self.paths)
        for path, output in self.jobs:
            if self.isInterruptionRequested():
                break
            self.progress_changed.emit(processed, total, path.name)
            try:
                outputs.append(format_file(path, output=output))
            except Exception as error:
                failures.append(f"{path.name}: {error}")
            processed += 1
            self.progress_changed.emit(processed, total, path.name)
        self.completed.emit(outputs, failures, self.isInterruptionRequested(), processed)


class PreviewThread(QThread):
    """Build one comparison result away from the Qt GUI thread."""

    completed = pyqtSignal(int, object, object, object, object, object)

    def __init__(
        self,
        request_id: int,
        path: Path,
        template_dir: Path | None,
        safe_repair: bool = False,
        indent_width: int = 4,
    ) -> None:
        super().__init__()
        self.request_id = request_id
        self.path = path
        self.template_dir = template_dir
        self.safe_repair = safe_repair
        self.indent_width = indent_width

    def run(self) -> None:
        try:
            original, metadata = read_source_with_metadata(self.path)
            if self.safe_repair:
                formatted = safe_repair_c_code(original, self.indent_width)
            else:
                project_profile = load_project_profile(self.path)
                template_dir = template_dir_for_use(self.path) if project_profile is not None else self.template_dir
                formatted = format_c_code(original, TemplateStyle.from_template(template_dir, self.path.suffix))
        except Exception as error:
            self.completed.emit(self.request_id, self.path, None, None, None, str(error))
            return
        self.completed.emit(self.request_id, self.path, original, formatted, metadata, None)


class ChevronTreeMixin:
    """Paint one chevron for expandable rows and no connector lines."""

    def _init_chevron(self, color: str) -> None:
        self._chevron_color = QColor(color)

    def drawBranches(self, painter, rect, index) -> None:
        model = self.model()
        if model is None or not model.hasChildren(index):
            return
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(self._chevron_color)
        pen.setWidthF(1.8)
        painter.setPen(pen)
        center = rect.center()
        if self.isExpanded(index):
            painter.drawLine(center.x() - 4, center.y() - 1, center.x(), center.y() + 3)
            painter.drawLine(center.x(), center.y() + 3, center.x() + 4, center.y() - 1)
        else:
            painter.drawLine(center.x() - 2, center.y() - 4, center.x() + 2, center.y())
            painter.drawLine(center.x() + 2, center.y(), center.x() - 2, center.y() + 4)
        painter.restore()


class ChevronTreeView(ChevronTreeMixin, QTreeView):
    def __init__(self, color: str) -> None:
        super().__init__()
        self._init_chevron(color)


class ChevronTreeWidget(ChevronTreeMixin, QTreeWidget):
    def __init__(self, color: str) -> None:
        super().__init__()
        self._init_chevron(color)


class UnifiedPathDialog(QDialog):
    """One picker that allows selecting code files and folders in the same view."""

    def __init__(self, parent: QWidget | None, start_directory: Path, icon_color: str = "#007AFF") -> None:
        super().__init__(parent)
        self.setObjectName("pathPicker")
        self.setWindowTitle("选择 C 文件或文件夹")
        self.resize(760, 520)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        hint = QLabel("可多选 .c/.h 文件或文件夹；文件夹会自动递归加载其中的代码文件。")
        hint.setObjectName("secondaryText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.AllDirs | QDir.Filter.Files | QDir.Filter.NoDotAndDotDot)
        self.model.setNameFilters(["*.c", "*.h"])
        self.model.setNameFilterDisables(False)
        self.proxy = FolderFirstProxy(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setDynamicSortFilter(True)
        self.proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.model.setIconProvider(PickerIconProvider(
            tinted_icon(ASSET_DIR / "tabler-folder.svg", icon_color, 18),
            tinted_icon(ASSET_DIR / "tabler-file-code.svg", icon_color, 18),
        ))
        self._start_directory = start_directory.resolve()
        drive_bar = QHBoxLayout()
        drive_bar.setSpacing(6)
        drive_bar.addWidget(QLabel("位置"))
        drives = [Path(info.absoluteFilePath()) for info in QDir.drives()]
        if not drives:
            drives = [Path(QDir.rootPath())]
        for drive in drives:
            button = QPushButton(drive.anchor or str(drive))
            button.setObjectName("pathDriveButton")
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.clicked.connect(lambda checked=False, path=drive: self._set_root(path))
            drive_bar.addWidget(button)
        drive_bar.addStretch()
        layout.addLayout(drive_bar)

        self.tree = ChevronTreeView(icon_color)
        self.tree.setObjectName("pathPickerTree")
        self.tree.setModel(self.proxy)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.tree.setAlternatingRowColors(False)
        self.tree.setAnimated(True)
        self.tree.setColumnWidth(0, 460)
        for column in (1, 2, 3):
            self.tree.hideColumn(column)
        layout.addWidget(self.tree, 1)
        self.model.directoryLoaded.connect(self._resort)
        self._set_root(Path(self._start_directory.anchor or QDir.rootPath()), self._start_directory)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok
        )
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("添加选中项")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _set_root(self, drive: Path, focus: Path | None = None) -> None:
        root = self.model.setRootPath(str(drive))
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)
        self.tree.setRootIndex(self.proxy.mapFromSource(root))
        target = focus if focus is not None and focus.anchor.lower() == drive.anchor.lower() else drive
        index = self.proxy.mapFromSource(self.model.index(str(target)))
        if index.isValid():
            self.tree.expand(index)
            self.tree.scrollTo(index)

    def _resort(self, _path: str) -> None:
        self.proxy.sort(0, Qt.SortOrder.AscendingOrder)

    def selected_paths(self) -> list[Path]:
        paths: list[Path] = []
        for proxy_index in self.tree.selectionModel().selectedRows(0):
            index = self.proxy.mapToSource(proxy_index)
            path = Path(self.model.filePath(index)).resolve()
            if path.is_file() and path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if path.is_file() or path.is_dir():
                paths.append(path)
        return paths


class FormatterWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.selected: list[Path] = []
        self.code_views: list[CodeView] = []
        self._selection_sources: list[Path] = []
        self.settings = QSettings("CCodeFormatter", "CCodeFormatter")
        self._tree_items: dict[Path, QTreeWidgetItem] = {}
        self.compare_path: Path | None = None
        self.compare_content = ""
        self.compare_metadata: SourceMetadata | None = None
        self.compare_source_digest: str | None = None
        self._last_output_directory: Path | None = None
        self.check_path: Path | None = None
        self.check_diagnostics: list[Diagnostic] = []
        self.check_sources: dict[Path, str] = {}
        self._check_worker: BatchCheckThread | None = None
        self._folder_scan_worker: FolderScanThread | None = None
        self._folder_scan_pending: list[tuple[list[Path], bool]] = []
        self._folder_scan_remember = True
        self._discard_folder_scan_result = False
        self._close_after_folder_scan = False
        self._format_worker: BatchFormatThread | None = None
        self._preview_worker: PreviewThread | None = None
        self._preview_pending: tuple[int, Path, bool, int] | None = None
        self._preview_request_id = 0
        self._restart_check_after_finish = False
        self._discard_check_result = False
        self._close_after_check = False
        self._close_after_format = False
        self._close_after_preview = False
        self._watched_paths: set[Path] = set()
        self._watch_pending_path: Path | None = None
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.fileChanged.connect(self._watched_file_changed)
        self._watch_reload_timer = QTimer(self)
        self._watch_reload_timer.setSingleShot(True)
        self._watch_reload_timer.setInterval(450)
        self._watch_reload_timer.timeout.connect(self._auto_recheck)
        self.template_dir = template_dir_for_use()
        self.template_profile = self.template_dir.name if self.template_dir is not None else DEFAULT_PROFILE
        self._syncing_scroll = False
        self._syncing_horizontal_scroll = False
        self._diff_blocks: list[tuple[str, int, int, int, int]] = []
        self._current_diff_index = -1
        saved_dark = self.settings.value("ui/dark_mode", False)
        self.dark_mode = str(saved_dark).strip().casefold() not in {"", "0", "false", "no", "off"}
        saved_effect = str(self.settings.value("ui/effect_mode", "liquid")).strip().casefold()
        self.effect_mode = saved_effect if saved_effect in EFFECT_MODES else "liquid"
        self._maximized = False
        self._normal_geometry = None
        self._window_state_changing = False
        # The native screen capturer must never run while a maximized
        # translucent window is being resized. It is resumed only after a
        # restore to the normal window size.
        self._glass_capture_suspended = False
        self._closing = False
        self._fullscreen_effect_lock = False
        self._effect_mode_before_fullscreen: str | None = None
        self._glass_ready = False
        self._screen_glass_backdrop = None
        self._screen_glass_started = False
        self._screen_glass_refresh_timer = QTimer(self)
        self._screen_glass_refresh_timer.setSingleShot(True)
        # Fast enough to feel immediate after a move, while still coalescing
        # the burst of native move/resize events into one screen capture.
        self._screen_glass_refresh_timer.setInterval(40)
        self._screen_glass_refresh_timer.timeout.connect(self._refresh_screen_glass)
        self._glass_resize_timer = QTimer(self)
        self._glass_resize_timer.setSingleShot(True)
        self._glass_resize_timer.setInterval(180)
        self._glass_resize_timer.timeout.connect(self._resume_glass_after_resize)
        if (
            ScreenBackdrop is not None
            and sys.platform == "win32"
            and os.environ.get("QT_QPA_PLATFORM", "").casefold() not in {"offscreen", "minimal"}
        ):
            self._screen_glass_backdrop = ScreenBackdrop(
                self,
                # Keep the material visibly live while the adaptive backdrop
                # still backs off when the desktop is unchanged.
                interval_ms=120,
                capture_margin=40,
            )
        self.font_family = preferred_font_family()
        try:
            self.code_font_size = max(8, min(18, int(self.settings.value("ui/code_font_size", 10))))
        except (TypeError, ValueError):
            self.code_font_size = 10
        self.setWindowTitle("CCodeFormatter")
        self.setWindowIcon(QIcon(str(APP_ICON_PATH)))
        self.setMinimumSize(900, 560)
        self.resize(1180, 760)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFont(QFont(self.font_family, 10))
        self._glass_pool = QThreadPool(self)
        # One background worker avoids two NumPy kernels saturating the CPU;
        # each GlassBar drops stale frames instead of growing a queue.
        self._glass_pool.setMaxThreadCount(1)
        self._build_ui()
        self._restore_ui_state()
        self._apply_theme()
        self._install_shortcuts()
        remembered_output = self.settings.value("output/last_directory", "")
        if str(remembered_output).strip() and Path(str(remembered_output)).is_dir():
            self._last_output_directory = Path(str(remembered_output)).resolve()
            self.open_output_button.setEnabled(True)
        self._restore_last_selection()

    def _install_shortcuts(self) -> None:
        for sequence, callback in (
            ("Ctrl+O", self._choose_paths),
            ("Ctrl+F", self._focus_search),
            ("Ctrl+G", self._goto_line),
            ("F5", self._check_selected),
            ("Ctrl+Return", self._format_selected),
            ("Ctrl+Shift+R", self._refresh_screen_glass),
            ("Ctrl+=", lambda: self._zoom_code_views(1)),
            ("Ctrl+-", lambda: self._zoom_code_views(-1)),
            ("Ctrl+0", self._reset_code_zoom),
            ("Ctrl+Shift+P", self.show_command_palette),
            ("F1", self.show_usage_guide),
        ):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)

    def _focus_search(self) -> None:
        """Focus the search field that matches the visible workspace page."""
        search = self.issue_search if self.center_stack.currentWidget() == self.check_page else self.file_search
        search.setFocus()
        search.selectAll()

    def _update_zoom_label(self) -> None:
        if hasattr(self, "zoom_label"):
            self.zoom_label.setText(f"代码 {self.code_font_size} pt")

    def _zoom_code_views(self, delta: int) -> None:
        new_size = max(8, min(18, self.code_font_size + int(delta)))
        if new_size == self.code_font_size:
            return
        self.code_font_size = new_size
        for editor in self.code_views:
            editor.set_code_font_size(new_size)
        self.settings.setValue("ui/code_font_size", new_size)
        self._update_zoom_label()
        self.status_label.setText(f"代码字体已调整为 {new_size} pt")

    def _reset_code_zoom(self) -> None:
        if self.code_font_size == 10:
            return
        self.code_font_size = 10
        for editor in self.code_views:
            editor.set_code_font_size(self.code_font_size)
        self.settings.setValue("ui/code_font_size", self.code_font_size)
        self._update_zoom_label()
        self.status_label.setText("代码字体已恢复为 10 pt")

    def show_usage_guide(self) -> None:
        dialog = UsageGuideDialog(self)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def show_command_palette(self) -> None:
        commands = [
            ("选择文件 / 文件夹", "Ctrl+O", self._choose_paths),
            ("重新检查工作区", "F5", self._check_selected),
            ("生成当前文件对比", "Ctrl+Enter", self._format_selected),
            ("批量生成 .formatted 文件", "", self._batch_format_selected),
            ("打开自定义格式模板", "", self._open_template_editor),
            ("导出当前检查报告", "", self._export_check_report),
            ("查看检查结果总览", "", self._show_check_overview),
            ("导出当前差异补丁", "", self._export_diff_patch),
            ("上一处问题", "", lambda: self._move_to_diagnostic(-1)),
            ("下一处问题", "", lambda: self._move_to_diagnostic(1)),
            ("上一处差异", "", lambda: self._move_to_difference(-1)),
            ("下一处差异", "", lambda: self._move_to_difference(1)),
            ("放大代码字体", "Ctrl+=", lambda: self._zoom_code_views(1)),
            ("缩小代码字体", "Ctrl+-", lambda: self._zoom_code_views(-1)),
            ("恢复代码字体", "Ctrl+0", self._reset_code_zoom),
            ("切换浅色 / 深色主题", "", self.toggle_theme),
            ("刷新玻璃背景", "Ctrl+Shift+R", self._refresh_screen_glass),
            ("打开使用指南", "F1", self.show_usage_guide),
        ]
        dialog = CommandPaletteDialog(self, commands)
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def _build_ui(self) -> None:
        canvas = GlassCanvas()
        self.canvas = canvas
        canvas.setObjectName("windowCanvas")
        canvas_layout = QVBoxLayout(canvas)
        # The rounded container is inset only to expose the true transparent
        # window corners; GlassCanvas clips its material to this same shape.
        canvas_layout.setContentsMargins(10, 10, 10, 10)
        canvas_layout.setSpacing(0)

        container = QWidget()
        container.setObjectName("mainContainer")
        root_layout = QVBoxLayout(container)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.title_bar = TitleBar(self, self._screen_glass_backdrop, self.effect_mode)
        root_layout.addWidget(self.title_bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setObjectName("workbenchSplitter")
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(1)
        splitter.addWidget(self._create_left_sidebar())
        splitter.addWidget(self._create_center_panel())
        splitter.addWidget(self._create_right_sidebar())
        splitter.setSizes([210, 650, 270])
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        self.workbench_splitter = splitter
        root_layout.addWidget(splitter, 1)

        footer = GlassBar(
            screen_backdrop=self._screen_glass_backdrop,
            edge="bottom",
            effect_mode=self.effect_mode,
        )
        self.footer = footer
        footer.setObjectName("statusBar")
        footer_layout = QHBoxLayout(footer)
        footer_layout.setContentsMargins(16, 7, 16, 7)
        self.status_label = QLabel("等待添加代码文件")
        self.status_label.setObjectName("secondaryText")
        footer_layout.addWidget(self.status_label)
        footer_layout.addStretch()
        safe = QLabel("安全模式 · 对比确认后写入")
        safe.setObjectName("safeText")
        footer_layout.addWidget(safe)
        self.effect_segment = EffectSegment()
        self.effect_segment.setToolTip("切换窗口材质；毛玻璃不进行实时屏幕折射，性能更稳定")
        self.effect_segment.effect_changed.connect(self._effect_mode_changed)
        footer_layout.addSpacing(10)
        footer_layout.addWidget(self.effect_segment)
        self.help_button = QToolButton()
        self.help_button.setObjectName("helpButton")
        self.help_button.setText("?")
        self.help_button.setToolTip("打开 CCodeFormatter 使用指南")
        self.help_button.setFixedSize(30, 30)
        self.help_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.help_button.clicked.connect(self.show_usage_guide)
        footer_layout.addSpacing(8)
        footer_layout.addWidget(self.help_button)
        self.theme_button = QToolButton()
        self.theme_button.setObjectName("themeButton")
        self.theme_button.setFixedSize(30, 30)
        self.theme_button.setIconSize(QSize(17, 17))
        self.theme_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        self.theme_button.setAutoRaise(False)
        self.theme_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.theme_button.clicked.connect(self.toggle_theme)
        footer_layout.addSpacing(10)
        footer_layout.addWidget(self.theme_button)
        self.zoom_label = QLabel()
        self.zoom_label.setObjectName("zoomLabel")
        self.zoom_label.setToolTip("代码字体：Ctrl + = 放大，Ctrl + - 缩小，Ctrl + 0 恢复")
        footer_layout.addSpacing(10)
        footer_layout.addWidget(self.zoom_label)
        self._update_zoom_label()
        root_layout.addWidget(footer)
        canvas_layout.addWidget(container)
        self.setCentralWidget(canvas)
        self.resize_grip = QSizeGrip(canvas)
        self.resize_grip.setObjectName("resizeGrip")
        self.resize_grip.setFixedSize(18, 18)
        self.resize_grip.raise_()

    def _restore_ui_state(self) -> None:
        geometry = self.settings.value("ui/geometry")
        if geometry:
            self.restoreGeometry(geometry)
        splitter_state = self.settings.value("ui/workbench_splitter")
        if splitter_state:
            self.workbench_splitter.restoreState(splitter_state)
        saved_mode = str(self.settings.value("ui/mode", "overwrite"))
        self.mode_segment.setCurrentData(saved_mode)
        saved_effect = str(self.settings.value("ui/effect_mode", "liquid")).strip().casefold()
        self.effect_segment.setCurrentData(saved_effect if saved_effect in EFFECT_MODES else "liquid")

    def _save_ui_state(self) -> None:
        if not self._maximized:
            self.settings.setValue("ui/geometry", self.saveGeometry())
        self.settings.setValue("ui/workbench_splitter", self.workbench_splitter.saveState())
        self.settings.setValue("ui/dark_mode", self.dark_mode)
        self.settings.setValue("ui/mode", self.mode_segment.currentData())
        self.settings.setValue("ui/effect_mode", self.effect_mode)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._glass_ready:
            self._glass_ready = True
            _apply_native_glass(self, self.dark_mode, self.effect_mode)
        self._sync_effect_mode()

    def _refresh_screen_glass(self) -> None:
        if (
            self._closing
            or self._window_state_changing
            or self._glass_capture_suspended
            or self.effect_mode not in {"liquid", "frosted"}
            or self._screen_glass_backdrop is None
        ):
            return
        try:
            self._screen_glass_backdrop.refresh()
        except (RuntimeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
            # Screen capture is optional and can briefly fail while DWM is
            # rebuilding a maximized layered window.  Keep the last frame and
            # let the next normal refresh recover instead of killing the UI.
            return

    def _sync_effect_mode(self) -> None:
        backdrop = self._screen_glass_backdrop
        if backdrop is None or self._closing or self._glass_capture_suspended:
            return
        if self.effect_mode not in {"liquid", "frosted"}:
            backdrop.set_live(False)
            return
        if not self._screen_glass_started:
            self._screen_glass_started = True
            backdrop.configure()
        backdrop.set_live(True)
        QTimer.singleShot(0, self._start_screen_glass)

    def _start_screen_glass(self) -> None:
        """Start capture only after a pending window-state transition settles."""
        if self._closing or self._window_state_changing or self._glass_capture_suspended:
            return
        backdrop = self._screen_glass_backdrop
        if backdrop is None or self.effect_mode not in {"liquid", "frosted"}:
            return
        try:
            backdrop.start()
        except (RuntimeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
            return

    def _schedule_screen_glass_refresh(self) -> None:
        if (
            not self._closing
            and not self._window_state_changing
            and not self._glass_capture_suspended
            and self.effect_mode in {"liquid", "frosted"}
            and self._screen_glass_backdrop is not None
        ):
            self._screen_glass_refresh_timer.start()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._schedule_screen_glass_refresh()

    def resizeEvent(self, event) -> None:
        if hasattr(self, "resize_grip"):
            self.resize_grip.move(self.width() - 30, self.height() - 30)
            self.resize_grip.raise_()
        super().resizeEvent(event)
        if not self._closing and not self._maximized:
            # A native resize can emit dozens of events per second. Keep the
            # old frame during the gesture and render only after the size is
            # stable; this prevents a queue of large NumPy kernels and also
            # keeps Windows Magnification out of the resize path.
            if not self._glass_capture_suspended:
                self._glass_capture_suspended = True
                self._screen_glass_refresh_timer.stop()
                if self._screen_glass_backdrop is not None:
                    self._screen_glass_backdrop.set_live(False)
                for bar in (getattr(self, "title_bar", None), getattr(self, "footer", None)):
                    if bar is not None:
                        bar.suspend_rendering()
            self._glass_resize_timer.start()

    def _resume_glass_after_resize(self) -> None:
        if self._closing or self._maximized or self._window_state_changing:
            return
        self._glass_capture_suspended = False
        for bar in (getattr(self, "title_bar", None), getattr(self, "footer", None)):
            if bar is not None:
                bar.resume_rendering()
        self._sync_effect_mode()
        self._schedule_screen_glass_refresh()

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _create_left_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("leftSidebar")
        sidebar.setMinimumWidth(180)
        sidebar.setMaximumWidth(280)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(14, 16, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._section_title("工作区"))
        files_label = QLabel("已选文件 / 文件夹")
        files_label.setObjectName("eyebrow")
        layout.addWidget(files_label)
        self.file_search = QLineEdit()
        self.file_search.setObjectName("issueSearch")
        self.file_search.setPlaceholderText("筛选文件或文件夹")
        self.file_search.setClearButtonEnabled(True)
        self.file_search.setToolTip("按名称快速筛选工作区，不会移除文件")
        self.file_search.textChanged.connect(self._filter_file_tree)
        layout.addWidget(self.file_search)
        self.skip_generated_dirs_check = QCheckBox("跳过常见生成目录")
        self.skip_generated_dirs_check.setObjectName("workspaceOption")
        saved_skip = self.settings.value("workspace/skip_generated_dirs", False)
        self.skip_generated_dirs_check.setChecked(
            str(saved_skip).strip().casefold() not in {"", "0", "false", "no", "off"}
        )
        self.skip_generated_dirs_check.setToolTip(
            "扫描文件夹时跳过 .git、build、dist、.venv、__pycache__、node_modules 等目录"
        )
        self.skip_generated_dirs_check.toggled.connect(
            lambda checked: self.settings.setValue("workspace/skip_generated_dirs", checked)
        )
        layout.addWidget(self.skip_generated_dirs_check)
        self.file_list = ChevronTreeWidget(palette_for(self.dark_mode).accent)
        self.file_list.setObjectName("fileList")
        self.file_list.setHeaderHidden(True)
        self.file_list.setRootIsDecorated(True)
        self.file_list.setIndentation(16)
        self.file_list.setAnimated(True)
        self.file_list.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.file_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.file_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.file_list.currentItemChanged.connect(self._selected_file_changed)
        self.file_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.file_list.customContextMenuRequested.connect(self._show_file_context_menu)
        layout.addWidget(self.file_list, 1)
        clear = QPushButton("清空文件")
        clear.setObjectName("secondaryButton")
        clear.clicked.connect(self._clear_files)
        layout.addWidget(clear)
        return sidebar

    def _create_center_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("centerPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(18, 16, 18, 14)
        layout.setSpacing(8)
        layout.addWidget(self._section_title("代码工作台"))
        toolbar = QHBoxLayout()
        toolbar.setSpacing(8)
        mode_label = QLabel("工作流")
        mode_label.setObjectName("eyebrow")
        toolbar.addWidget(mode_label)
        self.mode_segment = ModeSegment()
        self.mode_segment.mode_changed.connect(self._mode_changed)
        toolbar.addWidget(self.mode_segment)
        toolbar.addStretch()
        self.choose_button = QPushButton("选择文件 / 文件夹")
        self.choose_button.setObjectName("secondaryButton")
        self.choose_button.setIconSize(QSize(16, 16))
        self.choose_button.setToolTip("自动识别文件或文件夹")
        self.choose_button.clicked.connect(self._choose_paths)
        toolbar.addWidget(self.choose_button)
        self.template_button = QPushButton(f"模板：{self.template_profile}")
        self.template_button.setObjectName("secondaryButton")
        self.template_button.clicked.connect(self._open_template_editor)
        toolbar.addWidget(self.template_button)
        layout.addLayout(toolbar)

        self.drop_area = DropArea()
        drop_layout = QHBoxLayout(self.drop_area)
        drop_layout.setContentsMargins(0, 0, 0, 0)
        drop_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_content = QFrame()
        drop_content.setObjectName("dropContent")
        drop_content.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        drop_content_layout = QVBoxLayout(drop_content)
        drop_content_layout.setContentsMargins(0, 0, 0, 0)
        drop_content_layout.setSpacing(8)
        drop_content_layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        icon = QLabel()
        icon.setObjectName("dropIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(64, 64)
        self.drop_icon = icon
        drop_content_layout.addWidget(icon, 0, Qt.AlignmentFlag.AlignHCenter)
        drop_title = QLabel("将 C 文件或文件夹拖到这里")
        drop_title.setObjectName("dropTitle")
        drop_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_content_layout.addWidget(drop_title, 0, Qt.AlignmentFlag.AlignHCenter)
        drop_hint = QLabel("支持 .c 和 .h 文件 · 文件夹会递归加载")
        drop_hint.setObjectName("secondaryText")
        drop_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        drop_content_layout.addWidget(drop_hint, 0, Qt.AlignmentFlag.AlignHCenter)
        drop_layout.addWidget(drop_content)
        self.drop_area.files_dropped.connect(self._add_paths)
        self.drop_area.clicked.connect(self._choose_paths)
        self.drop_page = QWidget()
        drop_page_layout = QVBoxLayout(self.drop_page)
        drop_page_layout.setContentsMargins(0, 0, 0, 0)
        drop_page_layout.addWidget(self.drop_area)

        self.check_page = self._create_check_page()
        self.compare_page = self._create_compare_page()
        self.center_stack = QStackedWidget()
        self.center_stack.addWidget(self.drop_page)
        self.center_stack.addWidget(self.check_page)
        self.center_stack.addWidget(self.compare_page)
        layout.addWidget(self.center_stack, 1)
        return panel

    def _create_check_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("规范检查")
        title.setObjectName("cardTitle")
        header.addWidget(title)
        self.check_summary_label = QLabel("拖入文件后自动检查")
        self.check_summary_label.setObjectName("checkSummary")
        header.addWidget(self.check_summary_label)
        header.addStretch()
        self.check_file_label = QLabel("尚未检查")
        self.check_file_label.setObjectName("secondaryText")
        header.addWidget(self.check_file_label)
        layout.addLayout(header)
        self.check_card, self.check_editor = self._create_code_view("代码内容")
        layout.addWidget(self.check_card, 1)
        return page

    def _create_compare_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("格式化前后对比")
        title.setObjectName("cardTitle")
        header.addWidget(title)
        self.compare_summary_label = QLabel("等待生成差异")
        self.compare_summary_label.setObjectName("compareSummary")
        header.addWidget(self.compare_summary_label)
        header.addStretch()
        self.diff_position_label = QLabel("无差异")
        self.diff_position_label.setObjectName("diffPosition")
        header.addWidget(self.diff_position_label)
        self.ignore_whitespace_check = QCheckBox("忽略空白")
        self.ignore_whitespace_check.setObjectName("compareOption")
        self.ignore_whitespace_check.setToolTip("忽略空格、Tab 和空白差异")
        self.ignore_whitespace_check.stateChanged.connect(lambda _state: self._apply_diff_highlighting())
        header.addWidget(self.ignore_whitespace_check)
        self.previous_diff_button = QToolButton()
        self.previous_diff_button.setObjectName("diffNavButton")
        self.previous_diff_button.setText("‹")
        self.previous_diff_button.setToolTip("上一处差异")
        self.previous_diff_button.setEnabled(False)
        self.previous_diff_button.clicked.connect(lambda: self._move_to_difference(-1))
        header.addWidget(self.previous_diff_button)
        self.next_diff_button = QToolButton()
        self.next_diff_button.setObjectName("diffNavButton")
        self.next_diff_button.setText("›")
        self.next_diff_button.setToolTip("下一处差异")
        self.next_diff_button.setEnabled(False)
        self.next_diff_button.clicked.connect(lambda: self._move_to_difference(1))
        header.addWidget(self.next_diff_button)
        self.compare_file_label = QLabel("尚未生成对比")
        self.compare_file_label.setObjectName("secondaryText")
        header.addWidget(self.compare_file_label)
        self.compare_metadata_label = QLabel("")
        self.compare_metadata_label.setObjectName("compareMetadata")
        header.addWidget(self.compare_metadata_label)
        layout.addLayout(header)

        editors = QSplitter(Qt.Orientation.Horizontal)
        editors.setChildrenCollapsible(False)
        self.before_card, self.before_editor = self._create_code_view("格式化之前")
        self.after_card, self.after_editor = self._create_code_view("格式化之后")
        self.before_editor.verticalScrollBar().valueChanged.connect(
            lambda value: self._sync_vertical_scroll(self.before_editor, self.after_editor, value)
        )
        self.after_editor.verticalScrollBar().valueChanged.connect(
            lambda value: self._sync_vertical_scroll(self.after_editor, self.before_editor, value)
        )
        self.before_editor.horizontalScrollBar().valueChanged.connect(
            lambda value: self._sync_horizontal_scroll(self.before_editor, self.after_editor, value)
        )
        self.after_editor.horizontalScrollBar().valueChanged.connect(
            lambda value: self._sync_horizontal_scroll(self.after_editor, self.before_editor, value)
        )
        self.before_editor.verticalScrollBar().rangeChanged.connect(
            lambda _minimum, _maximum: self._update_diff_overview_viewport(self.before_editor)
        )
        self.after_editor.verticalScrollBar().rangeChanged.connect(
            lambda _minimum, _maximum: self._update_diff_overview_viewport(self.after_editor)
        )
        editors.addWidget(self.before_card)
        editors.addWidget(self.after_card)
        editors.setSizes([1, 1])
        comparison = QWidget()
        comparison_layout = QHBoxLayout(comparison)
        comparison_layout.setContentsMargins(0, 0, 0, 0)
        comparison_layout.setSpacing(7)
        self.diff_overview = DiffOverview()
        self.diff_overview.difference_selected.connect(self._select_difference)
        comparison_layout.addWidget(self.diff_overview)
        comparison_layout.addWidget(editors, 1)
        layout.addWidget(comparison, 1)
        return page

    def _sync_vertical_scroll(self, source: QPlainTextEdit, target: QPlainTextEdit, value: int) -> None:
        """Keep both panes aligned, even when formatting changes line counts."""
        if self._syncing_scroll:
            return
        source_max = source.verticalScrollBar().maximum()
        target_max = target.verticalScrollBar().maximum()
        mapped = round(value * target_max / source_max) if source_max else 0
        self._syncing_scroll = True
        try:
            target.verticalScrollBar().setValue(mapped)
            self._update_diff_overview_viewport(source)
        finally:
            self._syncing_scroll = False

    def _sync_horizontal_scroll(self, source: QPlainTextEdit, target: QPlainTextEdit, value: int) -> None:
        """Keep long lines aligned while preserving each editor's own range."""
        if self._syncing_horizontal_scroll:
            return
        source_max = source.horizontalScrollBar().maximum()
        target_max = target.horizontalScrollBar().maximum()
        mapped = round(value * target_max / source_max) if source_max else 0
        self._syncing_horizontal_scroll = True
        try:
            target.horizontalScrollBar().setValue(mapped)
        finally:
            self._syncing_horizontal_scroll = False

    def _update_diff_overview_viewport(self, editor: QPlainTextEdit) -> None:
        if hasattr(self, "diff_overview"):
            bar = editor.verticalScrollBar()
            self.diff_overview.set_viewport(bar.value(), bar.maximum(), bar.pageStep())

    def _move_to_difference(self, step: int) -> None:
        if not self._diff_blocks:
            return
        next_index = (self._current_diff_index + step) % len(self._diff_blocks)
        self._select_difference(next_index)

    def _select_difference(self, index: int) -> None:
        if not self._diff_blocks:
            return
        self._current_diff_index = max(0, min(index, len(self._diff_blocks) - 1))
        _, before_start, before_end, after_start, after_end = self._diff_blocks[self._current_diff_index]

        def focus(editor: QPlainTextEdit, start: int, end: int) -> None:
            line_count = max(editor.document().blockCount(), 1)
            line = min(start if start < end else max(start - 1, 0), line_count - 1)
            block = editor.document().findBlockByLineNumber(line)
            if block.isValid():
                editor.setTextCursor(QTextCursor(block))
                editor.centerCursor()

        self._syncing_scroll = True
        try:
            focus(self.before_editor, before_start, before_end)
            focus(self.after_editor, after_start, after_end)
        finally:
            self._syncing_scroll = False
        # The scroll-bar signals were intentionally ignored while both panes
        # were centered. Refresh the overview after the guarded jump.
        self._update_diff_overview_viewport(self.before_editor)
        self._update_diff_navigation()

    def _update_diff_navigation(self) -> None:
        count = len(self._diff_blocks)
        has_differences = count > 0
        self.previous_diff_button.setEnabled(has_differences)
        self.next_diff_button.setEnabled(has_differences)
        self.diff_position_label.setText(f"差异 {self._current_diff_index + 1} / {count}" if has_differences else "无差异")
        self.diff_overview.set_differences(
            self._diff_blocks,
            self.before_editor.document().blockCount(),
            self.after_editor.document().blockCount(),
            self._current_diff_index,
        )

    @staticmethod
    def _line_selection(editor: QPlainTextEdit, line_number: int, color: QColor) -> QTextEdit.ExtraSelection | None:
        block = editor.document().findBlockByLineNumber(line_number)
        if not block.isValid():
            return None
        cursor = QTextCursor(block)
        cursor.select(QTextCursor.SelectionType.LineUnderCursor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(color)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        return selection

    @staticmethod
    def _line_range_selection(
        editor: QPlainTextEdit,
        start_line: int,
        end_line: int,
        color: QColor,
    ) -> QTextEdit.ExtraSelection | None:
        if end_line <= start_line:
            return None
        first = editor.document().findBlockByLineNumber(start_line)
        last = editor.document().findBlockByLineNumber(end_line - 1)
        if not first.isValid() or not last.isValid():
            return None
        cursor = QTextCursor(first)
        cursor.setPosition(last.position() + max(last.length() - 1, 0), QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(color)
        selection.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        return selection

    @staticmethod
    def _char_selection(
        editor: QPlainTextEdit,
        line_number: int,
        start: int,
        end: int,
        color: QColor,
    ) -> QTextEdit.ExtraSelection | None:
        if end <= start:
            return None
        block = editor.document().findBlockByLineNumber(line_number)
        if not block.isValid():
            return None
        cursor = QTextCursor(editor.document())
        cursor.setPosition(block.position() + start)
        cursor.setPosition(block.position() + end, QTextCursor.MoveMode.KeepAnchor)
        selection = QTextEdit.ExtraSelection()
        selection.cursor = cursor
        selection.format.setBackground(color)
        return selection

    def _apply_diff_highlighting(self) -> None:
        before_text = self.before_editor.toPlainText()
        after_text = self.after_editor.toPlainText()
        before_lines = before_text.splitlines()
        after_lines = after_text.splitlines()
        dark = self.dark_mode
        before_line_color = QColor("#482729" if dark else "#FFF0F0")
        after_line_color = QColor("#23412E" if dark else "#ECF8EF")
        before_char_color = QColor("#733A3D" if dark else "#FFDAD6")
        after_char_color = QColor("#356B45" if dark else "#D2F3D9")
        before_selections: list[QTextEdit.ExtraSelection] = []
        after_selections: list[QTextEdit.ExtraSelection] = []
        changed_before = 0
        changed_after = 0

        opcodes = diff_opcodes(
            before_text,
            after_text,
            self.ignore_whitespace_check.isChecked(),
        )
        self._diff_blocks = [opcode for opcode in opcodes if opcode[0] != "equal"]
        changed_line_total = sum(
            (before_end - before_start) + (after_end - after_start)
            for tag, before_start, before_end, after_start, after_end in self._diff_blocks
        )
        # Keep the useful line-level overview for large files, but avoid the
        # quadratic-looking per-line matcher when it would freeze the window.
        precise_char_diff = len(before_text) + len(after_text) <= 600_000 and changed_line_total <= 6_000
        if self._diff_blocks:
            self._current_diff_index = min(max(self._current_diff_index, 0), len(self._diff_blocks) - 1)
        else:
            self._current_diff_index = -1

        for tag, before_start, before_end, after_start, after_end in opcodes:
            if tag == "equal":
                continue
            changed_before += before_end - before_start
            changed_after += after_end - after_start
            before_selection = self._line_range_selection(
                self.before_editor, before_start, before_end, before_line_color
            )
            if before_selection:
                before_selections.append(before_selection)
            after_selection = self._line_range_selection(self.after_editor, after_start, after_end, after_line_color)
            if after_selection:
                after_selections.append(after_selection)

            if tag == "replace" and precise_char_diff:
                for offset in range(min(before_end - before_start, after_end - after_start)):
                    before_line_number = before_start + offset
                    after_line_number = after_start + offset
                    before_line = before_lines[before_line_number]
                    after_line = after_lines[after_line_number]
                    for inner_tag, left_start, left_end, right_start, right_end in difflib.SequenceMatcher(
                        None, before_line, after_line, autojunk=False
                    ).get_opcodes():
                        if inner_tag == "equal":
                            continue
                        left_selection = self._char_selection(
                            self.before_editor, before_line_number, left_start, left_end, before_char_color
                        )
                        right_selection = self._char_selection(
                            self.after_editor, after_line_number, right_start, right_end, after_char_color
                        )
                        if left_selection:
                            before_selections.append(left_selection)
                        if right_selection:
                            after_selections.append(right_selection)

        self.before_editor.setExtraSelections(before_selections)
        self.after_editor.setExtraSelections(after_selections)
        if not changed_before and not changed_after:
            self.compare_summary_label.setText(
                "忽略空白后无差异" if self.ignore_whitespace_check.isChecked() else "两边完全一致"
            )
        else:
            self.compare_summary_label.setText(f"{changed_before} 行变更 → {changed_after} 行结果")
        self._update_diff_navigation()
        self._update_diff_overview_viewport(self.before_editor)

    def _create_code_view(self, title: str) -> tuple[QFrame, QPlainTextEdit]:
        card = QFrame()
        card.setObjectName("codeCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 10, 10, 10)
        header = QHBoxLayout()
        header.setContentsMargins(2, 0, 2, 2)
        label = QLabel(title)
        label.setObjectName("codeTitle")
        header.addWidget(label)
        header.addStretch()
        badge_text = {"格式化之前": "原文件", "格式化之后": "模板结果"}.get(title)
        if badge_text:
            badge = QLabel(badge_text)
            badge.setObjectName("beforeBadge" if "之前" in title else "afterBadge")
            header.addWidget(badge)
        layout.addLayout(header)
        editor = CodeView()
        editor.setObjectName("codeView")
        editor.setReadOnly(True)
        editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        code_font = QFont(preferred_code_font_family(), self.code_font_size)
        code_font.setStyleHint(QFont.StyleHint.Monospace)
        code_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.2)
        editor.setFont(code_font)
        editor.setTabStopDistance(editor.fontMetrics().horizontalAdvance("    ") or 32)
        editor._ccf_highlighter = CCodeHighlighter(editor.document(), dark=False)
        self.code_views.append(editor)
        layout.addWidget(editor, 1)
        return card, editor

    def _create_right_sidebar(self) -> QWidget:
        sidebar = QFrame()
        sidebar.setObjectName("rightSidebar")
        sidebar.setMinimumWidth(238)
        sidebar.setMaximumWidth(330)
        outer_layout = QVBoxLayout(sidebar)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setObjectName("actionScroll")
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        content = QWidget()
        content.setObjectName("actionScrollContent")
        layout = QVBoxLayout(content)
        layout.setContentsMargins(14, 16, 14, 12)
        layout.setSpacing(8)
        layout.addWidget(self._section_title("操作台"))

        check_group = QFrame()
        check_group.setObjectName("actionGroup")
        check_layout = QVBoxLayout(check_group)
        check_layout.setContentsMargins(10, 10, 10, 10)
        check_layout.setSpacing(6)
        check_title = QLabel("① 检查格式")
        check_title.setObjectName("actionTitle")
        check_layout.addWidget(check_title)
        check_hint = QLabel("只读分析 · 不会修改文件")
        check_hint.setObjectName("actionHint")
        check_layout.addWidget(check_hint)
        self.auto_recheck_check = QCheckBox("文件变化时自动重新检查")
        self.auto_recheck_check.setObjectName("compareOption")
        auto_recheck = self.settings.value("check/auto_recheck", True)
        self.auto_recheck_check.setChecked(str(auto_recheck).lower() not in {"0", "false", "no"})
        self.auto_recheck_check.stateChanged.connect(
            lambda state: self.settings.setValue("check/auto_recheck", bool(state))
        )
        check_layout.addWidget(self.auto_recheck_check)
        self.check_progress = QProgressBar()
        self.check_progress.setObjectName("checkProgress")
        self.check_progress.setTextVisible(True)
        self.check_progress.hide()
        check_layout.addWidget(self.check_progress)
        issue_filter_row = QHBoxLayout()
        issue_filter_row.setSpacing(6)
        self.issue_search = QLineEdit()
        self.issue_search.setObjectName("issueSearch")
        self.issue_search.setPlaceholderText("搜索文件名、路径、问题或规则")
        self.issue_search.setClearButtonEnabled(True)
        self.issue_search.textChanged.connect(lambda _text: self._filter_diagnostics())
        issue_filter_row.addWidget(self.issue_search, 1)
        self.issue_severity = QComboBox()
        self.issue_severity.setObjectName("issueSeverity")
        self.issue_severity.addItem("全部级别", "")
        self.issue_severity.addItem("错误", "error")
        self.issue_severity.addItem("警告", "warning")
        self.issue_severity.addItem("提示", "info")
        self.issue_severity.currentIndexChanged.connect(lambda _index: self._filter_diagnostics())
        issue_filter_row.addWidget(self.issue_severity)
        check_layout.addLayout(issue_filter_row)
        self.issue_list = QListWidget()
        self.issue_list.setObjectName("issueList")
        self.issue_list.setWordWrap(False)
        self.issue_list.setUniformItemSizes(True)
        self.issue_list.setMinimumHeight(112)
        self.issue_list.setMaximumHeight(190)
        self.issue_list.itemClicked.connect(self._show_diagnostic)
        check_layout.addWidget(self.issue_list)
        issue_nav = QHBoxLayout()
        issue_nav.setSpacing(6)
        self.previous_issue_button = QToolButton()
        self.previous_issue_button.setObjectName("diffNavButton")
        self.previous_issue_button.setText("‹")
        self.previous_issue_button.setToolTip("上一个问题")
        self.previous_issue_button.setEnabled(False)
        self.previous_issue_button.clicked.connect(lambda: self._move_to_diagnostic(-1))
        issue_nav.addWidget(self.previous_issue_button)
        self.next_issue_button = QToolButton()
        self.next_issue_button.setObjectName("diffNavButton")
        self.next_issue_button.setText("›")
        self.next_issue_button.setToolTip("下一个问题")
        self.next_issue_button.setEnabled(False)
        self.next_issue_button.clicked.connect(lambda: self._move_to_diagnostic(1))
        issue_nav.addWidget(self.next_issue_button)
        issue_nav.addStretch()
        check_layout.addLayout(issue_nav)
        self.issue_detail = QLabel("拖入文件后显示检查结果")
        self.issue_detail.setObjectName("secondaryText")
        self.issue_detail.setWordWrap(True)
        check_layout.addWidget(self.issue_detail)
        self.check_button = QPushButton("开始检查")
        self.check_button.setObjectName("secondaryButton")
        self.check_button.setEnabled(False)
        self.check_button.clicked.connect(self._check_button_clicked)
        check_layout.addWidget(self.check_button)
        self.repair_button = QPushButton("生成安全修复预览")
        self.repair_button.setObjectName("secondaryButton")
        self.repair_button.setEnabled(False)
        self.repair_button.setToolTip("仅修复缩进和缺失大括号，并先进入对比页面确认")
        self.repair_button.clicked.connect(self._repair_selected)
        check_layout.addWidget(self.repair_button)
        self.export_report_button = QPushButton("导出检查报告")
        self.export_report_button.setObjectName("secondaryButton")
        self.export_report_button.setEnabled(False)
        self.export_report_button.setToolTip("将本次检查结果导出为 TXT、HTML 或 JSON，不会修改源文件")
        self.export_report_button.clicked.connect(self._export_check_report)
        check_layout.addWidget(self.export_report_button)
        self.overview_button = QPushButton("查看文件总览")
        self.overview_button.setObjectName("secondaryButton")
        self.overview_button.setEnabled(False)
        self.overview_button.setToolTip("按文件查看本次检查状态和问题数量")
        self.overview_button.clicked.connect(self._show_check_overview)
        check_layout.addWidget(self.overview_button)
        layout.addWidget(check_group)
        self._render_diagnostics([])

        format_group = QFrame()
        format_group.setObjectName("actionGroup")
        format_layout = QVBoxLayout(format_group)
        format_layout.setContentsMargins(10, 10, 10, 10)
        format_layout.setSpacing(6)
        format_title = QLabel("② 格式化预览")
        format_title.setObjectName("actionTitle")
        format_layout.addWidget(format_title)
        format_hint = QLabel("只生成对比 · 不会立即写入")
        format_hint.setObjectName("actionHint")
        format_layout.addWidget(format_hint)
        self.format_button = QPushButton("生成对比")
        self.format_button.setObjectName("primaryButton")
        self.format_button.setMinimumHeight(38)
        self.format_button.setEnabled(False)
        self.format_button.clicked.connect(self._format_selected)
        format_layout.addWidget(self.format_button)
        self.batch_format_button = QPushButton("批量生成 .formatted 文件")
        self.batch_format_button.setObjectName("secondaryButton")
        self.batch_format_button.setEnabled(False)
        self.batch_format_button.setToolTip("选择输出文件夹后，为全部已选 .c/.h 文件生成旁路结果，不覆盖原文件")
        self.batch_format_button.clicked.connect(self._batch_format_selected)
        format_layout.addWidget(self.batch_format_button)
        self.format_issues_only_check = QCheckBox("只生成检查出问题的文件")
        self.format_issues_only_check.setObjectName("compareOption")
        only_issues = self.settings.value("format/issues_only", False)
        self.format_issues_only_check.setChecked(str(only_issues).lower() not in {"0", "false", "no"})
        self.format_issues_only_check.setToolTip("使用最近一次检查结果，只批量生成存在规则问题的文件")
        self.format_issues_only_check.toggled.connect(
            lambda checked: self.settings.setValue("format/issues_only", checked)
        )
        format_layout.addWidget(self.format_issues_only_check)
        self.preserve_structure_check = QCheckBox("保留选中文件夹的目录结构")
        self.preserve_structure_check.setObjectName("compareOption")
        preserve_structure = self.settings.value("format/preserve_structure", False)
        self.preserve_structure_check.setChecked(
            str(preserve_structure).lower() not in {"0", "false", "no"}
        )
        self.preserve_structure_check.setToolTip(
            "批量输出时在目标目录下保留文件夹层级；直接选择的单个文件仍输出到目标目录"
        )
        self.preserve_structure_check.toggled.connect(
            lambda checked: self.settings.setValue("format/preserve_structure", checked)
        )
        format_layout.addWidget(self.preserve_structure_check)
        layout.addWidget(format_group)

        commit_group = QFrame()
        commit_group.setObjectName("actionGroup")
        commit_layout = QVBoxLayout(commit_group)
        commit_layout.setContentsMargins(10, 10, 10, 10)
        commit_layout.setSpacing(6)
        commit_title = QLabel("③ 确认输出")
        commit_title.setObjectName("actionTitle")
        commit_layout.addWidget(commit_title)
        self.output_value_label = QLabel("先生成并查看对比结果")
        self.output_value_label.setObjectName("actionHint")
        self.output_value_label.setWordWrap(True)
        commit_layout.addWidget(self.output_value_label)
        self.commit_button = QPushButton("覆盖原文件")
        self.commit_button.setObjectName("dangerButton")
        self.commit_button.setEnabled(False)
        self.commit_button.clicked.connect(self._commit_comparison)
        commit_layout.addWidget(self.commit_button)
        self.copy_button = QPushButton("复制格式化结果")
        self.copy_button.setObjectName("secondaryButton")
        self.copy_button.setEnabled(False)
        self.copy_button.setToolTip("将右侧格式化结果复制到剪贴板")
        self.copy_button.clicked.connect(self._copy_comparison)
        commit_layout.addWidget(self.copy_button)
        self.export_patch_button = QPushButton("导出差异补丁")
        self.export_patch_button.setObjectName("secondaryButton")
        self.export_patch_button.setEnabled(False)
        self.export_patch_button.setToolTip("将当前格式化前后差异保存为 unified diff，不修改源文件")
        self.export_patch_button.clicked.connect(self._export_diff_patch)
        commit_layout.addWidget(self.export_patch_button)
        self.open_output_button = QPushButton("打开输出目录")
        self.open_output_button.setObjectName("secondaryButton")
        self.open_output_button.setEnabled(False)
        self.open_output_button.setToolTip("打开最近一次另存或批量生成的输出目录")
        self.open_output_button.clicked.connect(self._open_output_directory)
        commit_layout.addWidget(self.open_output_button)
        layout.addWidget(commit_group)

        result_card = QFrame()
        result_card.setObjectName("infoCard")
        result_layout = QVBoxLayout(result_card)
        result_layout.setContentsMargins(12, 12, 12, 12)
        result_title = QLabel("最近结果")
        result_title.setObjectName("cardTitle")
        result_layout.addWidget(result_title)
        self.result_label = QLabel("暂无格式化结果")
        self.result_label.setObjectName("secondaryText")
        self.result_label.setWordWrap(True)
        result_layout.addWidget(self.result_label)
        layout.addWidget(result_card)
        layout.addStretch()
        scroll.setWidget(content)
        outer_layout.addWidget(scroll)
        return sidebar

    def _render_diagnostics(self, diagnostics: list[Diagnostic]) -> None:
        if not hasattr(self, "issue_list"):
            return
        self.check_diagnostics = list(diagnostics)
        if hasattr(self, "repair_button"):
            self.repair_button.setEnabled(any(item.fixable for item in self.check_diagnostics))
        if hasattr(self, "export_report_button"):
            self.export_report_button.setEnabled(bool(self.check_sources))
        if hasattr(self, "overview_button"):
            self.overview_button.setEnabled(bool(self.check_sources or self.check_diagnostics))
        self._filter_diagnostics()

    def _filter_diagnostics(self) -> None:
        """Refresh only the visible issue list; highlights keep all diagnostics."""
        if not hasattr(self, "issue_list"):
            return
        query = self.issue_search.text().strip().casefold() if hasattr(self, "issue_search") else ""
        severity_filter = self.issue_severity.currentData() if hasattr(self, "issue_severity") else ""
        visible = [
            diagnostic
            for diagnostic in self.check_diagnostics
            if (not severity_filter or diagnostic.severity == severity_filter)
            and (not query or _diagnostic_matches_query(diagnostic, query))
        ]
        self.issue_list.blockSignals(True)
        try:
            self.issue_list.clear()
            if not self.check_diagnostics:
                item = QListWidgetItem("✓  未发现违反规则的地方")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.issue_list.addItem(item)
                detail = "当前文件符合已启用的自动检查规则。"
            elif not visible:
                item = QListWidgetItem("未找到匹配的问题")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                self.issue_list.addItem(item)
                detail = f"显示 0 / {len(self.check_diagnostics)} 个问题，请调整搜索条件。"
            else:
                for diagnostic in visible:
                    severity = {"error": "错误", "warning": "警告", "info": "提示"}.get(diagnostic.severity, "问题")
                    path_prefix = f"{diagnostic.path.name} · " if len(self.check_sources) > 1 else ""
                    item = QListWidgetItem(f"{path_prefix}L{diagnostic.line}  {severity} · {diagnostic.message}")
                    item.setData(Qt.ItemDataRole.UserRole, diagnostic)
                    item.setToolTip(
                        f"文件：{diagnostic.path}\n规则：{diagnostic.rule_id}\n应当：{diagnostic.expected}"
                    )
                    self.issue_list.addItem(item)
                detail = f"显示 {len(visible)} / {len(self.check_diagnostics)} 个问题 · 点击问题可定位并查看规则说明。"
        finally:
            self.issue_list.blockSignals(False)
        if hasattr(self, "issue_detail"):
            self.issue_detail.setText(detail)
        has_visible = bool(visible)
        if hasattr(self, "previous_issue_button"):
            self.previous_issue_button.setEnabled(has_visible)
            self.next_issue_button.setEnabled(has_visible)

    def _export_check_report(self) -> None:
        if not self.check_sources:
            return
        suggested = (self.check_path or next(iter(self.check_sources))).with_name("ccf-check-report.txt")
        output, selected_filter = QFileDialog.getSaveFileName(
            self,
            "导出检查报告",
            str(suggested),
            "文本报告 (*.txt);;网页报告 (*.html);;机器可读 JSON (*.json)",
        )
        if not output:
            return
        target = Path(output)
        is_json = target.suffix.lower() == ".json" or "JSON" in selected_filter
        is_html = target.suffix.lower() == ".html" or "网页报告" in selected_filter
        if is_json and target.suffix.lower() != ".json":
            target = target.with_suffix(".json")
        elif is_html and target.suffix.lower() != ".html":
            target = target.with_suffix(".html")
        elif not is_html and not is_json and target.suffix.lower() != ".txt":
            target = target.with_suffix(".txt")
        diagnostics = self.check_diagnostics
        severity_names = {"error": "错误", "warning": "警告", "info": "提示"}
        lines = [
            "CCodeFormatter 检查报告",
            f"文件数量：{len(self.check_sources)}",
            f"问题数量：{len(diagnostics)}",
            "",
        ]
        for index, diagnostic in enumerate(diagnostics, 1):
            lines.extend(
                (
                    f"{index}. {diagnostic.path}:{diagnostic.line}:{diagnostic.column} "
                    f"[{severity_names.get(diagnostic.severity, '问题')}] {diagnostic.rule_id}",
                    f"   {diagnostic.message}",
                    f"   应当：{diagnostic.expected}",
                    "",
                )
            )
        try:
            if is_json:
                payload = {
                    "tool": "CCodeFormatter",
                    "files": len(self.check_sources),
                    "issues": len(diagnostics),
                    "diagnostics": [
                        {
                            "file": str(diagnostic.path),
                            "line": diagnostic.line,
                            "column": diagnostic.column,
                            "end_line": diagnostic.end_line,
                            "end_column": diagnostic.end_column,
                            "severity": diagnostic.severity,
                            "rule_id": diagnostic.rule_id,
                            "message": diagnostic.message,
                            "expected": diagnostic.expected,
                            "fixable": diagnostic.fixable,
                        }
                        for diagnostic in diagnostics
                    ],
                }
                target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
            elif is_html:
                body = "<br>".join(html.escape(line) for line in lines)
                content = (
                    "<!doctype html><html><head><meta charset='utf-8'>"
                    "<title>CCodeFormatter 检查报告</title></head>"
                    f"<body><pre>{body}</pre></body></html>"
                )
                target.write_text(content, encoding="utf-8", newline="\n")
            else:
                target.write_text("\n".join(lines), encoding="utf-8", newline="\n")
        except OSError as error:
            QMessageBox.critical(self, "导出失败", str(error))
            return
        self.result_label.setText(f"检查报告已导出\n{target.name}")
        self.status_label.setText(f"检查报告已导出 · {target}")

    def _show_check_overview(self) -> None:
        if not self.selected or not (self.check_sources or self.check_diagnostics):
            return
        dialog = CheckOverviewDialog(
            self,
            list(self.selected),
            self.check_diagnostics,
            self._open_check_overview_path,
            list(set(self.check_sources) | {Path(item.path).resolve() for item in self.check_diagnostics}),
        )
        dialog.setStyleSheet(self.styleSheet())
        dialog.exec()

    def _open_check_overview_path(self, path: Path) -> None:
        path = Path(path).resolve()
        self._select_path(path)
        source = self.check_sources.get(path)
        self.check_path = path
        if source is not None:
            self.check_editor.setPlainText(source)
            self.check_file_label.setText(path.name)
        else:
            self.check_editor.clear()
            self.check_file_label.setText(f"{path.name} · 无法读取")
        self.center_stack.setCurrentWidget(self.check_page)
        for index in range(self.issue_list.count()):
            item = self.issue_list.item(index)
            diagnostic = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(diagnostic, Diagnostic) and Path(diagnostic.path).resolve() == path:
                self.issue_list.setCurrentItem(item)
                self._show_diagnostic(item)
                return
        self.issue_list.clearSelection()
        self._apply_diagnostic_highlighting()
        self.issue_detail.setText("该文件本次检查未发现问题。")
        self.status_label.setText(f"已切换到 {path.name}")

    def _move_to_diagnostic(self, step: int) -> None:
        items = [
            self.issue_list.item(index)
            for index in range(self.issue_list.count())
            if isinstance(self.issue_list.item(index).data(Qt.ItemDataRole.UserRole), Diagnostic)
        ]
        if not items:
            return
        current = self.issue_list.currentItem()
        index = items.index(current) if current in items else (-1 if step > 0 else 0)
        target = items[(index + step) % len(items)]
        self.issue_list.setCurrentItem(target)
        self._show_diagnostic(target)

    def _apply_diagnostic_highlighting(self) -> None:
        selections: list[QTextEdit.ExtraSelection] = []
        palette = palette_for(self.dark_mode)
        line_colors: dict[int, QColor] = {}
        for diagnostic in self.check_diagnostics:
            if self.check_path is not None and diagnostic.path != self.check_path:
                continue
            is_error = diagnostic.severity == "error"
            line_color = QColor(palette.danger_soft if is_error else palette.selection)
            line = diagnostic.line - 1
            # One line can contain several rule violations. Keep one marker
            # per line; error coloring takes precedence over warning coloring.
            if line not in line_colors or is_error:
                line_colors[line] = line_color
        for line, color in line_colors.items():
            line_selection = self._line_selection(self.check_editor, line, color)
            if line_selection:
                selections.append(line_selection)

        # Precise character highlighting is reserved for the selected issue;
        # line markers still show every issue without thousands of selections.
        current_item = self.issue_list.currentItem()
        current = current_item.data(Qt.ItemDataRole.UserRole) if current_item is not None else None
        if isinstance(current, Diagnostic) and current.path == self.check_path:
            char_color = QColor(palette.danger if current.severity == "error" else palette.accent)
            char_selection = self._char_selection(
                self.check_editor,
                current.line - 1,
                max(0, current.column - 1),
                max(current.column, current.end_column - 1),
                char_color,
            )
            if char_selection:
                selections.append(char_selection)
        self.check_editor.setExtraSelections(selections)

    def _show_diagnostic(self, item: QListWidgetItem) -> None:
        diagnostic = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(diagnostic, Diagnostic):
            return
        source = self.check_sources.get(diagnostic.path)
        if source is not None and diagnostic.path != self.check_path:
            self.check_path = diagnostic.path
            self.check_editor.setPlainText(source)
            self.check_file_label.setText(diagnostic.path.name)
            self._select_path(diagnostic.path)
            self._apply_diagnostic_highlighting()
        self.issue_detail.setText(
            f"规则：{diagnostic.rule_id}\n违反：{diagnostic.message}\n应当：{diagnostic.expected}"
        )
        cursor = self.check_editor.textCursor()
        block = self.check_editor.document().findBlockByLineNumber(diagnostic.line - 1)
        cursor.setPosition(block.position() + max(0, diagnostic.column - 1))
        self.check_editor.setTextCursor(cursor)
        self.check_editor.ensureCursorVisible()
        self.center_stack.setCurrentWidget(self.check_page)
        self.check_editor.setFocus()

    def _repair_selected(self) -> None:
        path = self.check_path or self._current_file_path()
        if path is None:
            return
        if not any(item.path == path and item.fixable for item in self.check_diagnostics):
            QMessageBox.information(self, "没有安全修复项", "当前文件没有可以自动安全修复的问题。")
            return
        try:
            project_profile = load_project_profile(path)
            template_dir = template_dir_for_use(path) if project_profile is not None else self.template_dir
            style = TemplateStyle.from_template(template_dir, path.suffix)
        except Exception as error:
            QMessageBox.critical(self, "无法生成安全修复", str(error))
            return
        self._prepare_comparison(path, safe_repair=True, indent_width=style.indent_width)
        self.status_label.setText("已生成安全修复预览 · 请在对比页确认后再写入")

    def _batch_format_selected(self) -> None:
        if not self.selected:
            return
        if self._check_worker is not None and self._check_worker.isRunning():
            QMessageBox.information(self, "检查进行中", "请等待检查完成后再开始批量生成。")
            return
        if self._format_worker is not None and self._format_worker.isRunning():
            self._format_worker.requestInterruption()
            self.batch_format_button.setText("正在取消批量生成…")
            return
        paths = list(self.selected)
        if self.format_issues_only_check.isChecked():
            issue_paths = {diagnostic.path.resolve() for diagnostic in self.check_diagnostics}
            paths = [path for path in paths if path.resolve() in issue_paths]
            if not paths:
                QMessageBox.information(
                    self,
                    "没有待处理文件",
                    "最近一次检查没有发现问题文件，或还没有完成检查。\n请取消该选项后生成全部文件。",
                )
                return
        remembered = self.settings.value("output/last_directory", "")
        remembered_dir = Path(str(remembered)).expanduser() if str(remembered).strip() else self._selection_directory()
        if not remembered_dir.is_dir():
            remembered_dir = self._selection_directory()
        output_text = QFileDialog.getExistingDirectory(
            self,
            "选择批量输出文件夹",
            str(remembered_dir),
        )
        if not output_text:
            return
        output_dir = Path(output_text).resolve()
        self.settings.setValue("output/last_directory", str(output_dir))
        roots = [path for path in self._selection_sources if path.is_dir()]
        preserve_structure = self.preserve_structure_check.isChecked()
        outputs = BatchFormatThread.output_paths(
            paths,
            output_dir,
            preserve_structure=preserve_structure,
            source_roots=roots,
        )
        existing = [path for path in outputs if path.exists()]
        layout_hint = "，并保留选中文件夹的目录结构" if preserve_structure else ""
        detail = f"将为 {len(outputs)} 个文件生成到：\n{output_dir}{layout_hint}\n不会覆盖原文件。"
        if existing:
            detail += f"\n其中 {len(existing)} 个结果已存在，将被更新。"
        answer = QMessageBox.question(
            self,
            "确认批量生成",
            detail,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.check_progress.setRange(0, len(paths))
        self.check_progress.setValue(0)
        self.check_progress.setFormat(f"0 / {len(paths)}")
        self.check_progress.show()
        self.format_button.setEnabled(False)
        self.batch_format_button.setText("取消批量生成")
        self.batch_format_button.setEnabled(True)
        self.check_button.setEnabled(False)
        scope = "问题文件" if self.format_issues_only_check.isChecked() else "文件"
        self.status_label.setText(f"批量生成已开始 · 共 {len(paths)} 个{scope}")
        roots = [path for path in self._selection_sources if path.is_dir()]
        worker = BatchFormatThread(
            paths,
            output_dir,
            preserve_structure=self.preserve_structure_check.isChecked(),
            source_roots=roots,
        )
        worker.progress_changed.connect(self._format_progress_changed)
        worker.completed.connect(self._apply_batch_format_result)
        worker.finished.connect(self._batch_format_finished)
        self._format_worker = worker
        worker.start()

    def _format_progress_changed(self, completed: int, total: int, file_name: str) -> None:
        self.check_progress.setRange(0, total)
        self.check_progress.setValue(completed)
        self.check_progress.setFormat(f"{completed} / {total}")
        self.status_label.setText(f"批量生成 · {file_name}")

    def _apply_batch_format_result(
        self,
        outputs: list[Path],
        failures: list[str],
        cancelled: bool,
        processed: int,
    ) -> None:
        if cancelled:
            self.status_label.setText(f"批量生成已取消 · 已生成 {len(outputs)} 个文件")
            self.result_label.setText(f"批量生成已取消\n已生成 {len(outputs)} 个结果")
        else:
            self.status_label.setText(f"批量生成完成 · 成功 {len(outputs)} 个，失败 {len(failures)} 个")
            self.result_label.setText(f"批量生成完成\n成功 {len(outputs)} 个，失败 {len(failures)} 个")
        if outputs:
            self._last_output_directory = outputs[0].parent
            self.open_output_button.setEnabled(True)
        if failures:
            QMessageBox.warning(self, "部分文件生成失败", "\n".join(failures))

    def _batch_format_finished(self) -> None:
        worker = self.sender()
        if worker is self._format_worker:
            self._format_worker = None
        if isinstance(worker, QThread):
            worker.deleteLater()
        self.check_progress.hide()
        self.check_button.setEnabled(bool(self.selected))
        self.format_button.setEnabled(bool(self.selected))
        self.batch_format_button.setEnabled(bool(self.selected))
        self.batch_format_button.setText("批量生成 .formatted 文件")
        if self._close_after_format:
            self._close_after_format = False
            self.close()

    def _check_button_clicked(self) -> None:
        if self._check_worker is not None and self._check_worker.isRunning():
            self._cancel_check()
            return
        self._check_selected()

    def _cancel_check(self) -> None:
        if self._check_worker is None or not self._check_worker.isRunning():
            return
        self._restart_check_after_finish = False
        self._check_worker.requestInterruption()
        self.check_button.setEnabled(False)
        self.check_button.setText("正在取消…")
        self.status_label.setText("正在取消后台检查…")

    def _check_selected(self) -> None:
        if not self.selected:
            return
        if self._check_worker is not None and self._check_worker.isRunning():
            self._discard_check_result = True
            self._restart_check_after_finish = True
            self._check_worker.requestInterruption()
            self.check_button.setEnabled(False)
            self.check_button.setText("正在重新检查…")
            return

        paths = list(self.selected)
        self._invalidate_comparison()
        self.check_sources = {}
        self.check_diagnostics = []
        self._update_tree_issue_badges([])
        self._discard_check_result = False
        self.issue_list.clear()
        waiting_item = QListWidgetItem("正在后台检查…")
        waiting_item.setFlags(Qt.ItemFlag.NoItemFlags)
        self.issue_list.addItem(waiting_item)
        self.issue_detail.setText("检查期间可以继续浏览界面，也可以随时取消。")
        self.repair_button.setEnabled(False)
        self.export_report_button.setEnabled(False)
        self.overview_button.setEnabled(False)
        self.format_button.setEnabled(False)
        self.batch_format_button.setEnabled(False)
        self.check_progress.setRange(0, len(paths))
        self.check_progress.setValue(0)
        self.check_progress.setFormat(f"0 / {len(paths)}")
        self.check_progress.show()
        self.check_button.setEnabled(True)
        self.check_button.setText("取消检查")
        self.check_summary_label.setText(f"正在检查 · 0 / {len(paths)}")
        self.status_label.setText(f"后台检查已开始 · 共 {len(paths)} 个文件")

        worker = BatchCheckThread(paths)
        worker.progress_changed.connect(self._check_progress_changed)
        worker.completed.connect(
            lambda sources, diagnostics, failures, cancelled, processed, checked_paths=paths:
            self._apply_check_result(
                checked_paths, sources, diagnostics, failures, cancelled, processed
            )
        )
        worker.finished.connect(self._check_worker_finished)
        self._check_worker = worker
        worker.start()

    def _check_progress_changed(self, completed: int, total: int, file_name: str) -> None:
        self.check_progress.setRange(0, total)
        self.check_progress.setValue(completed)
        self.check_progress.setFormat(f"{completed} / {total}")
        self.check_summary_label.setText(f"正在检查 · {completed} / {total}")
        self.status_label.setText(f"后台检查 · {file_name}")

    def _apply_check_result(
        self,
        paths: list[Path],
        sources: dict[Path, str],
        diagnostics: list[Diagnostic],
        failures: list[str],
        cancelled: bool,
        processed: int,
    ) -> None:
        if self._discard_check_result:
            return
        self.check_sources = dict(sources)
        path = self._current_file_path() or paths[0]
        self._select_path(path)
        self.check_path = path
        self.check_editor.setPlainText(self.check_sources.get(path, ""))
        self.check_file_label.setText(path.name)
        self._render_diagnostics(diagnostics)
        self._update_tree_issue_badges(diagnostics)
        self._apply_diagnostic_highlighting()
        self.center_stack.setCurrentWidget(self.check_page)
        if cancelled:
            self.check_summary_label.setText(f"检查已取消 · 完成 {processed} / {len(paths)}")
            self.status_label.setText(f"检查已取消 · 已保留 {processed} 个文件的结果")
            return
        if diagnostics:
            error_count = sum(item.severity == "error" for item in diagnostics)
            warning_count = sum(item.severity == "warning" for item in diagnostics)
            affected_files = len({item.path for item in diagnostics})
            self.check_summary_label.setText(
                f"{len(paths)} 个文件 · {error_count} 错误 · {warning_count} 警告"
            )
            self.status_label.setText(
                f"检查完成 · {affected_files} 个文件受影响 · {len(diagnostics)} 个问题待处理"
            )
        else:
            self.check_summary_label.setText(f"检查通过 · {len(paths)} 个文件 · 0 个问题")
            self.status_label.setText(f"检查完成 · {len(paths)} 个文件均未发现规则问题")
        if failures:
            QMessageBox.warning(self, "部分文件检查失败", "\n".join(failures))

    def _check_worker_finished(self) -> None:
        worker = self.sender()
        if worker is self._check_worker:
            self._check_worker = None
        if isinstance(worker, QThread):
            worker.deleteLater()
        self.check_progress.hide()
        self.check_button.setEnabled(bool(self.selected))
        self.check_button.setText("重新检查" if self.selected else "开始检查")
        self.format_button.setEnabled(bool(self.selected))
        self.batch_format_button.setEnabled(bool(self.selected))
        if self._close_after_check:
            self._close_after_check = False
            self.close()
            return
        if self._restart_check_after_finish and self.selected:
            self._restart_check_after_finish = False
            self._discard_check_result = False
            self._check_selected()

    def _path_for_item(self, item: QTreeWidgetItem | None) -> Path | None:
        if item is None or item.data(0, ITEM_KIND_ROLE) != "file":
            return None
        value = item.data(0, Qt.ItemDataRole.UserRole)
        return Path(str(value)) if value else None

    def _current_file_path(self) -> Path | None:
        return self._path_for_item(self.file_list.currentItem())

    def _select_path(self, path: Path) -> None:
        item = self._tree_items.get(Path(path))
        if item is not None:
            self.file_list.setCurrentItem(item)

    def _new_tree_item(self, parent, path: Path, kind: str, icon: QIcon | None = None) -> QTreeWidgetItem:
        item = FolderFirstItem(parent, [path.name])
        item.setData(0, Qt.ItemDataRole.UserRole, str(path))
        item.setData(0, ITEM_KIND_ROLE, kind)
        item.setToolTip(0, str(path))
        if icon is not None:
            item.setIcon(0, icon)
        if kind == "folder":
            item.setExpanded(True)
            item.setChildIndicatorPolicy(QTreeWidgetItem.ChildIndicatorPolicy.ShowIndicator)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        return item

    def _add_folder_tree(self, folder: Path, files: list[Path], code_icon: QIcon, folder_icon: QIcon) -> None:
        root = self._tree_items.get(folder)
        if root is None:
            root = self._new_tree_item(self.file_list, folder, "folder", folder_icon)
            self._tree_items[folder] = root
        root.setExpanded(True)
        for path in files:
            relative = path.relative_to(folder)
            parent = root
            current = folder
            for directory_name in relative.parts[:-1]:
                current /= directory_name
                child = self._tree_items.get(current)
                if child is None:
                    child = self._new_tree_item(parent, current, "folder", folder_icon)
                    self._tree_items[current] = child
                child.setExpanded(True)
                parent = child
            item = self._new_tree_item(parent, path, "file", code_icon)
            self._tree_items[path] = item

    def _sort_file_tree(self) -> None:
        self.file_list.sortItems(0, Qt.SortOrder.AscendingOrder)

        def sort_children(item: QTreeWidgetItem) -> None:
            item.sortChildren(0, Qt.SortOrder.AscendingOrder)
            for child_index in range(item.childCount()):
                sort_children(item.child(child_index))

        for index in range(self.file_list.topLevelItemCount()):
            sort_children(self.file_list.topLevelItem(index))
        self._filter_file_tree(self.file_search.text())

    def _update_tree_issue_badges(self, diagnostics: list[Diagnostic]) -> None:
        """Show issue counts in the workspace without changing stored paths."""
        counts = Counter(Path(item.path).resolve() for item in diagnostics)
        folder_counts: Counter[Path] = Counter()
        for issue_path, count in counts.items():
            parent = issue_path.parent
            while parent in self._tree_items:
                if self._tree_items[parent].data(0, ITEM_KIND_ROLE) == "folder":
                    folder_counts[parent] += count
                parent = parent.parent
        palette = palette_for(self.dark_mode)
        for path, item in self._tree_items.items():
            base_name = path.name
            if item.data(0, ITEM_KIND_ROLE) == "folder":
                count = folder_counts.get(path, 0)
            else:
                count = counts.get(path.resolve(), 0)
            item.setText(0, f"{base_name}  · {count}" if count else base_name)
            item.setForeground(0, QBrush(QColor(palette.danger if count else palette.text)))
            item.setToolTip(0, f"{path}\n{count} 个问题" if count else str(path))

    def _filter_file_tree(self, text: str = "") -> None:
        """Filter the visible tree while keeping matching parent folders open."""
        query = text.strip().casefold()

        def visit(item: QTreeWidgetItem) -> bool:
            child_visible = False
            for index in range(item.childCount()):
                child_visible = visit(item.child(index)) or child_visible
            item_text = item.text(0).casefold()
            path_text = str(item.data(0, Qt.ItemDataRole.UserRole) or "").casefold()
            matches = not query or query in item_text or query in path_text
            item.setHidden(bool(query and not (matches or child_visible)))
            if query and child_visible:
                item.setExpanded(True)
            return matches or child_visible

        for index in range(self.file_list.topLevelItemCount()):
            visit(self.file_list.topLevelItem(index))

    def _show_file_context_menu(self, position: QPoint) -> None:
        item = self.file_list.itemAt(position)
        path = self._path_for_item(item)
        if path is None and item is not None:
            value = item.data(0, Qt.ItemDataRole.UserRole)
            path = Path(str(value)) if value else None
        if path is None:
            return
        menu = QMenu(self)
        copy_action = menu.addAction("复制路径")
        open_action = menu.addAction("打开所在文件夹")
        menu.addSeparator()
        remove_action = menu.addAction("从工作区移除")
        busy = any(
            worker is not None and worker.isRunning()
            for worker in (self._folder_scan_worker, self._check_worker, self._format_worker)
        )
        remove_action.setEnabled(not busy)
        chosen = menu.exec(self.file_list.viewport().mapToGlobal(position))
        if chosen is copy_action:
            QApplication.clipboard().setText(str(path))
            self.status_label.setText("路径已复制到剪贴板")
        elif chosen is open_action:
            directory = path if path.is_dir() else path.parent
            if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve()))):
                self.status_label.setText(f"无法打开文件夹 · {directory}")
        elif chosen is remove_action:
            self._remove_workspace_path(path)

    def _remove_workspace_path(self, path: Path) -> None:
        path = path.resolve()
        if any(
            worker is not None and worker.isRunning()
            for worker in (self._folder_scan_worker, self._check_worker, self._format_worker)
        ):
            self.status_label.setText("任务进行中，暂不能移除文件")
            return
        removed_files = {
            selected_path
            for selected_path in self.selected
            if selected_path == path or (path.is_dir() and path in selected_path.parents)
        }
        if not removed_files:
            return
        self._preview_request_id += 1
        if self.compare_path in removed_files:
            self._invalidate_comparison()
        self.selected = [selected_path for selected_path in self.selected if selected_path not in removed_files]
        persisted_sources: list[Path] = []
        for source in self._selection_sources:
            if source == path:
                continue
            if path.is_dir() and source.is_dir() and source in path.parents:
                # Keep a nested-folder removal stable across restart.  The
                # compact root-folder entry must become the remaining files.
                for selected_path in self.selected:
                    if source in selected_path.parents and selected_path not in persisted_sources:
                        persisted_sources.append(selected_path)
                continue
            if path.is_dir() and path in source.parents:
                continue
            if source not in persisted_sources:
                persisted_sources.append(source)
        self._selection_sources = persisted_sources
        item = self._tree_items.get(path)
        if item is not None:
            parent = item.parent()
            if parent is None:
                self.file_list.takeTopLevelItem(self.file_list.indexOfTopLevelItem(item))
            else:
                parent.removeChild(item)
        elif path in self._tree_items:
            self._tree_items[path].setHidden(True)
        for key in list(self._tree_items):
            if key == path or (path.is_dir() and path in key.parents):
                self._tree_items.pop(key, None)
        self._persist_selection()
        self._refresh_file_watcher()
        if not self.selected:
            self._clear_files()
            return
        self._select_path(self.selected[0])
        self.status_label.setText(f"已移除 {len(removed_files)} 个文件 · 正在重新检查")
        self._check_selected()

    def _persist_selection(self) -> None:
        self.settings.setValue("selection/sources", [str(path) for path in self._selection_sources])

    def _remembered_paths(self) -> list[Path]:
        value = self.settings.value("selection/sources", [])
        values = [value] if isinstance(value, str) else list(value or [])
        return [Path(str(item)).expanduser() for item in values if str(item).strip()]

    def _selection_directory(self) -> Path:
        for path in reversed(self._remembered_paths()):
            if path.is_dir():
                return path
            if path.is_file():
                return path.parent
        return Path.home()

    def _restore_last_selection(self) -> None:
        paths = [path for path in self._remembered_paths() if path.exists()]
        if not paths:
            return
        self._selection_sources = paths
        self._add_paths([str(path) for path in paths], remember=False)
        self._persist_selection()

    def _finish_path_add(self, added: int, accepted_sources: list[Path], remember: bool, auto_check: bool) -> None:
        if added:
            self._sort_file_tree()
            if remember:
                for path in accepted_sources:
                    if path not in self._selection_sources:
                        self._selection_sources.append(path)
                self._persist_selection()
            if self._current_file_path() is None:
                self._select_path(self.selected[0])
            self._refresh_file_watcher()
            self.check_button.setEnabled(True)
            self.format_button.setEnabled(True)
            self.batch_format_button.setEnabled(True)
            self.status_label.setText(f"已添加 {len(self.selected)} 个文件")
            if auto_check:
                self._check_selected()
        elif not self.selected:
            self.status_label.setText("未找到 .c 或 .h 文件")

    def _start_folder_scan(self, folders: list[Path], remember: bool = True) -> None:
        self._discard_folder_scan_result = False
        self._folder_scan_remember = remember
        self.choose_button.setEnabled(False)
        scan_note = "（已跳过常见生成目录）" if self.skip_generated_dirs_check.isChecked() else ""
        self.status_label.setText(f"正在后台扫描 {len(folders)} 个文件夹…{scan_note}")
        worker = FolderScanThread(folders, self.skip_generated_dirs_check.isChecked())
        worker.progress_changed.connect(self._folder_scan_progress_changed)
        worker.completed.connect(self._apply_folder_scan_result)
        worker.finished.connect(self._folder_scan_finished)
        self._folder_scan_worker = worker
        worker.start()

    def _folder_scan_progress_changed(self, folder_name: str, scanned: int) -> None:
        scan_note = " · 已跳过生成目录" if self.skip_generated_dirs_check.isChecked() else ""
        self.status_label.setText(f"正在扫描 {folder_name} · 已发现 {scanned} 个代码文件{scan_note}…")

    def _apply_folder_scan_result(
        self,
        results: list[tuple[Path, list[Path]]],
        failures: list[str],
        cancelled: bool,
    ) -> None:
        if self._discard_folder_scan_result or self._close_after_folder_scan:
            return
        code_icon = tinted_icon(ASSET_DIR / "tabler-file-code.svg", palette_for(self.dark_mode).accent, 16)
        folder_icon = tinted_icon(ASSET_DIR / "tabler-folder.svg", palette_for(self.dark_mode).accent, 16)
        added = 0
        accepted_sources: list[Path] = []
        self.file_list.setUpdatesEnabled(False)
        try:
            for folder, files in results:
                new_files = [path.resolve() for path in files if path.resolve() not in self.selected]
                if not new_files:
                    continue
                self._add_folder_tree(folder, new_files, code_icon, folder_icon)
                self.selected.extend(new_files)
                accepted_sources.append(folder)
                added += len(new_files)
        finally:
            self.file_list.setUpdatesEnabled(True)
        self._finish_path_add(added, accepted_sources, self._folder_scan_remember, auto_check=False)
        if failures:
            QMessageBox.warning(self, "部分文件夹扫描失败", "\n".join(failures))
        if cancelled:
            self.status_label.setText(f"文件夹扫描已取消 · 已添加 {len(self.selected)} 个文件")
        elif added:
            self.status_label.setText(f"文件夹扫描完成 · 已添加 {len(self.selected)} 个文件")

    def _folder_scan_finished(self) -> None:
        worker = self.sender()
        if worker is self._folder_scan_worker:
            self._folder_scan_worker = None
        if isinstance(worker, QThread):
            worker.deleteLater()
        if self._discard_folder_scan_result:
            self._discard_folder_scan_result = False
            self._folder_scan_pending.clear()
            self.choose_button.setEnabled(True)
            if self._close_after_folder_scan:
                self._close_after_folder_scan = False
                self.close()
            return
        if self._folder_scan_pending:
            next_folders, remember = self._folder_scan_pending.pop(0)
            self._start_folder_scan(next_folders, remember)
            return
        self.choose_button.setEnabled(True)
        if self.selected:
            self._check_selected()

    def _add_paths(self, raw_paths: list[str], remember: bool = True) -> None:
        code_icon = tinted_icon(ASSET_DIR / "tabler-file-code.svg", palette_for(self.dark_mode).accent, 16)
        folder_icon = tinted_icon(ASSET_DIR / "tabler-folder.svg", palette_for(self.dark_mode).accent, 16)
        added = 0
        accepted_sources: list[Path] = []
        folder_paths: list[Path] = []
        self.file_list.setUpdatesEnabled(False)
        try:
            for raw_path in raw_paths:
                path = Path(raw_path).expanduser().resolve()
                if path.is_dir():
                    if path not in folder_paths:
                        folder_paths.append(path)
                    continue
                if path.suffix.lower() not in SUPPORTED_EXTENSIONS or not path.is_file() or path in self.selected:
                    continue
                self.selected.append(path)
                accepted_sources.append(path)
                item = self._new_tree_item(self.file_list, path, "file", code_icon)
                self._tree_items[path] = item
                added += 1
        finally:
            self.file_list.setUpdatesEnabled(True)
        self._finish_path_add(added, accepted_sources, remember, auto_check=not folder_paths)
        if folder_paths:
            if self._folder_scan_worker is not None and self._folder_scan_worker.isRunning():
                self._folder_scan_pending.append((folder_paths, remember))
                self.status_label.setText("已加入文件，等待后台扫描文件夹…")
            else:
                self._start_folder_scan(folder_paths, remember)

    def _refresh_file_watcher(self) -> None:
        watched = set(self._file_watcher.files())
        if watched:
            self._file_watcher.removePaths(list(watched))
        self._watched_paths = {path for path in self.selected if path.is_file()}
        if self.auto_recheck_check.isChecked() and self._watched_paths:
            self._file_watcher.addPaths([str(path) for path in self._watched_paths])

    def _watched_file_changed(self, path_text: str) -> None:
        path = Path(path_text).resolve()
        if not self.auto_recheck_check.isChecked() or path not in self.selected:
            return
        self._watch_pending_path = path
        if self.compare_path == path:
            self._invalidate_comparison()
        self.status_label.setText(f"检测到文件变化 · {path.name} · 准备重新检查")
        self._watch_reload_timer.start()

    def _auto_recheck(self) -> None:
        path = self._watch_pending_path
        self._watch_pending_path = None
        self._refresh_file_watcher()
        if path is not None and path in self.selected:
            self._check_selected()

    def _goto_line(self) -> None:
        current_page = self.center_stack.currentWidget()
        if current_page == self.check_page:
            editor = self.check_editor
        elif current_page == self.compare_page:
            editor = self.after_editor
        else:
            return
        current_line = editor.textCursor().blockNumber() + 1
        line, accepted = QInputDialog.getInt(
            self,
            "跳转到行",
            "行号：",
            current_line,
            1,
            max(1, editor.document().blockCount()),
        )
        if not accepted:
            return
        block = editor.document().findBlockByLineNumber(line - 1)
        if block.isValid():
            editor.setTextCursor(QTextCursor(block))
            editor.centerCursor()
            editor.setFocus()

    def _selected_file_changed(self, current: QTreeWidgetItem | None, previous: QTreeWidgetItem | None = None) -> None:
        del previous
        path = self._path_for_item(current)
        if path is None:
            return
        showing_comparison = self.center_stack.currentWidget() == self.compare_page
        if self.compare_path is not None and path != self.compare_path:
            self._invalidate_comparison()
        source = self.check_sources.get(path)
        if source is not None:
            self.check_path = path
            self.check_editor.setPlainText(source)
            self.check_file_label.setText(path.name)
            self._apply_diagnostic_highlighting()
        if showing_comparison and path != self.compare_path:
            self._prepare_comparison()

    def _invalidate_comparison(self) -> None:
        """Clear stale output before another file or check result can be used."""
        if self.compare_path is None and not self.compare_content:
            return
        self.compare_path = None
        self.compare_content = ""
        self.compare_metadata = None
        self.compare_source_digest = None
        self._diff_blocks = []
        self._current_diff_index = -1
        self.before_editor.clear()
        self.after_editor.clear()
        self.compare_file_label.setText("尚未生成对比")
        self.compare_metadata_label.setText("")
        self.compare_summary_label.setText("等待生成差异")
        self._update_diff_navigation()
        self.commit_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.export_patch_button.setEnabled(False)
        self.output_value_label.setText("先生成并查看对比结果")
        self.result_label.setText("暂无格式化结果")

    def _choose_paths(self) -> None:
        dialog = UnifiedPathDialog(self, self._selection_directory(), palette_for(self.dark_mode).accent)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        paths = dialog.selected_paths()
        if paths:
            self._add_paths([str(path) for path in paths])
        else:
            self.status_label.setText("未选择文件或文件夹")

    def _open_template_editor(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("templateDialog")
        dialog.setWindowTitle("自定义格式模板")
        dialog.resize(860, 620)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        hint = QLabel("修改 .c / .h 参考代码中的缩进、空格和宏对齐方式，保存后用于下一次格式化对比。")
        hint.setObjectName("secondaryText")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        profile_row = QHBoxLayout()
        profile_row.setSpacing(8)
        profile_label = QLabel("模板配置")
        profile_label.setObjectName("eyebrow")
        profile_row.addWidget(profile_label)
        profile_combo = QComboBox()
        profile_combo.setObjectName("profileCombo")
        profile_combo.addItems(list_profiles())
        if self.template_profile in list_profiles():
            profile_combo.setCurrentText(self.template_profile)
        profile_row.addWidget(profile_combo, 1)
        new_profile_button = QPushButton("新建模板")
        new_profile_button.setObjectName("secondaryButton")
        new_profile_button.setToolTip("从内置模板创建一套新的命名模板")
        profile_row.addWidget(new_profile_button)
        layout.addLayout(profile_row)

        apply_project = QCheckBox("将此模板应用到当前项目（保存 .ccfconfig.json）")
        apply_project.setObjectName("templateProjectCheck")
        apply_project.setEnabled(bool(self._current_file_path() or self.selected))
        layout.addWidget(apply_project)

        tabs = QTabWidget()
        editors: dict[str, QPlainTextEdit] = {}
        for suffix, title in ((".c", "template.c"), (".h", "template.h")):
            editor = QPlainTextEdit()
            editor.setObjectName("templateCodeView")
            editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            code_font = QFont(preferred_code_font_family(), 10)
            code_font.setStyleHint(QFont.StyleHint.Monospace)
            code_font.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, 0.2)
            editor.setFont(code_font)
            editor.setTabStopDistance(editor.fontMetrics().horizontalAdvance("    ") or 32)
            editor._ccf_highlighter = CCodeHighlighter(editor.document(), dark=True)
            editor.setPlainText(load_template(suffix, profile_template_dir(profile_combo.currentText())))
            editors[suffix] = editor
            tabs.addTab(editor, title)
        layout.addWidget(tabs, 1)

        def load_selected_profile(name: str) -> None:
            directory = profile_template_dir(name)
            for suffix, editor in editors.items():
                editor.setPlainText(load_template(suffix, directory))

        profile_combo.currentTextChanged.connect(load_selected_profile)

        def create_profile() -> None:
            name, accepted = QInputDialog.getText(dialog, "新建模板", "模板名称（仅字母、数字、点、下划线和短横线）：")
            name = name.strip()
            if not accepted or not name:
                return
            if name in list_profiles():
                QMessageBox.warning(dialog, "模板已存在", f"模板“{name}”已经存在。")
                return
            try:
                profile_template_dir(name)
            except ValueError as error:
                QMessageBox.warning(dialog, "模板名称无效", str(error))
                return
            profile_combo.addItem(name)
            profile_combo.setCurrentText(name)

        new_profile_button.clicked.connect(create_profile)

        buttons = QDialogButtonBox()
        cancel_button = buttons.addButton("取消", QDialogButtonBox.ButtonRole.RejectRole)
        cancel_button.setObjectName("secondaryButton")
        save_button = buttons.addButton("保存模板", QDialogButtonBox.ButtonRole.AcceptRole)
        save_button.setObjectName("primaryButton")
        cancel_button.setMinimumHeight(36)
        save_button.setMinimumHeight(36)
        buttons.rejected.connect(dialog.reject)
        buttons.accepted.connect(dialog.accept)
        layout.addWidget(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            profile = profile_combo.currentText()
            if profile == DEFAULT_PROFILE:
                self.template_dir = None
            else:
                self.template_dir = save_profile_templates(
                    profile,
                    editors[".c"].toPlainText(),
                    editors[".h"].toPlainText(),
                )
            self.template_profile = profile
            self.template_button.setText(f"模板：{profile}")
            if apply_project.isChecked():
                target = self._current_file_path() or (self.selected[0] if self.selected else None)
                if target is not None:
                    save_project_config(target, profile)
            self.result_label.setText(f"模板已保存 · {profile}")
            self.status_label.setText(f"模板已保存 · 后续格式化使用“{profile}”")
        except Exception as error:
            QMessageBox.critical(self, "保存模板失败", str(error))

    def _clear_files(self) -> None:
        self._preview_request_id += 1
        self._preview_pending = None
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_worker.requestInterruption()
        self._watch_reload_timer.stop()
        self._screen_glass_refresh_timer.stop()
        if self._screen_glass_backdrop is not None:
            self._screen_glass_backdrop.cleanup()
        self._watch_pending_path = None
        if self._file_watcher.files():
            self._file_watcher.removePaths(self._file_watcher.files())
        self._watched_paths.clear()
        if self._check_worker is not None and self._check_worker.isRunning():
            self._discard_check_result = True
            self._restart_check_after_finish = False
            self._check_worker.requestInterruption()
        if self._format_worker is not None and self._format_worker.isRunning():
            self._format_worker.requestInterruption()
            self._close_after_format = False
        if self._folder_scan_worker is not None and self._folder_scan_worker.isRunning():
            self._discard_folder_scan_result = True
            self._folder_scan_pending.clear()
            self._folder_scan_worker.requestInterruption()
        self.selected.clear()
        self._selection_sources.clear()
        self.settings.remove("selection/sources")
        self._tree_items.clear()
        self.file_list.clear()
        self.compare_path = None
        self.compare_content = ""
        self.compare_metadata = None
        self._diff_blocks = []
        self._current_diff_index = -1
        self.before_editor.clear()
        self.after_editor.clear()
        self.compare_file_label.setText("尚未生成对比")
        self.compare_metadata_label.setText("")
        self.compare_summary_label.setText("等待生成差异")
        self._update_diff_navigation()
        self.check_path = None
        self.check_diagnostics = []
        self.check_sources = {}
        self._update_tree_issue_badges([])
        self.check_editor.clear()
        self.check_file_label.setText("尚未检查")
        self.check_summary_label.setText("拖入文件后自动检查")
        self._render_diagnostics([])
        self.check_progress.hide()
        self.check_button.setEnabled(False)
        self.format_button.setEnabled(False)
        self.batch_format_button.setEnabled(False)
        self.commit_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.export_patch_button.setEnabled(False)
        self.export_report_button.setEnabled(False)
        self.overview_button.setEnabled(False)
        self.result_label.setText("暂无格式化结果")
        self.status_label.setText("等待添加代码文件")
        self.center_stack.setCurrentWidget(self.drop_page)

    def _format_selected(self) -> None:
        if not self.selected:
            QMessageBox.warning(self, "没有文件", "请先选择或拖入 .c / .h 文件。")
            return
        self._prepare_comparison()

    def _mode_changed(self) -> None:
        overwrite = self.mode_segment.currentData() == "overwrite"
        self.commit_button.setText("覆盖原文件" if overwrite else "另存为")
        self.commit_button.setObjectName("dangerButton" if overwrite else "secondaryButton")
        self.commit_button.style().unpolish(self.commit_button)
        self.commit_button.style().polish(self.commit_button)
        self.commit_button.setEnabled(self.compare_path is not None)
        self.output_value_label.setText(
            "先生成并查看对比结果"
            if self.compare_path is None
            else ("对比确认后覆盖原文件" if overwrite else "对比确认后另存为新文件")
        )

    def _prepare_comparison(self, path: Path | None = None, safe_repair: bool = False, indent_width: int = 4) -> None:
        path = path or self._current_file_path()
        if path is None:
            path = self.selected[0]
            self._select_path(path)
        self._preview_request_id += 1
        request_id = self._preview_request_id
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._preview_pending = (request_id, path, safe_repair, indent_width)
            self._preview_worker.requestInterruption()
            self.status_label.setText(f"正在切换预览 · {path.name}")
            return
        self._start_preview(request_id, path, safe_repair, indent_width)

    def _start_preview(self, request_id: int, path: Path, safe_repair: bool, indent_width: int) -> None:
        self._invalidate_comparison()
        self.commit_button.setEnabled(False)
        self.copy_button.setEnabled(False)
        self.export_patch_button.setEnabled(False)
        self.status_label.setText(f"正在生成预览 · {path.name}")
        worker = PreviewThread(request_id, path, self.template_dir, safe_repair, indent_width)
        worker.completed.connect(self._apply_preview_result)
        worker.finished.connect(self._preview_finished)
        self._preview_worker = worker
        worker.start()

    def _apply_preview_result(
        self,
        request_id: int,
        path: Path,
        original: str | None,
        formatted: str | None,
        metadata: SourceMetadata | None,
        error: str | None,
    ) -> None:
        if request_id != self._preview_request_id or path != self._current_file_path():
            return
        if error is not None:
            QMessageBox.critical(self, "无法生成对比", error)
            return
        if original is None or formatted is None or metadata is None:
            QMessageBox.critical(self, "无法生成对比", "预览线程没有返回完整结果。")
            return
        self.compare_path = path
        self.compare_content = formatted
        self.compare_metadata = metadata
        try:
            self.compare_source_digest = source_digest(path)
        except OSError as error:
            self._invalidate_comparison()
            QMessageBox.warning(self, "源文件已变化", f"预览完成后无法读取源文件，未生成可写入结果。\n\n{error}")
            return
        self._current_diff_index = -1
        self.before_editor.setPlainText(original)
        self.after_editor.setPlainText(formatted)
        self._apply_diff_highlighting()
        self.compare_file_label.setText(path.name)
        newline_name = {"\r\n": "CRLF", "\n": "LF", "\r": "CR"}.get(metadata.newline, "换行")
        encoding_name = metadata.encoding.upper()
        if metadata.bom:
            encoding_name += " BOM"
        self.compare_metadata_label.setText(f"{encoding_name} · {newline_name}")
        self.center_stack.setCurrentWidget(self.compare_page)
        self.commit_button.setEnabled(True)
        self.copy_button.setEnabled(True)
        self.export_patch_button.setEnabled(True)
        self.output_value_label.setText(
            "对比确认后覆盖原文件"
            if self.mode_segment.currentData() == "overwrite"
            else "对比确认后另存为新文件"
        )
        self.result_label.setText("对比已生成\n" + path.name)
        action = "覆盖原文件" if self.mode_segment.currentData() == "overwrite" else "另存为新文件"
        self.status_label.setText(f"请确认右侧结果后再{action}")

    def _preview_finished(self) -> None:
        worker = self.sender()
        if worker is self._preview_worker:
            self._preview_worker = None
        if isinstance(worker, QThread):
            worker.deleteLater()
        pending = self._preview_pending
        self._preview_pending = None
        if pending is not None:
            self._start_preview(*pending)
        elif self._close_after_preview:
            self._close_after_preview = False
            self.close()

    def _commit_comparison(self) -> None:
        if self.mode_segment.currentData() == "overwrite":
            self._overwrite_current()
        else:
            self._save_as_current()

    def _copy_comparison(self) -> None:
        if not self.compare_content:
            return
        QApplication.clipboard().setText(self.compare_content)
        self.status_label.setText("格式化结果已复制到剪贴板")

    def _export_diff_patch(self) -> None:
        if self.compare_path is None:
            return
        patch = unified_diff(
            self.before_editor.toPlainText(),
            self.after_editor.toPlainText(),
            str(self.compare_path),
        )
        if not patch:
            self.status_label.setText("当前对比没有差异，无需导出补丁")
            return
        remembered = self.settings.value("output/last_directory", str(self.compare_path.parent))
        output_dir = Path(str(remembered)).expanduser()
        if not output_dir.is_dir():
            output_dir = self.compare_path.parent
        suggested = output_dir / f"{self.compare_path.stem}.formatted.patch"
        output, _ = QFileDialog.getSaveFileName(self, "导出差异补丁", str(suggested), "Patch files (*.patch);;All files (*)")
        if not output:
            return
        target = Path(output)
        if target.exists():
            answer = QMessageBox.question(
                self,
                "确认替换补丁",
                f"补丁文件已经存在，确定要替换吗？\n\n{target}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            target.write_text(patch, encoding="utf-8", newline="\n")
        except OSError as error:
            QMessageBox.critical(self, "导出补丁失败", str(error))
            return
        self.settings.setValue("output/last_directory", str(target.parent.resolve()))
        self._last_output_directory = target.parent.resolve()
        self.open_output_button.setEnabled(True)
        self.status_label.setText("差异补丁已导出")
        self.result_label.setText("已导出差异补丁\n" + target.name)
        QMessageBox.information(self, "导出完成", f"已保存：\n{target}")

    def _open_output_directory(self) -> None:
        directory = self._last_output_directory
        if directory is None:
            remembered = self.settings.value("output/last_directory", "")
            directory = Path(str(remembered)).expanduser() if str(remembered).strip() else None
        if directory is None or not directory.is_dir():
            self.status_label.setText("最近输出目录不存在")
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(directory.resolve()))):
            self.status_label.setText(f"无法打开输出目录 · {directory}")

    def _overwrite_current(self) -> None:
        if self.compare_path is None:
            return
        answer = QMessageBox.question(
            self,
            "确认覆盖原文件",
            f"确定要用格式化结果覆盖以下文件吗？\n\n{self.compare_path}",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            current_digest = source_digest(self.compare_path)
        except OSError as error:
            QMessageBox.critical(self, "覆盖失败", str(error))
            return
        if self.compare_source_digest is not None and current_digest != self.compare_source_digest:
            self._invalidate_comparison()
            QMessageBox.warning(
                self,
                "源文件已变化",
                "对比生成后源文件已被其他程序修改，旧结果不会覆盖当前文件。请重新生成对比。",
            )
            return
        try:
            overwrite_file(
                self.compare_path,
                self.compare_content,
                self.compare_metadata,
            )
        except Exception as error:
            QMessageBox.critical(self, "覆盖失败", str(error))
            return
        self.commit_button.setEnabled(False)
        self.status_label.setText("原文件已覆盖")
        self.result_label.setText("已覆盖原文件\n" + self.compare_path.name)
        QMessageBox.information(self, "覆盖完成", f"已覆盖：\n{self.compare_path}")

    def _save_as_current(self) -> None:
        if self.compare_path is None:
            return
        remembered = self.settings.value("output/last_directory", str(self.compare_path.parent))
        output_dir = Path(str(remembered)).expanduser()
        if not output_dir.is_dir():
            output_dir = self.compare_path.parent
        suggested = output_dir / f"{self.compare_path.stem}.formatted{self.compare_path.suffix}"
        output, _ = QFileDialog.getSaveFileName(self, "另存格式化结果", str(suggested), "C files (*.c *.h)")
        if not output:
            return
        target = Path(output)
        self.settings.setValue("output/last_directory", str(target.parent.resolve()))
        self._last_output_directory = target.parent.resolve()
        self.open_output_button.setEnabled(True)
        if target.resolve() == self.compare_path.resolve():
            QMessageBox.warning(self, "不能覆盖原文件", "“对比后另存”需要选择一个新的文件路径。")
            return
        if target.exists():
            answer = QMessageBox.question(
                self,
                "确认覆盖已有结果",
                f"目标文件已经存在，确定要替换吗？\n\n{target}",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        try:
            saved = save_source(target, self.compare_content, self.compare_metadata)
        except Exception as error:
            QMessageBox.critical(self, "另存失败", str(error))
            return
        self.status_label.setText("格式化结果已另存")
        self.result_label.setText("已另存为\n" + saved.name)
        QMessageBox.information(self, "另存完成", f"已保存：\n{saved}")

    def toggle_maximize(self) -> None:
        if self._window_state_changing:
            return

        self._window_state_changing = True
        self._glass_capture_suspended = True
        self._glass_resize_timer.stop()
        self._screen_glass_refresh_timer.stop()
        if self._screen_glass_backdrop is not None:
            self._screen_glass_backdrop.set_live(False)

        actually_maximized = self.isMaximized() or self._maximized
        if actually_maximized:
            self.showNormal()
            geometry = self._normal_geometry
            self._maximized = False
            self._normal_geometry = None
            self.resize_grip.show()
            if geometry is not None and geometry.isValid():
                # A monitor may have been disconnected while the app was
                # maximized.  Keep at least part of the restored window on a
                # currently available screen instead of restoring it offscreen.
                screen = QApplication.screenAt(geometry.center()) or QApplication.primaryScreen()
                available = screen.availableGeometry() if screen is not None else QRect()
                if available.isValid() and not geometry.intersects(available):
                    geometry.moveCenter(available.center())
                self.setGeometry(geometry)
            self._fullscreen_effect_lock = False
            self.effect_segment.set_allowed_modes(set(EFFECT_MODES))
            previous_effect = self._effect_mode_before_fullscreen
            self._effect_mode_before_fullscreen = None
            if previous_effect in EFFECT_MODES and previous_effect != self.effect_mode:
                self.effect_segment.setCurrentData(previous_effect)
        else:
            self._effect_mode_before_fullscreen = self.effect_mode
            self._fullscreen_effect_lock = True
            self.effect_segment.set_allowed_modes({"none"})
            self.effect_segment.setCurrentData("none")
            geometry = self.geometry()
            self._normal_geometry = QRect(geometry) if geometry.isValid() else None
            self._maximized = True
            self.resize_grip.hide()
            # Do not call showMaximized() for this frameless translucent
            # window. On some Windows/Qt/DWM combinations that native state
            # transition tears down the layered surface while the screen
            # capturer is still holding it and the process exits without a
            # Python exception. Filling the current work area gives the same
            # user-visible result while keeping the window in a stable normal
            # state.
            screen = QApplication.screenAt(geometry.center()) or QApplication.primaryScreen()
            available = screen.availableGeometry() if screen is not None else QRect()
            if available.isValid():
                self.setGeometry(available)

        # Resize events are synchronous on some Qt/Windows combinations and
        # deferred on others.  Restart native capture only after both have
        # settled, which avoids grabbing a half-reconfigured layered window.
        QTimer.singleShot(0, self._finish_window_state_change)

    def _finish_window_state_change(self) -> None:
        self._window_state_changing = False
        if self._closing:
            return
        maximized = self.isMaximized() or self._maximized
        self.resize_grip.setVisible(not maximized)
        # Keep native desktop capture disabled in the maximized layout. This
        # avoids a known crash-prone path in Windows Magnification when a
        # layered frameless window covers the work area. The normal view still
        # gets the live material after restore.
        if not maximized:
            self._glass_capture_suspended = False
            self._sync_effect_mode()
            self._schedule_screen_glass_refresh()

    def toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()
        if self.compare_path is not None:
            self._apply_diff_highlighting()
        if self.check_path is not None:
            self._apply_diagnostic_highlighting()

    def _effect_mode_changed(self, mode: str) -> None:
        if self._fullscreen_effect_lock:
            mode = "none"
        self.effect_mode = mode if mode in EFFECT_MODES else "liquid"
        self.title_bar.set_effect_mode(self.effect_mode)
        self.footer.set_effect_mode(self.effect_mode)
        self._screen_glass_refresh_timer.stop()
        if self.effect_mode in {"liquid", "frosted"}:
            self._sync_effect_mode()
        elif self._screen_glass_backdrop is not None:
            self._screen_glass_backdrop.set_live(False)
        if self._glass_ready and not self._glass_capture_suspended:
            _apply_native_glass(self, self.dark_mode, self.effect_mode)
        if not self._fullscreen_effect_lock:
            self.settings.setValue("ui/effect_mode", self.effect_mode)

    def _refresh_asset_icons(self, palette: ThemePalette) -> None:
        code_icon = tinted_icon(ASSET_DIR / "tabler-file-code.svg", palette.accent, 16)
        folder_icon = tinted_icon(ASSET_DIR / "tabler-folder.svg", palette.accent, 16)
        file_icon = tinted_icon(ASSET_DIR / "alibaba-file-cloud.svg", palette.accent, 38)
        self.choose_button.setIcon(tinted_icon(ASSET_DIR / "tabler-file-plus.svg", palette.accent, 17))
        self.drop_icon.setPixmap(file_icon.pixmap(38, 38))
        if hasattr(self, "open_output_button"):
            self.open_output_button.setIcon(tinted_icon(ASSET_DIR / "tabler-folder.svg", palette.accent, 16))
        for path, item in self._tree_items.items():
            if item.data(0, ITEM_KIND_ROLE) == "folder":
                item.setIcon(0, folder_icon)
            elif path.suffix.lower() in SUPPORTED_EXTENSIONS:
                item.setIcon(0, code_icon)
        if hasattr(self, "check_diagnostics"):
            self._update_tree_issue_badges(self.check_diagnostics)

    def _apply_theme(self) -> None:
        palette: ThemePalette = palette_for(self.dark_mode)
        dropdown_icon = DROPDOWN_ICON_PATH.as_posix()
        # Liquid Glass belongs to functional chrome. The layered gradients
        # provide its light-catching edge and depth; content stays solid.
        chrome_alpha = 228 if sys.platform == "win32" else 255
        glass_chrome = (
            f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {_rgba(palette.elevated, chrome_alpha)}, "
            f"stop:0.48 {_rgba(palette.surface, 214 if sys.platform == 'win32' else 255)}, "
            f"stop:1 {_rgba(palette.button, 204 if sys.platform == 'win32' else 255)})"
        )
        glass_panel = (
            f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {_rgba(palette.elevated, 242 if sys.platform == 'win32' else 255)}, "
            f"stop:1 {_rgba(palette.surface, 222 if sys.platform == 'win32' else 255)})"
        )
        glass_button = (
            f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
            f"stop:0 {_rgba(palette.elevated, 242 if sys.platform == 'win32' else 255)}, "
            f"stop:1 {_rgba(palette.button, 216 if sys.platform == 'win32' else 255)})"
        )
        self.setStyleSheet(f"""
            QWidget {{ color: {palette.text}; font-family: '{self.font_family}'; }}
            QDialog {{ background: {palette.surface}; color: {palette.text}; }}
            QToolTip {{ background: {palette.elevated}; color: {palette.text}; border: 1px solid {palette.border}; padding: 5px 7px; }}
            QPushButton:disabled, QToolButton:disabled {{ color: {palette.tertiary_text}; }}
            QCheckBox {{ color: {palette.secondary_text}; }}
            QAbstractItemView {{ outline: none; selection-background-color: {palette.selection}; selection-color: {palette.text}; }}
            #windowCanvas {{ background: transparent; }}
            #mainContainer {{ background: transparent; border: none; border-radius: 20px; }}
            #titleBar {{ background: transparent; border: none; border-top-left-radius: 20px; border-top-right-radius: 20px; }}
            #windowTitle {{ font-size: 13px; font-weight: 700; letter-spacing: 0.1px; }}
            #themeButton {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 15px; padding: 0; margin: 0; }}
            #themeButton:hover {{ background: {palette.button_hover}; }}
            /* Sidebars stay opaque so live glass work is limited to chrome. */
            #leftSidebar, #rightSidebar {{ background: {palette.sidebar}; }}
            #leftSidebar {{ border-right: none; }}
            #rightSidebar {{ border-left: none; }}
            #leftSidebar {{ border-bottom-left-radius: 20px; }}
            #rightSidebar {{ border-bottom-right-radius: 20px; }}
            #actionScroll, #actionScrollContent {{ background: transparent; border: none; }}
            #actionScroll QScrollBar:vertical {{ background: transparent; width: 8px; margin: 3px 1px 3px 0; }}
            #actionScroll QScrollBar::handle:vertical {{ background: {palette.tertiary_text}; border-radius: 4px; min-height: 26px; }}
            #actionScroll QScrollBar::add-line:vertical, #actionScroll QScrollBar::sub-line:vertical {{ height: 0; }}
            #actionScroll QScrollBar::add-page:vertical, #actionScroll QScrollBar::sub-page:vertical {{ background: transparent; }}
            #centerPanel {{ background: {palette.content}; }}
            #sectionTitle {{ font-size: 16px; font-weight: 600; }}
            #eyebrow {{ color: {palette.secondary_text}; font-size: 10px; }}
            #secondaryText {{ color: {palette.secondary_text}; font-size: 10px; }}
            #safeText {{ color: {palette.secondary_text}; font-size: 10px; }}
            #infoCard {{ background: {glass_panel}; border: none; border-radius: 10px; }}
            #cardTitle {{ font-size: 11px; font-weight: 600; }}
            #compareSummary {{ color: {palette.accent}; background: {palette.selection}; border-radius: 8px; padding: 4px 8px; font-size: 10px; font-weight: 600; }}
            #actionGroup {{ background: {glass_panel}; border: none; border-radius: 12px; }}
            #actionTitle {{ font-size: 11px; font-weight: 700; }}
            #actionHint {{ color: {palette.secondary_text}; font-size: 10px; }}
            #checkProgress {{ background: {glass_button}; color: {palette.text}; border: none; border-radius: 6px; min-height: 14px; max-height: 14px; text-align: center; font-size: 8px; }}
            #checkProgress::chunk {{ background: {palette.accent}; border-radius: 5px; }}
            #diffPosition {{ color: {palette.secondary_text}; font-size: 10px; font-weight: 600; padding-left: 8px; }}
            #compareMetadata {{ color: {palette.tertiary_text}; font-size: 9px; padding-left: 4px; }}
            #compareOption {{ color: {palette.secondary_text}; font-size: 9px; spacing: 5px; }}
            #compareOption::indicator, #templateProjectCheck::indicator {{ width: 14px; height: 14px; border: 1px solid {palette.border}; border-radius: 4px; background: {palette.surface}; }}
            #compareOption::indicator:checked, #templateProjectCheck::indicator:checked {{ background: {palette.accent}; border-color: {palette.accent}; }}
            #diffNavButton {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 8px; min-width: 24px; min-height: 24px; font-size: 18px; padding: 0 2px 2px 2px; }}
            #diffNavButton:hover {{ background: {palette.button_hover}; color: {palette.accent}; }}
            #diffNavButton:disabled {{ color: {palette.tertiary_text}; background: {palette.sidebar}; }}
            #diffOverview {{ border: none; border-radius: 8px; }}
            #checkSummary {{ color: {palette.accent}; background: {palette.selection}; border-radius: 8px; padding: 4px 8px; font-size: 10px; font-weight: 600; }}
            #codeTitle {{ font-size: 11px; font-weight: 600; }}
            #beforeBadge, #afterBadge {{ border-radius: 7px; padding: 3px 7px; font-size: 9px; font-weight: 600; }}
            #beforeBadge {{ color: {palette.danger}; background: {palette.danger_soft}; }}
            #afterBadge {{ color: {palette.success}; background: {palette.success_soft}; }}
            #cardValue {{ font-size: 10px; }}
            #templateDialog {{ background: {palette.surface}; }}
            #templateDialog QLabel {{ color: {palette.secondary_text}; }}
            #templateDialog #profileCombo, #issueSeverity, #overviewStatus {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 9px; padding: 5px 28px 5px 9px; min-height: 20px; }}
            #templateDialog #profileCombo:hover, #issueSeverity:hover, #overviewStatus:hover {{ background: {palette.button_hover}; border-color: {palette.accent}; }}
            #templateDialog #profileCombo:focus, #issueSeverity:focus, #overviewStatus:focus {{ background: {palette.surface}; border-color: {palette.accent}; }}
            #templateDialog #profileCombo::drop-down, #issueSeverity::drop-down, #overviewStatus::drop-down {{ subcontrol-origin: border; subcontrol-position: top right; width: 25px; border: none; border-top-right-radius: 9px; border-bottom-right-radius: 9px; }}
            #templateDialog #profileCombo::drop-down:hover, #issueSeverity::drop-down:hover, #overviewStatus::drop-down:hover {{ background: {palette.button_hover}; }}
            #templateDialog #profileCombo::down-arrow, #issueSeverity::down-arrow, #overviewStatus::down-arrow {{ image: url("{dropdown_icon}"); width: 13px; height: 13px; }}
            #templateDialog #profileCombo QAbstractItemView {{ background: {palette.surface}; color: {palette.text}; selection-background-color: {palette.selection}; border: none; }}
            #templateProjectCheck {{ color: {palette.secondary_text}; font-size: 10px; spacing: 6px; }}
            #templateDialog QTabWidget::pane {{ background: #1E1E1E; border: 1px solid #3C3C3C; border-radius: 10px; top: -1px; }}
            #templateDialog QTabBar::tab {{ background: {palette.button}; color: {palette.secondary_text}; border: 1px solid {palette.border}; padding: 7px 16px; margin-right: 3px; border-top-left-radius: 7px; border-top-right-radius: 7px; }}
            #templateDialog QTabBar::tab:selected {{ background: #1E1E1E; color: #D4D4D4; border-bottom-color: #1E1E1E; }}
            #templateDialog QDialogButtonBox {{ border-top: 1px solid {palette.separator}; padding-top: 12px; }}
            #templateDialog #secondaryButton, #templateDialog #primaryButton {{ min-width: 108px; font-size: 10pt; font-weight: 600; }}
            #fileList {{ background: {palette.surface}; border: none; border-radius: 10px; padding: 5px; outline: none; }}
            #fileList::item {{ padding: 7px 8px; border-radius: 7px; }}
             #fileList::item:hover {{ background: {palette.button_hover}; }}
             #fileList::item:selected {{ background: {palette.selection}; color: {palette.text}; }}
             #fileList::branch {{ background: transparent; }}
            QMenu {{ background: {palette.surface}; color: {palette.text}; border: none; border-radius: 9px; padding: 5px; }}
            QMenu::item {{ padding: 6px 28px 6px 10px; border-radius: 6px; }}
            QMenu::item:selected {{ background: {palette.selection}; color: {palette.text}; }}
            QMenu::separator {{ height: 1px; background: {palette.separator}; margin: 4px 7px; }}
            #issueList {{ background: {palette.surface}; color: {palette.text}; border: none; border-radius: 10px; padding: 4px; outline: none; }}
            #issueList::item {{ padding: 6px 7px; border-radius: 7px; }}
            #issueList::item:hover {{ background: {palette.button_hover}; }}
            #issueList::item:selected {{ background: {palette.selection}; color: {palette.text}; }}
            #issueSearch {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 8px; padding: 5px 7px; min-height: 20px; }}
            #issueSearch:focus {{ background: {palette.surface}; }}
            #issueSearch::clear-button {{ width: 14px; height: 14px; }}
            #workspaceOption {{ color: {palette.secondary_text}; font-size: 9px; spacing: 6px; padding: 1px 2px; }}
            #workspaceOption::indicator {{ width: 14px; height: 14px; border: 1px solid {palette.border}; border-radius: 4px; background: {palette.surface}; }}
            #workspaceOption::indicator:checked {{ background: {palette.accent}; border-color: {palette.accent}; }}
            #issueSeverity QAbstractItemView, #overviewStatus QAbstractItemView {{ background: {palette.surface}; color: {palette.text}; selection-background-color: {palette.selection}; border: none; }}
            #overviewSearch {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 9px; padding: 6px 9px; min-height: 20px; }}
            #overviewSearch:focus {{ background: {palette.surface}; }}
            #overviewTable {{ background: {palette.surface}; alternate-background-color: {palette.button}; color: {palette.text}; border: none; border-radius: 10px; padding: 4px; outline: none; selection-background-color: {palette.selection}; gridline-color: transparent; }}
            #overviewTable::item {{ padding: 7px 8px; border-radius: 5px; }}
            #overviewTable::item:selected {{ background: {palette.selection}; color: {palette.text}; }}
            #overviewTable QHeaderView::section {{ background: {palette.button}; color: {palette.secondary_text}; border: none; padding: 7px 8px; font-weight: 600; }}
            #secondaryButton {{ background: {glass_button}; color: {palette.text}; border: none; border-radius: 9px; padding: 7px 12px; }}
             #secondaryButton:hover {{ background: {palette.button_hover}; }}
             #secondaryButton:pressed {{ background: {palette.border}; }}
             #pathPicker {{ background: {palette.surface}; }}
             #pathDriveButton {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 7px; padding: 5px 10px; }}
             #pathDriveButton:hover {{ background: {palette.button_hover}; border-color: {palette.accent}; }}
             #pathPickerTree {{ background: {palette.surface}; color: {palette.text}; border: none; border-radius: 10px; outline: none; padding: 4px; }}
             #pathPickerTree::item {{ padding: 6px 8px; border-radius: 6px; }}
             #pathPickerTree::item:hover {{ background: {palette.button_hover}; }}
             #pathPickerTree::item:selected {{ background: {palette.selection}; color: {palette.text}; }}
             #pathPickerTree::branch {{ background: transparent; }}
             #primaryButton {{ background: {palette.accent}; color: white; border: none; border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            #primaryButton:hover {{ background: {palette.accent_hover}; }}
            #primaryButton:pressed {{ background: {palette.accent_pressed}; }}
            #modeSegment {{ background: {palette.button}; border: none; border-radius: 10px; }}
            #modeSegmentButton {{ background: transparent; color: {palette.secondary_text}; border: none; border-radius: 7px; padding: 6px 12px; font-size: 10px; font-weight: 600; }}
            #modeSegmentButton:hover {{ color: {palette.text}; background: {palette.button_hover}; }}
            #modeSegmentButton:checked {{ color: {palette.text}; background: {palette.surface}; border: 1px solid {palette.border}; }}
            #effectSegment {{ background: {palette.button}; border: none; border-radius: 10px; }}
            #effectSegmentButton {{ background: transparent; color: {palette.secondary_text}; border: none; border-radius: 7px; padding: 5px 8px; font-size: 9px; font-weight: 600; }}
            #effectSegmentButton:hover {{ color: {palette.text}; background: {palette.button_hover}; }}
            #effectSegmentButton:checked {{ color: {palette.text}; background: {palette.surface}; border: 1px solid {palette.border}; }}
            #dropArea {{ background: {palette.surface}; border: none; border-radius: 18px; }}
            #dropArea:hover {{ background: {palette.selection}; }}
            #dropIcon {{ color: {palette.accent}; background: {palette.selection}; border-radius: 32px; padding: 0; }}
            #dropTitle {{ font-size: 14px; font-weight: 600; }}
            #codeCard {{ background: {palette.surface}; border: none; border-radius: 14px; }}
            #codeView {{ background: {palette.surface}; color: {palette.text}; border: none; border-radius: 9px; padding: 10px 12px; selection-background-color: {palette.selection}; font-family: '{preferred_code_font_family()}'; }}
            #codeView:focus {{ border: 1px solid {palette.accent}; }}
            #lineNumberArea {{ background: {palette.sidebar}; color: {palette.tertiary_text}; border-right: none; }}
            #codeView QScrollBar:vertical {{ background: transparent; width: 10px; margin: 3px 1px 3px 0; }}
            #codeView QScrollBar::handle:vertical {{ background: {palette.tertiary_text}; border-radius: 5px; min-height: 28px; }}
            #codeView QScrollBar::handle:vertical:hover {{ background: {palette.secondary_text}; }}
            #codeView QScrollBar::add-line:vertical, #codeView QScrollBar::sub-line:vertical {{ height: 0; }}
            #codeView QScrollBar::add-page:vertical, #codeView QScrollBar::sub-page:vertical {{ background: transparent; }}
            #codeView QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0 3px 1px 3px; }}
            #codeView QScrollBar::handle:horizontal {{ background: {palette.tertiary_text}; border-radius: 5px; min-width: 28px; }}
            #codeView QScrollBar::handle:horizontal:hover {{ background: {palette.secondary_text}; }}
            #codeView QScrollBar::add-line:horizontal, #codeView QScrollBar::sub-line:horizontal {{ width: 0; }}
            #codeView QScrollBar::add-page:horizontal, #codeView QScrollBar::sub-page:horizontal {{ background: transparent; }}
            #templateCodeView {{ background: #1E1E1E; color: #D4D4D4; border: 1px solid #3C3C3C; border-radius: 9px; padding: 10px 12px; selection-background-color: #264F78; font-family: '{preferred_code_font_family()}'; font-size: 10pt; }}
            #templateCodeView:focus {{ border: 1px solid #569CD6; }}
            #templateCodeView QScrollBar:vertical {{ background: #1E1E1E; width: 10px; margin: 3px 1px 3px 0; }}
            #templateCodeView QScrollBar::handle:vertical {{ background: #424242; border-radius: 5px; min-height: 28px; }}
            #templateCodeView QScrollBar::handle:vertical:hover {{ background: #5A5A5A; }}
            #templateCodeView QScrollBar::add-line:vertical, #templateCodeView QScrollBar::sub-line:vertical {{ height: 0; }}
            #templateCodeView QScrollBar::add-page:vertical, #templateCodeView QScrollBar::sub-page:vertical {{ background: transparent; }}
            #resizeGrip {{ background: transparent; }}
            #dangerButton {{ background: {palette.danger_soft}; color: {palette.danger}; border: none; border-radius: 10px; padding: 8px 16px; font-weight: 600; }}
            #dangerButton:hover {{ background: {palette.danger}; color: white; }}
            #dangerButton:disabled {{ color: {palette.tertiary_text}; background: transparent; }}
             #statusBar {{ background: transparent; border: none; border-bottom-left-radius: 20px; border-bottom-right-radius: 20px; }}
             #zoomLabel {{ color: {palette.tertiary_text}; font-size: 9px; padding: 0 2px; }}
             #helpButton {{ background: {palette.button}; color: {palette.secondary_text}; border: none; border-radius: 15px; font-size: 14px; font-weight: 700; padding: 0; }}
             #helpButton:hover {{ background: {palette.button_hover}; color: {palette.accent}; border-color: {palette.accent}; }}
             #usageGuide {{ background: {palette.surface}; }}
             #checkOverview {{ background: {palette.surface}; }}
             #dialogTitle {{ color: {palette.text}; font-size: 14px; font-weight: 700; }}
             #guideTitle {{ color: {palette.text}; font-size: 16px; font-weight: 700; }}
             #guideSearch {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 9px; padding: 7px 10px; min-height: 22px; }}
             #guideSearch:focus {{ background: {palette.surface}; }}
             #guideSections {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 10px; padding: 4px; outline: none; }}
             #guideSections::item {{ padding: 8px 9px; border-radius: 7px; }}
             #guideSections::item:hover {{ background: {palette.button_hover}; }}
             #guideSections::item:selected {{ background: {palette.selection}; color: {palette.accent}; font-weight: 600; }}
             #guideView {{ background: {palette.surface}; color: {palette.text}; border: none; border-radius: 10px; padding: 14px 16px; selection-background-color: {palette.selection}; }}
             #commandPalette {{ background: {glass_chrome}; }}
             #commandSearch {{ background: {palette.button}; color: {palette.text}; border: none; border-radius: 10px; padding: 9px 11px; min-height: 24px; font-size: 11pt; }}
             #commandSearch:focus {{ background: {palette.surface}; }}
             #commandList {{ background: {palette.surface}; color: {palette.text}; border: none; border-radius: 10px; padding: 5px; outline: none; }}
             #commandList::item {{ padding: 9px 10px; border-radius: 7px; }}
             #commandList::item:hover {{ background: {palette.button_hover}; }}
             #commandList::item:selected {{ background: {palette.selection}; color: {palette.accent}; font-weight: 600; }}
             QSplitter::handle {{ background: transparent; width: 8px; }}
        """)
        if hasattr(self, "canvas"):
            self.canvas.set_dark(self.dark_mode)
        if self._glass_ready and not self._glass_capture_suspended:
            _apply_native_glass(self, self.dark_mode, self.effect_mode)
        self._refresh_asset_icons(palette)
        self.diff_overview.set_dark(self.dark_mode)
        icon_name = "tabler-sun.svg" if self.dark_mode else "tabler-moon.svg"
        self.theme_button.setIcon(tinted_icon(ASSET_DIR / icon_name, palette.secondary_text))
        self.theme_button.setToolTip("切换到浅色模式" if self.dark_mode else "切换到深色模式")

    def closeEvent(self, event: QCloseEvent) -> None:
        # Mark the close request before waiting for worker threads.  Queued
        # glass callbacks can otherwise start another native capture while
        # Qt is tearing down the translucent top-level window.
        self._closing = True
        self._glass_capture_suspended = True
        if self._folder_scan_worker is not None and self._folder_scan_worker.isRunning():
            self._close_after_folder_scan = True
            self._discard_folder_scan_result = True
            self._folder_scan_pending.clear()
            self._folder_scan_worker.requestInterruption()
            event.ignore()
            return
        if self._check_worker is not None and self._check_worker.isRunning():
            self._discard_check_result = True
            self._restart_check_after_finish = False
            self._close_after_check = True
            self._check_worker.requestInterruption()
            event.ignore()
            return
        if self._preview_worker is not None and self._preview_worker.isRunning():
            self._close_after_preview = True
            self._preview_worker.requestInterruption()
            event.ignore()
            return
        if self._format_worker is not None and self._format_worker.isRunning():
            self._close_after_format = True
            self._format_worker.requestInterruption()
            event.ignore()
            return
        self._watch_reload_timer.stop()
        self._screen_glass_refresh_timer.stop()
        if self._screen_glass_backdrop is not None:
            self._screen_glass_backdrop.set_live(False)
            self._screen_glass_backdrop.cleanup()
        # Glass bars submit at most one latest-only NumPy task.  Clear queued
        # work and wait briefly for the active task before QObject destruction
        # so no worker can touch a widget that is already being deleted.
        self._glass_pool.clear()
        self._glass_pool.waitForDone(1000)
        if self._file_watcher.files():
            self._file_watcher.removePaths(self._file_watcher.files())
        self._save_ui_state()
        event.accept()


def run_gui() -> int:
    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("CCodeFormatter")
    app.setWindowIcon(QIcon(str(APP_ICON_PATH)))
    window = FormatterWindow()
    window.show()
    return app.exec()
