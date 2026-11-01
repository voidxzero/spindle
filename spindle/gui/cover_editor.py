"""Cover art editor: crop (free or square), rotate, resize. Pure Qt - no extra libraries."""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QPushButton,
                               QSpinBox, QVBoxLayout, QWidget)

HANDLE = 9          # handle size in screen pixels
MIN_CROP = 32       # smallest crop, in image pixels


def image_to_bytes(img: QImage, quality: int = 92) -> bytes:
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    fmt = "PNG" if img.hasAlphaChannel() else "JPG"
    img.save(buf, fmt, quality if fmt == "JPG" else -1)
    return bytes(buf.data())


def crop_and_scale(img: QImage, crop: QRectF, max_px: int) -> QImage:
    out = img.copy(crop.toAlignedRect())
    if max_px and max(out.width(), out.height()) > max_px:
        out = out.scaled(max_px, max_px, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return out


def auto_trim_rect(img: QImage, tolerance: int = 18) -> QRectF:
    """Bounding box of the picture inside uniform borders (letterboxing, white scan margins)."""
    w, h = img.width(), img.height()
    if w < 8 or h < 8:
        return QRectF(0, 0, w, h)
    ref = QColor(img.pixel(0, 0))

    def same(x, y):
        c = QColor(img.pixel(x, y))
        return (abs(c.red() - ref.red()) + abs(c.green() - ref.green()) + abs(c.blue() - ref.blue())) <= tolerance

    step = max(1, min(w, h) // 200)

    def row_uniform(y):
        return all(same(x, y) for x in range(0, w, step))

    def col_uniform(x):
        return all(same(x, y) for y in range(0, h, step))
    top = 0
    while top < h - 1 and row_uniform(top):
        top += 1
    bottom = h - 1
    while bottom > top and row_uniform(bottom):
        bottom -= 1
    left = 0
    while left < w - 1 and col_uniform(left):
        left += 1
    right = w - 1
    while right > left and col_uniform(right):
        right -= 1
    if right - left < MIN_CROP or bottom - top < MIN_CROP:
        return QRectF(0, 0, w, h)
    return QRectF(left, top, right - left + 1, bottom - top + 1)


class CropCanvas(QWidget):
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(420, 420)
        self.setMouseTracking(True)
        self.img = QImage()
        self.crop = QRectF()
        self.square = True
        self._drag = None        # (mode, start_pos_img, start_crop)

    # --- geometry -------------------------------------------------------------------------------
    def set_image(self, img: QImage):
        self.img = img
        self.reset_crop()

    def reset_crop(self):
        w, h = self.img.width(), self.img.height()
        if self.square:
            s = min(w, h)
            self.crop = QRectF((w - s) / 2, (h - s) / 2, s, s)
        else:
            self.crop = QRectF(0, 0, w, h)
        self.update()
        self.changed.emit()

    def _view(self) -> tuple[float, QPointF]:
        """Scale and top-left offset of the image on screen."""
        if self.img.isNull():
            return 1.0, QPointF(0, 0)
        sc = min((self.width() - 20) / self.img.width(), (self.height() - 20) / self.img.height())
        sc = max(sc, 0.01)
        off = QPointF((self.width() - self.img.width() * sc) / 2, (self.height() - self.img.height() * sc) / 2)
        return sc, off

    def _to_screen(self, r: QRectF) -> QRectF:
        sc, off = self._view()
        return QRectF(off.x() + r.x() * sc, off.y() + r.y() * sc, r.width() * sc, r.height() * sc)

    def _to_img(self, p) -> QPointF:
        sc, off = self._view()
        return QPointF((p.x() - off.x()) / sc, (p.y() - off.y()) / sc)

    def _hit(self, pos) -> str:
        r = self._to_screen(self.crop)
        x, y, t = pos.x(), pos.y(), HANDLE
        near_l, near_r = abs(x - r.left()) <= t, abs(x - r.right()) <= t
        near_t, near_b = abs(y - r.top()) <= t, abs(y - r.bottom()) <= t
        inside_x, inside_y = r.left() - t <= x <= r.right() + t, r.top() - t <= y <= r.bottom() + t
        if near_t and near_l:
            return "tl"
        if near_t and near_r:
            return "tr"
        if near_b and near_l:
            return "bl"
        if near_b and near_r:
            return "br"
        if near_l and inside_y:
            return "l"
        if near_r and inside_y:
            return "r"
        if near_t and inside_x:
            return "t"
        if near_b and inside_x:
            return "b"
        if r.contains(QPointF(x, y)):
            return "move"
        return ""

    # --- mouse ----------------------------------------------------------------------------------
    def mousePressEvent(self, e):
        mode = self._hit(e.position())
        if mode:
            self._drag = (mode, self._to_img(e.position()), QRectF(self.crop))

    def mouseMoveEvent(self, e):
        if not self._drag:
            cursors = {"tl": Qt.SizeFDiagCursor, "br": Qt.SizeFDiagCursor, "tr": Qt.SizeBDiagCursor,
                       "bl": Qt.SizeBDiagCursor, "l": Qt.SizeHorCursor, "r": Qt.SizeHorCursor,
                       "t": Qt.SizeVerCursor, "b": Qt.SizeVerCursor, "move": Qt.SizeAllCursor}
            self.setCursor(cursors.get(self._hit(e.position()), Qt.ArrowCursor))
            return
        mode, start, c0 = self._drag
        p = self._to_img(e.position())
        dx, dy = p.x() - start.x(), p.y() - start.y()
        self.crop = self._apply(mode, c0, dx, dy)
        self.update()
        self.changed.emit()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def _apply(self, mode: str, c: QRectF, dx: float, dy: float) -> QRectF:
        W, H = self.img.width(), self.img.height()
        left, top, right, bottom = c.left(), c.top(), c.right(), c.bottom()
        if mode == "move":
            nx = min(max(0.0, left + dx), W - c.width())
            ny = min(max(0.0, top + dy), H - c.height())
            return QRectF(nx, ny, c.width(), c.height())
        if "l" in mode:
            left = min(max(0.0, left + dx), right - MIN_CROP)
        if "r" in mode:
            right = max(min(W, right + dx), left + MIN_CROP)
        if "t" in mode:
            top = min(max(0.0, top + dy), bottom - MIN_CROP)
        if "b" in mode:
            bottom = max(min(H, bottom + dy), top + MIN_CROP)
        r = QRectF(QPointF(left, top), QPointF(right, bottom))
        if self.square:
            # keep it square: size follows the dragged edge, anchored to the opposite side/corner
            s = max(r.width(), r.height()) if mode in ("tl", "tr", "bl", "br") else (
                r.width() if mode in ("l", "r") else r.height())
            ax = c.right() if "l" in mode else c.left()
            ay = c.bottom() if "t" in mode else c.top()
            if mode in ("t", "b"):
                ax = c.center().x() - s / 2
            if mode in ("l", "r"):
                ay = c.center().y() - s / 2
            x = ax - s if "l" in mode else ax
            y = ay - s if "t" in mode else ay
            # stay inside the image
            s = min(s, W, H)
            x = min(max(0.0, x), W - s)
            y = min(max(0.0, y), H - s)
            r = QRectF(x, y, s, s)
        return r

    # --- painting -------------------------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.fillRect(self.rect(), self.palette().base())
        if self.img.isNull():
            return
        sc, off = self._view()
        target = QRectF(off.x(), off.y(), self.img.width() * sc, self.img.height() * sc)
        p.drawImage(target, self.img)
        cr = self._to_screen(self.crop)
        shade = QPainterPath()
        shade.addRect(target)
        hole = QPainterPath()
        hole.addRect(cr)
        p.fillPath(shade.subtracted(hole), QColor(0, 0, 0, 140))
        p.setPen(QPen(QColor(255, 255, 255, 230), 1.5))
        p.drawRect(cr)
        p.setPen(QPen(QColor(255, 255, 255, 90), 1))          # rule-of-thirds guides
        for i in (1, 2):
            x = cr.left() + cr.width() * i / 3
            y = cr.top() + cr.height() * i / 3
            p.drawLine(QPointF(x, cr.top()), QPointF(x, cr.bottom()))
            p.drawLine(QPointF(cr.left(), y), QPointF(cr.right(), y))
        p.setPen(QPen(QColor(0, 0, 0, 160), 1))
        p.setBrush(QColor(255, 255, 255))
        for hx in (cr.left(), cr.center().x(), cr.right()):
            for hy in (cr.top(), cr.center().y(), cr.bottom()):
                if hx == cr.center().x() and hy == cr.center().y():
                    continue
                p.drawRect(QRectF(hx - 4, hy - 4, 8, 8))
        p.end()


class CoverEditor(QDialog):
    """Returns edited image bytes in .result_bytes when accepted."""

    def __init__(self, data: bytes, max_px: int = 0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Edit cover art")
        self.resize(640, 700)
        self.result_bytes: bytes | None = None
        img = QImage()
        img.loadFromData(QByteArray(data))
        self.original = img.convertToFormat(QImage.Format_ARGB32) if img.hasAlphaChannel() else \
            img.convertToFormat(QImage.Format_RGB32)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        self.canvas = CropCanvas()
        self.canvas.set_image(self.original)
        lay.addWidget(self.canvas, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.square = QCheckBox("Square")
        self.square.setChecked(True)
        self.square.setToolTip("Album covers are square - keep the crop square")
        self.square.toggled.connect(self._square_toggled)
        row.addWidget(self.square)
        for text, tip, slot in (("⟲ Rotate", "Rotate 90° left", lambda: self._rotate(-90)),
                                ("⟳ Rotate", "Rotate 90° right", lambda: self._rotate(90)),
                                ("Trim borders", "Remove plain borders around the picture", self._trim),
                                ("Reset", "Back to the original", self._reset)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(8)
        row2.addWidget(QLabel("Resize to at most"))
        self.size = QSpinBox()
        self.size.setRange(0, 4000)
        self.size.setSingleStep(100)
        self.size.setSpecialValueText("original size")
        self.size.setSuffix(" px")
        self.size.setValue(max_px)
        self.size.valueChanged.connect(self._update_info)
        row2.addWidget(self.size)
        row2.addStretch(1)
        self.info = QLabel()
        self.info.setObjectName("dim")
        row2.addWidget(self.info)
        lay.addLayout(row2)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Use edited cover")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.canvas.changed.connect(self._update_info)
        self._update_info()

    def _square_toggled(self, on):
        self.canvas.square = on
        self.canvas.reset_crop()

    def _rotate(self, deg):
        img = self.canvas.img.transformed(QTransform().rotate(deg), Qt.SmoothTransformation)
        self.canvas.set_image(img)

    def _trim(self):
        r = auto_trim_rect(self.canvas.img)
        if self.canvas.square:
            s = min(r.width(), r.height())
            r = QRectF(r.center().x() - s / 2, r.center().y() - s / 2, s, s)
        self.canvas.crop = r
        self.canvas.update()
        self._update_info()

    def _reset(self):
        self.canvas.set_image(self.original)

    def _update_info(self, *_):
        c = self.canvas.crop
        w, h = int(round(c.width())), int(round(c.height()))
        m = self.size.value()
        if m and max(w, h) > m:
            f = m / max(w, h)
            ow, oh = int(round(w * f)), int(round(h * f))
        else:
            ow, oh = w, h
        src = f"original {self.original.width()}×{self.original.height()}"
        self.info.setText(f"{src}  →  {ow}×{oh}")

    def _accept(self):
        out = crop_and_scale(self.canvas.img, self.canvas.crop, self.size.value())
        self.result_bytes = image_to_bytes(out)
        self.accept()


def edit_cover(parent, data: bytes, cfg=None):
    """Open the editor; returns a CoverCandidate with the edited image, or None if cancelled."""
    from .. import coverart
    from ..metadata.base import CoverCandidate
    max_px = 0
    dlg = CoverEditor(data, max_px, parent)
    if not dlg.exec() or not dlg.result_bytes:
        return None
    out = dlg.result_bytes
    w, h = coverart.image_size(out)
    return CoverCandidate(url="", source="Edited", data=out, width=w, height=h, description="Edited cover")
