"""Custom widgets: progress-in-cell delegate, clickable cover, reorderable provider list."""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
                               QPushButton, QStyle, QStyledItemDelegate, QStyleOptionViewItem,
                               QVBoxLayout, QWidget)

PROGRESS_ROLE = Qt.UserRole + 1
STATE_ROLE = Qt.UserRole + 2     # "good" | "warn" | "bad" | ""


class StatusDelegate(QStyledItemDelegate):
    """Paints a slim LCD-style progress bar behind the status text while a track is busy."""

    def __init__(self, colors: dict, parent=None):
        super().__init__(parent)
        self.colors = colors

    def paint(self, painter: QPainter, option, index):
        frac = index.data(PROGRESS_ROLE)
        state = index.data(STATE_ROLE) or ""
        # let the style draw the cell background (alternating rows, selection) without text
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        opt.text = ""
        widget = opt.widget
        (widget.style() if widget else QApplication.style()).drawControl(
            QStyle.CE_ItemViewItem, opt, painter, widget)
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing)
        selected = bool(option.state & QStyle.State_Selected)
        text = index.data(Qt.DisplayRole) or ""
        busy = frac is not None and 0 <= frac < 1
        text_rect = option.rect.adjusted(8, 0, -4, 0)
        if busy:
            r = QRectF(option.rect.adjusted(4, 5, -4, -5))
            painter.setPen(QPen(QColor(self.colors["border"]), 1))
            painter.setBrush(QColor(self.colors["lcd_bg"]))
            painter.drawRoundedRect(r, 3, 3)
            fill = QRectF(r.x(), r.y(), r.width() * frac, r.height())
            g = QLinearGradient(0, r.top(), 0, r.bottom())
            g.setColorAt(0, QColor(self.colors["lcd_fg"]).lighter(115))
            g.setColorAt(1, QColor(self.colors["lcd_fg"]).darker(130))
            painter.setPen(Qt.NoPen)
            painter.setBrush(g)
            painter.drawRoundedRect(fill, 3, 3)
            painter.setPen(QColor("#ffffff") if frac > 0.4 else QColor(self.colors["text"]))
        elif state in ("good", "bad", "warn"):
            # finished: a round status badge (✓ / ✗ / !) before the text
            d = min(16, option.rect.height() - 8)
            badge = QRectF(option.rect.x() + 6, option.rect.center().y() - d / 2 + 1, d, d)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(self.colors[state]))
            painter.drawEllipse(badge)
            pen = QPen(QColor("#ffffff"), max(1.6, d / 7.5))
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            painter.setPen(pen)
            x, y = badge.x(), badge.y()
            if state == "good":
                painter.drawPolyline([QPointF(x + d * .27, y + d * .52), QPointF(x + d * .44, y + d * .69),
                                      QPointF(x + d * .74, y + d * .34)])
            elif state == "bad":
                painter.drawLine(QPointF(x + d * .32, y + d * .32), QPointF(x + d * .68, y + d * .68))
                painter.drawLine(QPointF(x + d * .68, y + d * .32), QPointF(x + d * .32, y + d * .68))
            else:
                painter.drawLine(QPointF(x + d / 2, y + d * .25), QPointF(x + d / 2, y + d * .56))
                painter.drawPoint(QPointF(x + d / 2, y + d * .74))
            text_rect = option.rect.adjusted(int(d) + 12, 0, -4, 0)
            painter.setPen(QColor(self.colors["sel_text"] if selected else self.colors["text"]))
        else:
            painter.setPen(QColor(self.colors["sel_text"] if selected else self.colors["text"]))
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, text)
        painter.restore()


