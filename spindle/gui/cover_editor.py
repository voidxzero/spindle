"""Cover art editor: crop/stretch/fit, rotate, colour adjustments, resize. Qt + numpy only."""
from __future__ import annotations

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap, QTransform
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QGridLayout, QHBoxLayout,
                               QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget)

HANDLE = 9          # handle size in screen pixels
MIN_CROP = 32       # smallest crop, in image pixels


def to_array(img: QImage):
    """QImage -> H x W x 4 uint8 array (B, G, R, A), a private copy."""
    import numpy as np
    img = img.convertToFormat(QImage.Format_ARGB32)
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    buf = np.frombuffer(img.constBits(), np.uint8, count=h * bpl).reshape(h, bpl)
    return buf[:, : w * 4].reshape(h, w, 4).copy()


def from_array(arr) -> QImage:
    import numpy as np
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[:2]
    return QImage(arr.data, w, h, w * 4, QImage.Format_ARGB32).copy()   # copy: detach from numpy


def apply_adjust(img: QImage, adj: dict) -> QImage:
    if not any(adj.values()):
        return img
    from ..imageadjust import adjust
    out = from_array(adjust(to_array(img), **adj))
    return out if img.hasAlphaChannel() else out.convertToFormat(QImage.Format_RGB32)


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


# output shapes
SHAPE_CROP, SHAPE_STRETCH, SHAPE_FIT, SHAPE_FREE = "crop", "stretch", "fit", "free"
SHAPES = [(SHAPE_CROP, "Crop to square"), (SHAPE_STRETCH, "Stretch to square"),
          (SHAPE_FIT, "Fit in square"), (SHAPE_FREE, "Keep shape")]
FILLS = [("blur", "Blurred picture"), ("edge", "Edge colour"), ("black", "Black"), ("white", "White")]


def edge_colour(img: QImage) -> QColor:
    """Average colour around the picture's border."""
    small = img.scaled(16, 16, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    rs = gs = bs = n = 0
    for x in range(16):
        for y in range(16):
            if x in (0, 15) or y in (0, 15):
                c = QColor(small.pixel(x, y))
                rs, gs, bs, n = rs + c.red(), gs + c.green(), bs + c.blue(), n + 1
    return QColor(rs // n, gs // n, bs // n)


def blurred(img: QImage, side: int) -> QImage:
    """A soft, slightly darkened square backdrop made from the picture itself."""
    cover = img.scaled(side, side, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    cover = cover.copy((cover.width() - side) // 2, (cover.height() - side) // 2, side, side)
    tiny = cover.scaled(12, 12, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)   # shrinking hard = blur
    soft = tiny.scaled(side, side, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    p = QPainter(soft)
    p.fillRect(soft.rect(), QColor(0, 0, 0, 70))
    p.end()
    return soft


def compose(img: QImage, crop: QRectF, shape: str, fill: str = "blur", max_px: int = 0) -> QImage:
    """Build the final cover from the selection: crop, stretch or fit it into a square."""
    region = img.copy(crop.toAlignedRect())
    w, h = region.width(), region.height()
    if shape == SHAPE_STRETCH and w != h:
        side = max(w, h)
        out = region.scaled(side, side, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    elif shape == SHAPE_FIT and w != h:
        side = max(w, h)
        if fill == "blur":
            out = blurred(region, side).convertToFormat(QImage.Format_RGB32)
        else:
            out = QImage(side, side, QImage.Format_RGB32)
            out.fill({"black": QColor(0, 0, 0), "white": QColor(255, 255, 255)}.get(fill) or edge_colour(region))
        p = QPainter(out)
        p.setRenderHint(QPainter.SmoothPixmapTransform)
        p.drawImage((side - w) // 2, (side - h) // 2, region)
        p.end()
    else:
        out = region
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
        self.disp = QImage()      # optional adjusted, downscaled copy that's drawn instead of img
        self.square = True
        self._drag = None        # (mode, start_pos_img, start_crop)

    # --- geometry -------------------------------------------------------------------------------
    def set_image(self, img: QImage):
        self.img = img
        self.disp = QImage()
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
        p.drawImage(target, self.img if self.disp.isNull() else self.disp)
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
        self.resize(900, 760)
        self.result_bytes: bytes | None = None
        img = QImage()
        img.loadFromData(QByteArray(data))
        self.original = img.convertToFormat(QImage.Format_ARGB32) if img.hasAlphaChannel() else \
            img.convertToFormat(QImage.Format_RGB32)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        body = QHBoxLayout()
        body.setSpacing(12)
        self.canvas = CropCanvas()
        self.canvas.set_image(self.original)
        body.addWidget(self.canvas, 1)

        side = QVBoxLayout()
        side.setSpacing(8)
        side.addWidget(QLabel("Result"))
        self.preview = QLabel()
        self.preview.setObjectName("cover")
        self.preview.setFixedSize(200, 200)
        self.preview.setAlignment(Qt.AlignCenter)
        side.addWidget(self.preview)
        self.info = QLabel()
        self.info.setObjectName("dim")
        self.info.setWordWrap(True)
        self.info.setFixedWidth(200)
        side.addWidget(self.info)
        side.addSpacing(8)
        side.addWidget(QLabel("Shape"))
        self.shape = QComboBox()
        for key, label in SHAPES:
            self.shape.addItem(label, key)
        self.shape.setToolTip("Crop: cut a square out of the picture\n"
                              "Stretch: squash the selection to fill a square\n"
                              "Fit: keep the whole selection, fill the leftover space\n"
                              "Keep shape: free crop, saved as it is")
        self.shape.currentIndexChanged.connect(self._shape_changed)
        side.addWidget(self.shape)
        self.fill_label = QLabel("Fill the space with")
        side.addWidget(self.fill_label)
        self.fill = QComboBox()
        for key, label in FILLS:
            self.fill.addItem(label, key)
        self.fill.currentIndexChanged.connect(self._update_info)
        side.addWidget(self.fill)
        side.addSpacing(8)
        side.addWidget(QLabel("Adjust"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        self.sliders: dict[str, QSlider] = {}
        self.slider_vals: dict[str, QLabel] = {}
        for row_i, (key, label, lo, hi) in enumerate((("brightness", "Brightness", -100, 100),
                                                      ("contrast", "Contrast", -100, 100),
                                                      ("saturation", "Saturation", -100, 100),
                                                      ("sharpen", "Sharpen", 0, 100))):
            name = QLabel(label)
            sl = QSlider(Qt.Horizontal)
            sl.setRange(lo, hi)
            sl.setValue(0)
            sl.setToolTip("Double-click the number to reset")
            val = QLabel("0")
            val.setObjectName("dim")
            val.setFixedWidth(30)
            val.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            val.mouseDoubleClickEvent = lambda _e, s=sl: s.setValue(0)
            sl.valueChanged.connect(lambda v, lab=val: lab.setText(f"{v:+d}" if v else "0"))
            sl.valueChanged.connect(self._adjust_changed)
            grid.addWidget(name, row_i * 2, 0, 1, 2)
            grid.addWidget(sl, row_i * 2 + 1, 0)
            grid.addWidget(val, row_i * 2 + 1, 1)
            self.sliders[key], self.slider_vals[key] = sl, val
        side.addLayout(grid)
        self.auto = QCheckBox("Auto levels")
        self.auto.setToolTip("Stretch a dull or faded scan to full contrast")
        self.auto.toggled.connect(self._adjust_changed)
        arow = QHBoxLayout()
        arow.addWidget(self.auto)
        reset_adj = QPushButton("Reset")
        reset_adj.setToolTip("Reset brightness, contrast, saturation, sharpen and auto levels")
        reset_adj.clicked.connect(self._reset_adjust)
        arow.addStretch(1)
        arow.addWidget(reset_adj)
        side.addLayout(arow)
        side.addSpacing(8)
        side.addWidget(QLabel("Resize to at most"))
        self.size = QSpinBox()
        self.size.setRange(0, 4000)
        self.size.setSingleStep(100)
        self.size.setSpecialValueText("original size")
        self.size.setSuffix(" px")
        self.size.setValue(max_px)
        self.size.valueChanged.connect(self._update_info)
        side.addWidget(self.size)
        side.addStretch(1)
        body.addLayout(side)
        lay.addLayout(body, 1)

        row = QHBoxLayout()
        row.setSpacing(8)
        for text, tip, slot in (("⟲ Rotate", "Rotate 90° left", lambda: self._rotate(-90)),
                                ("⟳ Rotate", "Rotate 90° right", lambda: self._rotate(90)),
                                ("Trim borders", "Remove plain borders around the picture", self._trim),
                                ("Select all", "Select the whole picture", self._select_all),
                                ("Reset", "Back to the original", self._reset)):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Use edited cover")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self._accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        # the preview is rebuilt shortly after the last change, so dragging stays smooth
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(60)
        self._preview_timer.timeout.connect(self._render_preview)
        self._display_timer = QTimer(self)
        self._display_timer.setSingleShot(True)
        self._display_timer.setInterval(40)
        self._display_timer.timeout.connect(self._render_display)
        self.canvas.changed.connect(self._update_info)
        self._shape_changed()

    # --- options ------------------------------------------------------------------------------------
    def _shape_key(self) -> str:
        return self.shape.currentData() or SHAPE_CROP

    def _shape_changed(self, *_):
        key = self._shape_key()
        fit = key == SHAPE_FIT
        self.fill.setVisible(fit)
        self.fill_label.setVisible(fit)
        square = key == SHAPE_CROP
        if square != self.canvas.square:
            self.canvas.square = square
            if square:
                self.canvas.reset_crop()
        if key in (SHAPE_STRETCH, SHAPE_FIT) and self.canvas.square is False and self._is_default_square():
            self._select_all()                 # stretch/fit start from the whole picture
        self._update_info()

    def _is_default_square(self) -> bool:
        c, img = self.canvas.crop, self.canvas.img
        s = min(img.width(), img.height())
        return abs(c.width() - s) < 1 and abs(c.height() - s) < 1

    def _select_all(self):
        img = self.canvas.img
        if self.canvas.square:
            s = min(img.width(), img.height())
            self.canvas.crop = QRectF((img.width() - s) / 2, (img.height() - s) / 2, s, s)
        else:
            self.canvas.crop = QRectF(0, 0, img.width(), img.height())
        self.canvas.update()
        self._update_info()

    def _rotate(self, deg):
        img = self.canvas.img.transformed(QTransform().rotate(deg), Qt.SmoothTransformation)
        self.canvas.set_image(img)
        self._display_timer.start()
        if not self.canvas.square:
            self._select_all()

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
        self._reset_adjust()
        if not self.canvas.square:
            self._select_all()

    # --- adjustments --------------------------------------------------------------------------------
    def _adjustments(self) -> dict:
        d = {k: s.value() for k, s in self.sliders.items()}
        d["auto"] = self.auto.isChecked()
        return d

    def _adjust_changed(self, *_):
        self._display_timer.start()
        self._preview_timer.start()

    def _reset_adjust(self):
        for s in self.sliders.values():
            s.blockSignals(True)
            s.setValue(0)
            s.blockSignals(False)
        for v in self.slider_vals.values():
            v.setText("0")
        self.auto.blockSignals(True)
        self.auto.setChecked(False)
        self.auto.blockSignals(False)
        self._adjust_changed()

    def _render_display(self):
        """Show the adjustments on the big picture, using a downscaled copy so sliders stay smooth."""
        adj = self._adjustments()
        img = self.canvas.img
        if img.isNull() or not any(adj.values()):
            self.canvas.disp = QImage()
        else:
            small = img if max(img.width(), img.height()) <= 1000 else \
                img.scaled(1000, 1000, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self.canvas.disp = apply_adjust(small, adj)
        self.canvas.update()

    # --- result -------------------------------------------------------------------------------------
    def _output_size(self) -> tuple[int, int]:
        c = self.canvas.crop
        w, h = int(round(c.width())), int(round(c.height()))
        if self._shape_key() in (SHAPE_STRETCH, SHAPE_FIT):
            w = h = max(w, h)
        m = self.size.value()
        if m and max(w, h) > m:
            f = m / max(w, h)
            w, h = int(round(w * f)), int(round(h * f))
        return w, h

    def _update_info(self, *_):
        ow, oh = self._output_size()
        self.info.setText(f"Original {self.original.width()}×{self.original.height()}\nResult {ow}×{oh}")
        self._preview_timer.start()

    def _render_preview(self):
        if self.canvas.img.isNull():
            return
        out = compose(self.canvas.img, self.canvas.crop, self._shape_key(), self.fill.currentData(), 360)
        out = apply_adjust(out, self._adjustments())
        pm = QPixmap.fromImage(out).scaled(194, 194, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.preview.setPixmap(pm)

    def _accept(self):
        out = compose(self.canvas.img, self.canvas.crop, self._shape_key(), self.fill.currentData(),
                      self.size.value())
        out = apply_adjust(out, self._adjustments())
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