class CoverLabel(QLabel):
    clicked = Signal()

    def __init__(self, size: int = 168, parent=None):
        super().__init__(parent)
        self.setObjectName("cover")
        self.side = size
        self.setFixedSize(size, size)
        self.setAlignment(Qt.AlignCenter)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Click to choose cover art")
        self.clear_cover()

    def clear_cover(self):
        self._data = None
        self.setPixmap(QPixmap())
        self.setText("No cover\n\nclick to search")

    def set_loading(self, on: bool, text: str = "Finding cover art…"):
        if not hasattr(self, "_spin"):
            self._spin = Spinner(30, self)
        self._spin.move((self.side - 30) // 2, (self.side - 30) // 2 - 14)
        if on:
            if self.pixmap() is None or self.pixmap().isNull():
                self.setText("\n\n\n" + text)
            self._spin.start()
            self._spin.raise_()
        else:
            self._spin.stop()
            if self.pixmap() is None or self.pixmap().isNull():
                self.clear_cover()

    def set_image(self, data: bytes) -> bool:
        pm = QPixmap()
        if not pm.loadFromData(data):
            return False
        self._data = data
        self.setPixmap(pm.scaled(self.side - 6, self.side - 6, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        return True

    def set_side(self, px: int):
        """Resize the square (e.g. to match the height of the fields beside it), keeping the image."""
        px = int(px)
        if px <= 0 or px == self.side:
            return
        self.side = px
        self.setFixedSize(px, px)
        data = getattr(self, "_data", None)
        if data and self.pixmap() is not None and not self.pixmap().isNull():
            self.set_image(data)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(e)


class ProviderList(QWidget):
    """Checkable, reorderable list (order = priority)."""

    def __init__(self, names: list[str], order: list[str], disabled: list[str], parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setDragDropMode(QListWidget.InternalMove)
        ordered = [n for n in order if n in names] + [n for n in names if n not in order]
        for n in ordered:
            it = QListWidgetItem(n)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsDragEnabled)
            it.setCheckState(Qt.Unchecked if n in disabled else Qt.Checked)
            self.list.addItem(it)
        lay.addWidget(self.list, 1)
        col = QVBoxLayout()
        up, down = QPushButton("▲ Up"), QPushButton("▼ Down")
        up.clicked.connect(lambda: self._move(-1))
        down.clicked.connect(lambda: self._move(1))
        col.addWidget(up)
        col.addWidget(down)
        col.addStretch(1)
        lay.addLayout(col)
        self.list.setMaximumHeight(130)

    def _move(self, d):
        row = self.list.currentRow()
        if row < 0 or not (0 <= row + d < self.list.count()):
            return
        it = self.list.takeItem(row)
        self.list.insertItem(row + d, it)
        self.list.setCurrentRow(row + d)

    def order(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def disabled(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())
                if self.list.item(i).checkState() != Qt.Checked]


class Spinner(QWidget):
    """Small rotating-dots activity indicator; uses the palette highlight colour."""

    def __init__(self, size: int = 18, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._timer = QTimer(self)
        self._timer.setInterval(80)
        self._timer.timeout.connect(self._tick)
        self.color: QColor | None = None
        self.hide()

    def start(self):
        self.show()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def _tick(self):
        self._angle = (self._angle + 1) % 12
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        base = self.color or self.palette().highlight().color()
        s = min(self.width(), self.height())
        r = s / 2
        dot = max(2.0, s / 7)
        p.translate(self.width() / 2, self.height() / 2)
        for i in range(12):
            c = QColor(base)
            c.setAlphaF(0.15 + 0.85 * (((i - self._angle) % 12) / 11.0))
            p.setBrush(c)
            p.setPen(Qt.NoPen)
            p.save()
            p.rotate(i * 30)
            p.drawEllipse(QRectF(-dot / 2, -r + 1, dot, dot))
            p.restore()
        p.end()


def unify_heights(widgets, reference=None) -> int:
    """Give every control in a form the same height so text fields, dropdowns, spin boxes and
    buttons line up in every theme. The height comes from ``reference`` (normally a text field)
    so rows stay compact; without one, the tallest natural height is used."""
    widgets = [w for w in widgets if w is not None]
    if not widgets:
        return 0
    for w in widgets:
        w.setMinimumHeight(0)
        w.setMaximumHeight(16777215)
        w.ensurePolished()
    if reference is not None:
        h = int(reference.sizeHint().height()) + 2
    else:
        h = max(int(w.sizeHint().height()) for w in widgets)
    h = max(h, 28)
    for w in widgets:
        w.setFixedHeight(h)
    return h


class LoadingOverlay(QWidget):
    """Dims its parent and shows a centred spinner card; swallows clicks while visible."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self._host = parent
        self.setAttribute(Qt.WA_StyledBackground, False)
        self.card = QWidget(self)
        self.card.setObjectName("loadingCard")
        lay = QHBoxLayout(self.card)
        lay.setContentsMargins(22, 16, 22, 16)
        lay.setSpacing(14)
        self.spin = Spinner(28, self.card)
        self.label = QLabel("", self.card)
        f = self.label.font()
        f.setPointSizeF(f.pointSizeF() * 1.15)
        self.label.setFont(f)
        lay.addWidget(self.spin)
        lay.addWidget(self.label)
        parent.installEventFilter(self)
        self.hide()

    def show_message(self, text: str):
        self.label.setText(text)
        self.setGeometry(self._host.rect())
        self.card.adjustSize()
        self._center()
        self.raise_()
        self.show()
        self.spin.start()

    def finish(self):
        self.spin.stop()
        self.hide()

    def _center(self):
        self.card.move(max(0, (self.width() - self.card.width()) // 2),
                       max(0, (self.height() - self.card.height()) // 2))

    def eventFilter(self, obj, ev):
        from PySide6.QtCore import QEvent
        if obj is self._host and ev.type() == QEvent.Resize and self.isVisible():
            self.setGeometry(self._host.rect())
            self._center()
        return False

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        dim = QColor(pal.window().color())
        dim.setAlpha(150)
        p.fillRect(self.rect(), dim)
        # the card: a rounded panel in the base colour with a subtle border
        r = QRectF(self.card.geometry()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(pal.mid().color(), 1))
        p.setBrush(pal.base().color())
        p.drawRoundedRect(r, 10, 10)
        p.end()

    def mousePressEvent(self, e):
        e.accept()                     # block clicks on the dimmed page


class Visualizer(QWidget):
    """Mini Winamp-style spectrum analyser: segmented bars with falling peak caps."""
    BANDS = 19

    def __init__(self, source, active=lambda: False, parent=None):
        """source() -> bytes of the PCM currently playing (b"" when nothing is);
        active() -> True while the player is playing (the analyser keeps running meanwhile)."""
        super().__init__(parent)
        self.active = active
        from .. import spectrum
        self._spectrum = spectrum
        self.source = source
        self.smooth = spectrum.Smoother(self.BANDS)
        self.setFixedSize(4 * self.BANDS + 2 * (self.BANDS - 1) + 6, 28)   # 4px bars, 2px gaps
        self.setToolTip("Spectrum analyser")
        self.bg = None
        self._timer = QTimer(self)
        self._timer.setInterval(33)                    # ~30 fps
        self._timer.timeout.connect(self._tick)

    def start(self):
        if not self._timer.isActive():
            self._timer.start()

    def _tick(self):
        pcm = self.source()
        target = self._spectrum.levels(pcm, self.BANDS) if pcm else None
        self.smooth.step(target)
        self.update()
        # only go quiet once playback has stopped/paused AND the bars have fallen to zero -
        # an empty frame while playing (e.g. the very first one) must not switch it off
        if target is None and self.smooth.idle and not self.active():
            self._timer.stop()

    def paintEvent(self, _):
        p = QPainter(self)
        w, h = self.width(), self.height()
        pal = self.palette()
        p.fillRect(self.rect(), self.bg or QColor("#0b0d10"))
        p.setPen(QPen(pal.mid().color(), 1))
        p.drawRect(0, 0, w - 1, h - 1)
        inner_h = h - 6
        seg = 2                                        # 2px segments with 1px gaps, like the original
        rows = inner_h // (seg + 1)
        green, yellow, red = QColor("#3fd94a"), QColor("#e8d33a"), QColor("#e5482f")
        bars, peaks = self.smooth.bars, self.smooth.peaks
        unlit = QColor(pal.mid().color())
        unlit.setAlpha(55)
        for i in range(self.BANDS):
            x = 3 + i * 6
            for r in range(rows):                      # faint unlit segments, so it reads as a meter
                p.fillRect(x, h - 3 - (r + 1) * (seg + 1) + 1, 4, seg, unlit)
            lit = int(round(bars[i] * rows))
            for r in range(lit):
                frac = r / max(1, rows - 1)
                col = green if frac < 0.6 else (yellow if frac < 0.85 else red)
                y = h - 3 - (r + 1) * (seg + 1) + 1
                p.fillRect(x, y, 4, seg, col)
            pk = int(round(peaks[i] * rows))
            if pk > 0:
                y = h - 3 - pk * (seg + 1) + 1
                p.fillRect(x, y, 4, 1, QColor("#d8d8d8"))
        p.end()
