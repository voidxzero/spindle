"""Spindle main window."""
from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QObject, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices, QKeySequence
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMenu, QMessageBox, QPlainTextEdit, QProgressBar,
                               QPushButton, QSizePolicy, QSlider, QSpinBox, QSplitter, QStyle,
                               QTableWidget, QTableWidgetItem, QTabWidget, QToolButton, QVBoxLayout,
                               QWidget)

from .. import __version__, coverart, encoders, metadata, net, system
from ..config import Config
from ..disc import CDS_DISC_OK, CDS_TRAY_OPEN, TOC, DriveError, drive_status, eject, list_drives
from ..metadata.base import AlbumMeta, CoverCandidate
from ..pipeline import RipJob, TrackResult
from ..ripper import Drive, SimulatedDrive, can_rip, make_drive
from .dialogs import CoverDialog, ReleaseDialog, SettingsDialog
from .player import CDPlayer
from .tagger_page import TaggerPage
from . import icons
from .themes import THEMES, colors, stylesheet, theme_names
from .widgets import (PROGRESS_ROLE, STATE_ROLE, CheckDelegate, CoverLabel, LoadingOverlay, SeekSlider,
                      Spinner, StatusDelegate, Visualizer, unify_heights)
from . import sounds
from .. import updater
from .update_ui import UpdateBar, UpdateDialog
from .workers import QtReporter, run_async


class _LogBridge(QObject):
    """Thread-safe logging: emit from any thread, delivered on the GUI thread."""
    message = Signal(str)


GENRES = ["", "Alternative", "Ambient", "Blues", "Classical", "Country", "Dance", "Electronic", "Folk",
          "Funk", "Hip-Hop", "Indie", "Jazz", "Latin", "Metal", "Pop", "Punk", "R&B", "Reggae", "Rock",
          "Soul", "Soundtrack", "World"]
C_CHECK, C_NUM, C_TITLE, C_ARTIST, C_LEN, C_STATUS, C_CRC, C_AR = range(8)


class MainWindow(QMainWindow):
    def __init__(self, simulate: bool = False):
        super().__init__()
        self.cfg = Config()
        net.contact = self.cfg["metadata"].get("contact", "")
        self.simulate = simulate
        self.toc: TOC | None = None
        self.drive: Drive | None = None
        self.album: AlbumMeta | None = None
        self.candidates: list[AlbumMeta] = []
        self.cover: CoverCandidate | None = None
        self.job: RipJob | None = None
        self.busy = False
        self._last_status = -1
        self._tray_open = False
        self._lookup_serial = 0
        self._auto_rip_pending = False
        self._countdown_left = 0
        self._counting = False
        self._countdown_toc = None
        self._countdown = QTimer(self)
        self._countdown.setInterval(1000)
        self._countdown.timeout.connect(lambda: self._tick_countdown())
        self._bridge = _LogBridge()
        self._mono_icons: list = []           # (widget, icon key, fallback) - recoloured on theme change
        self._update_info = None
        self.setObjectName("SpindleMain")
        self.setWindowTitle(f"Spindle {__version__}")
        self.resize(1180, 760)
        self._build()
        self.apply_theme(self.cfg["ui"]["theme"])
        geo = self.cfg["ui"].get("geometry")
        if geo:
            self.restoreGeometry(QByteArray.fromBase64(geo.encode()))
        self._populate_drives()
        self._check_tools()
        self._set_busy(False)
        if self.cfg["update"].get("check_on_start", True):
            QTimer.singleShot(2500, lambda: self.check_updates(manual=False))
        self.poll = QTimer(self)
        self.poll.timeout.connect(self._poll_drive)
        self.poll.start(2000)
        QTimer.singleShot(300, self._poll_drive)

    # ================================================================== build
    def _build(self):
        self.player = CDPlayer(self)
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        shell = QWidget()
        sv = QVBoxLayout(shell)
        sv.setContentsMargins(0, 0, 0, 0)
        sv.setSpacing(0)
        self.update_bar = UpdateBar()
        self.update_bar.details.connect(lambda: self._show_update(self._update_info))
        self.update_bar.skip.connect(self._skip_update)
        sv.addWidget(self.update_bar)
        sv.addWidget(self.tabs, 1)
        self.setCentralWidget(shell)
        central = QWidget()
        central.setObjectName("central")
        root = QHBoxLayout(central)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(10)
        root.addWidget(self._build_rail())
        main_area = QWidget()                  # the loading overlay covers this, never the rail (Settings)
        main = QVBoxLayout(main_area)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(8)
        root.addWidget(main_area, 1)
        main.addWidget(self._build_header())
        main.addWidget(self._build_player())
        split = QSplitter(Qt.Vertical)
        split.addWidget(self._build_table())
        split.addWidget(self._build_log())
        split.setStretchFactor(0, 4)
        split.setStretchFactor(1, 1)
        split.setSizes([460, 140])
        main.addWidget(split, 1)
        main.addWidget(self._build_progress())
        self.tabs.addTab(central, "Rip CD")
        self.rip_page = central
        self.overlay = LoadingOverlay(main_area)
        self.tagger = TaggerPage(self.cfg, self)
        self.tagger.logged.connect(self.log)
        self.tabs.addTab(self.tagger, "Tag Files")
        from .convert_page import ConvertPage
        self.converter = ConvertPage(self.cfg, colors(self.cfg["ui"]["theme"]), open_settings=self.settings, parent=self)
        self.converter.logged.connect(self.log)
        self.tabs.addTab(self.converter, "Convert")
        self._build_menu()
        self._build_status()
        self._bridge.message.connect(self.log)

    def _icon(self, names: tuple[str, ...], fallback):
        """Desktop theme icon (Breeze/Adwaita) when available, Qt's built-in one otherwise."""
        from PySide6.QtGui import QIcon
        for n in names:
            ic = QIcon.fromTheme(n)
            if not ic.isNull():
                return ic
        return self.style().standardIcon(fallback)

    def _mono(self, widget, key: str, fallback):
        """Give a button one of Spindle's monochrome line icons (recoloured on theme change)."""
        self._mono_icons.append((widget, key, fallback))
        self._apply_mono(widget, key, fallback)

    def _icon_colors(self):
        from PySide6.QtGui import QPalette
        theme = self.cfg["ui"]["theme"]
        c = colors(theme)
        if theme in THEMES:
            return c["text"], c.get("btn_bot", "#000000")
        pal = QApplication.palette()
        return pal.color(QPalette.ButtonText).name(), pal.color(QPalette.Button).name()

    def _apply_mono(self, widget, key, fallback):
        """Flat line icons for the System theme; glossy full-colour XP-style icons for the skins."""
        theme = self.cfg["ui"]["theme"]
        if theme in THEMES:
            ic = icons.make_glossy(key, colors(theme)["accent"])
        else:
            fg, bg = self._icon_colors()
            ic = icons.make(key, fg, bg)
        if ic is None:                          # no SVG support: use Qt's built-in icon
            ic = self.style().standardIcon(fallback)
        if hasattr(widget, "setIcon"):
            widget.setIcon(ic)
        else:
            widget.setPixmap(ic.pixmap(16, 16))

    def _refresh_icons(self):
        for w, key, fb in getattr(self, "_mono_icons", []):
            if w is self.pl_play:
                key = "pause" if self.player.state in ("playing", "buffering") else "play"
            elif w is getattr(self, "b_eject", None):
                key = "close" if self._tray_open else "eject"
            self._apply_mono(w, key, fb)

    def _rail_button(self, text, icon, slot, tip, accent=False):
        b = QToolButton()
        b.setObjectName("railButton")
        b.setText(text)
        self._mono(b, icon[0], icon[1])
        b.setIconSize(QSize(28, 28))
        b.setAutoRaise(True)
        b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        b.setToolTip(tip)
        b.setProperty("accent", "true" if accent else "false")
        b.clicked.connect(slot)
        return b

    def _build_rail(self):
        rail = QFrame()
        rail.setObjectName("rail")
        lay = QVBoxLayout(rail)
        lay.setContentsMargins(6, 8, 6, 8)
        lay.setSpacing(6)
        t = QLabel("SPINDLE")
        t.setObjectName("railTitle")
        t.setAlignment(Qt.AlignCenter)
        lay.addWidget(t)
        S = QStyle.StandardPixmap
        self.b_read = self._rail_button("Read Disc", ("disc", S.SP_DriveCDIcon),
                                        self.read_disc, "Read the table of contents (F5)")
        self.b_lookup = self._rail_button("Look Up", ("search", S.SP_FileDialogContentsView),
                                          self.lookup, "Look the disc up in MusicBrainz and other databases (F6)")
        self.b_cover = self._rail_button("Cover Art", ("image", S.SP_DesktopIcon),
                                         self.choose_cover, "Find or choose artwork (F7)")
        self.b_rip = self._rail_button("Rip", ("rip", S.SP_MediaPlay),
                                       lambda: self.rip(False), "Rip, encode and tag the ticked tracks (F8)", accent=True)
        self.b_image = self._rail_button("Image", ("imagefile", S.SP_DialogSaveButton),
                                         lambda: self.rip(True), "Rip the whole disc to a single file plus a cue sheet")
        self.b_eject = self._rail_button("Eject", ("eject", S.SP_ArrowUp), self.do_eject, "Eject (Ctrl+E)")
        self.b_settings = self._rail_button("Settings", ("settings", S.SP_FileDialogDetailedView),
                                            self.settings, "Settings (F9)")
        for b in (self.b_read, self.b_lookup, self.b_cover, self.b_image, self.b_rip):
            lay.addWidget(b)
        lay.addStretch(1)
        lay.addWidget(self.b_eject)
        lay.addWidget(self.b_settings)
        return rail

    def _build_header(self):
        hdr = QFrame()
        hdr.setObjectName("header")
        g = QGridLayout(hdr)
        g.setContentsMargins(12, 12, 12, 12)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        self.cover_lbl = CoverLabel(176)
        self.cover_lbl.clicked.connect(self.choose_cover)
        self.cover_lbl.setContextMenuPolicy(Qt.CustomContextMenu)
        self.cover_lbl.customContextMenuRequested.connect(self._cover_menu)
        g.addWidget(self.cover_lbl, 0, 0, 6, 1, Qt.AlignTop | Qt.AlignLeft)

        top = QHBoxLayout()
        top.setSpacing(8)
        self.drive_box = QComboBox()
        self.drive_box.setMinimumWidth(260)
        self.drive_box.currentIndexChanged.connect(self._drive_changed)
        top.addWidget(self.drive_box)
        self.lcd = QLabel("NO DISC")
        self.lcd.setObjectName("lcdBig")
        self.lcd.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.lcd.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        top.addWidget(self.lcd, 1)
        g.addLayout(top, 0, 1, 1, 4)

        def lab(t):
            x = QLabel(t)
            x.setObjectName("fieldLabel")
            return x
        self.f_artist = QLineEdit()
        self.f_album = QLineEdit()
        self.f_year = QLineEdit()
        self.f_year.setFixedWidth(120)
        self.f_year.setPlaceholderText("YYYY")
        self.f_genre = QComboBox()
        self.f_genre.setEditable(True)
        self.f_genre.addItems(GENRES)
        self.f_disc = QSpinBox()
        self.f_disc.setRange(1, 99)
        self.f_disc.setFixedWidth(64)
        self.f_discs = QSpinBox()
        self.f_discs.setRange(1, 99)
        self.f_discs.setFixedWidth(64)
        self.f_label = QLineEdit()
        self.f_catalog = QLineEdit()
        self.f_va = QCheckBox("Various artists")
        self.f_va.toggled.connect(lambda on: self.table.setColumnHidden(C_ARTIST, not on and not self._has_track_artists()))
        g.addWidget(lab("Album artist"), 1, 1)
        g.addWidget(self.f_artist, 1, 2)
        g.addWidget(lab("Year"), 1, 3)
        g.addWidget(self.f_year, 1, 4)
        g.addWidget(lab("Album"), 2, 1)
        g.addWidget(self.f_album, 2, 2)
        g.addWidget(lab("Genre"), 2, 3)
        g.addWidget(self.f_genre, 2, 4)
        g.addWidget(lab("Label / Cat#"), 3, 1)
        lc = QHBoxLayout()
        lc.setSpacing(8)
        lc.addWidget(self.f_label, 3)
        lc.addWidget(self.f_catalog, 2)
        g.addLayout(lc, 3, 2)
        g.addWidget(lab("Disc"), 3, 3)
        dl = QHBoxLayout()
        dl.setSpacing(8)
        dl.addWidget(self.f_disc)
        of = QLabel("of")
        of.setObjectName("fieldLabel")
        dl.addWidget(of)
        dl.addWidget(self.f_discs)
        dl.addStretch(1)
        g.addLayout(dl, 3, 4)

        g.addWidget(lab("Release"), 4, 1)
        rl = QHBoxLayout()
        rl.setSpacing(8)
        self.release_box = QComboBox()
        self.release_box.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.release_box.activated.connect(self._release_picked)
        rl.addWidget(self.release_box, 1)
        self.b_search = QPushButton("Search…")
        self.b_search.clicked.connect(self.search_dialog)
        rl.addWidget(self.b_search)
        rl.addSpacing(4)
        rl.addWidget(self.f_va)
        g.addLayout(rl, 4, 2, 1, 3)

        g.addWidget(lab("Save to"), 5, 1)
        ol = QHBoxLayout()
        ol.setSpacing(8)
        self.f_out = QLineEdit(self.cfg["output"]["directory"])
        self.f_out.editingFinished.connect(self._out_changed)
        ob = QToolButton()
        ob.setObjectName("iconButton")
        ob.setIconSize(QSize(16, 16))
        self._mono(ob, "folder", QStyle.StandardPixmap.SP_DirOpenIcon)
        ob.setToolTip("Choose folder")
        ob.clicked.connect(self._browse_out)
        self.b_browse = ob
        ol.addWidget(self.f_out, 1)
        ol.addWidget(ob)
        self.b_formats = QToolButton()
        self.b_formats.setObjectName("formatButton")
        self.b_formats.setPopupMode(QToolButton.InstantPopup)
        self.fmt_menu = QMenu(self)
        self.b_formats.setMenu(self.fmt_menu)
        self.fmt_menu.aboutToShow.connect(self._fill_format_menu)
        ol.addWidget(self.b_formats)
        g.addLayout(ol, 5, 2, 1, 3)
        g.setColumnStretch(2, 1)
        self._update_format_button()
        return hdr

    def _build_table(self):
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["", "#", "Title", "Artist", "Length", "Status", "CRC", "AccurateRip"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked)
        self.table.setShowGrid(False)
        self.table.verticalHeader().setDefaultSectionSize(26)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(C_TITLE, QHeaderView.Stretch)
        h.setSectionResizeMode(C_ARTIST, QHeaderView.Interactive)
        fm = self.table.fontMetrics()
        adv = fm.horizontalAdvance
        widths = ((C_CHECK, 34), (C_NUM, adv("000") + 24), (C_LEN, adv("00:00") + 26),
                  (C_STATUS, adv("CRC mismatch") + 48), (C_CRC, adv("DDDDDDDD") + 26),
                  (C_AR, adv("Not in database") + 30), (C_ARTIST, 200))
        for c, w in widths:
            self.table.setColumnWidth(c, w)
        h.setSectionResizeMode(C_CHECK, QHeaderView.Fixed)
        h.sectionClicked.connect(self._header_clicked)
        self.table.cellDoubleClicked.connect(self._row_double_clicked)
        self.table.cellClicked.connect(self._cell_clicked)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._track_menu)
        play_sel = QAction(self.table)
        play_sel.setShortcuts([QKeySequence(Qt.Key_Return), QKeySequence(Qt.Key_Enter)])
        play_sel.setShortcutContext(Qt.WidgetShortcut)
        play_sel.triggered.connect(self._play_selected)
        self.table.addAction(play_sel)
        self.delegate = StatusDelegate(colors(self.cfg["ui"]["theme"]), self.table)
        self.table.setItemDelegateForColumn(C_STATUS, self.delegate)
        self.check_delegate = CheckDelegate(None, self.table)
        self.table.setItemDelegateForColumn(C_CHECK, self.check_delegate)
        self.table.setColumnHidden(C_ARTIST, True)
        return self.table

    # ================================================================= player
    def _build_player(self):
        f = QFrame()
        f.setObjectName("panel")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(10, 6, 10, 6)
        lay.setSpacing(6)
        S = QStyle.StandardPixmap

        def tb(icon, tip, slot):
            b = QToolButton()
            b.setObjectName("transport")
            self._mono(b, icon[0], icon[1])
            b.setIconSize(QSize(16, 16))
            b.setToolTip(tip)
            b.setAutoRaise(False)
            b.clicked.connect(slot)
            lay.addWidget(b)
            return b
        self.pl_prev = tb(("prev", S.SP_MediaSkipBackward), "Previous track", self.player.previous)
        self.pl_play = tb(("play", S.SP_MediaPlay), "Play / pause (Space)", self._play_pause)
        self.pl_stop = tb(("stop", S.SP_MediaStop), "Stop", self.player.stop)
        self.pl_next = tb(("next", S.SP_MediaSkipForward), "Next track", self.player.next)
        lay.addSpacing(6)
        self.pl_label = QLabel("Not playing")
        self.pl_label.setFixedWidth(300)          # fixed, so the seek bar never changes size
        self._pl_base = "Not playing"
        lay.addWidget(self.pl_label)
        from ..spectrum import N as _FFT_N
        self.viz = Visualizer(lambda: self.player.now_playing_pcm(_FFT_N * 4),
                              active=lambda: self.player.state in ("playing", "buffering"))
        self.pl_seek = SeekSlider(lambda: self.player.length())
        self.pl_seek.setEnabled(False)
        self.pl_seek.setToolTip("Click anywhere to jump there, or drag")
        self.pl_seek.seekRequested.connect(lambda f: self.player.seek(f * self.player.length()))
        # elapsed | seek bar | total  (fixed widths, so the bar never changes size)
        self.pl_cur = QLabel("0:00")
        self.pl_total = QLabel("0:00")
        tw = self.pl_cur.fontMetrics().horizontalAdvance("00:00") + 8
        for lbl, align in ((self.pl_cur, Qt.AlignRight), (self.pl_total, Qt.AlignLeft)):
            lbl.setObjectName("dim")
            lbl.setFixedWidth(tw)
            lbl.setAlignment(align | Qt.AlignVCenter)
        lay.addWidget(self.pl_cur)
        lay.addWidget(self.pl_seek, 1)
        lay.addWidget(self.pl_total)
        vol_icon = QLabel()
        self._mono(vol_icon, "volume", S.SP_MediaVolume)
        lay.addSpacing(6)
        lay.addWidget(vol_icon)
        self.pl_vol = QSlider(Qt.Horizontal)
        self.pl_vol.setRange(0, 100)
        self.pl_vol.setValue(80)
        self.pl_vol.setFixedWidth(90)
        self.pl_vol.valueChanged.connect(lambda v: self.player.set_volume(v / 100))
        lay.addWidget(self.pl_vol)
        lay.addSpacing(10)
        lay.addWidget(self.viz)              # spectrum analyser at the far right of the player bar
        self.player.position.connect(self._pl_position)
        self.player.track_changed.connect(self._pl_track)
        self.player.state_changed.connect(self._pl_state)
        self.player.error.connect(lambda m: self.log(f"Playback: {m}"))
        sc = QAction(self)
        sc.setShortcut(QKeySequence(Qt.Key_Space))
        sc.setShortcutContext(Qt.WidgetWithChildrenShortcut)
        sc.triggered.connect(self._play_pause)
        f.addAction(sc)
        self._update_player_buttons()
        return f

    def _update_player_buttons(self):
        ok = self.player.available and self.toc is not None and not (self.busy and self.b_cancel.isEnabled()
                                                                      if hasattr(self, "b_cancel") else False)
        for b in (self.pl_prev, self.pl_play, self.pl_stop, self.pl_next):
            b.setEnabled(ok)
        if not self.player.available:
            self._set_pl_text("Playback unavailable (Qt Multimedia missing)")

    def _play_pause(self):
        if not self.toc:
            return
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        chosen = self._num(sel[0].row()) if sel else None
        if chosen is not None and chosen != self.player.track and self.player.state in ("stopped", "paused"):
            self.player.play(chosen)          # a different track is selected: play that one
        else:
            self.player.toggle(chosen or self.toc.audio_tracks[0].number)

    def _num(self, row: int) -> int:
        """Track number of a table row (the cell may be showing ▶ while that track plays)."""
        it = self.table.item(row, C_NUM)
        n = it.data(Qt.UserRole)
        return int(n) if n is not None else int(it.text())

    def _can_play(self) -> bool:
        return bool(self.toc) and self.player.available and not (self.busy and self.b_cancel.isEnabled())

    def _row_double_clicked(self, row, col):
        if self._can_play():
            self.player.play(self._num(row))

    def _cell_clicked(self, row, col):
        if col == C_NUM and self._can_play():
            self.player.play(self._num(row))

    def _play_selected(self):
        sel = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if sel and self._can_play():
            self.player.play(self._num(sel[0].row()))

    def _track_menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0 or not self.toc:
            return
        n = self._num(row)
        m = QMenu(self)
        a_play = m.addAction(f"Play track {n}")
        a_play.setEnabled(self._can_play())
        a_rip = m.addAction(f"Rip only track {n}")
        a_rip.setEnabled(not self.busy)
        a_retry = None
        if n in self._failed_tracks():
            a_retry = m.addAction(f"Retry track {n}")
            a_retry.setEnabled(not self.busy)
        chosen = m.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is a_play:
            self.player.play(n)
        elif chosen is not None and chosen is a_retry:
            self.retry_failed([n])
        elif chosen is a_rip:
            for r in range(self.table.rowCount()):
                self.table.item(r, C_CHECK).setCheckState(Qt.Checked if r == row else Qt.Unchecked)
            self.rip(False)

    def _mark_playing(self, n: int | None):
        """▶ in the number column and bold text for the playing track."""
        from PySide6.QtGui import QFont
        for r in range(self.table.rowCount()):
            it = self.table.item(r, C_NUM)
            if it is None:
                continue
            num = self._num(r)
            playing = n is not None and num == n
            it.setText("▶" if playing else f"{num:02d}")
            f = QFont(self.table.font())
            f.setBold(playing)
            for c in (C_NUM, C_TITLE, C_ARTIST, C_LEN):
                cell = self.table.item(r, c)
                if cell is not None:
                    cell.setFont(f)

    def _set_pl_text(self, text: str, note: str = ""):
        """Fit the now-playing text into the fixed-width label, trimming the title with an ellipsis."""
        fm = self.pl_label.fontMetrics()
        suffix = f"  ({note})" if note else ""
        room = self.pl_label.width() - fm.horizontalAdvance(suffix) - 4
        self.pl_label.setText(fm.elidedText(text, Qt.ElideRight, max(40, room)) + suffix)

    def _pl_position(self, pos, length):
        if not self.pl_seek.isSliderDown():
            self.pl_seek.setValue(int(pos / length * 1000) if length else 0)
        fmt = lambda x: f"{int(x // 60)}:{int(x % 60):02d}"  # noqa: E731
        self.pl_cur.setText(fmt(pos))
        self.pl_total.setText(fmt(length))

    def _pl_track(self, n):
        r = self._row_of(n)
        title = self.table.item(r, C_TITLE).text() if r >= 0 else ""
        self._pl_base = f"{n:02d} · {title}"
        self._set_pl_text(self._pl_base)
        self.pl_label.setToolTip(title)
        self._mark_playing(n)
        if r >= 0:
            self.table.scrollToItem(self.table.item(r, C_NUM))

    def _pl_state(self, state):
        S = QStyle.StandardPixmap
        playing = state in ("playing", "buffering")
        self.viz.start()       # runs while music plays, then settles to zero by itself
        self._apply_mono(self.pl_play, "pause" if playing else "play",
                         S.SP_MediaPause if playing else S.SP_MediaPlay)
        self.pl_seek.setEnabled(state != "stopped")
        if state == "stopped":
            self._mark_playing(None)
            self._pl_base = "Not playing"
            self._set_pl_text(self._pl_base)
        elif state == "buffering":
            self._set_pl_text(self._pl_base, "spinning up…")
        elif state == "playing":
            self._set_pl_text(self._pl_base)

    def _build_log(self):
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("log")
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(5000)
        return self.log_view

    def _build_progress(self):
        f = QFrame()
        f.setObjectName("panel")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(8, 6, 8, 6)
        self.spinner = Spinner(18)
        self.prog_label = QLabel("Ready")
        self.prog_label.setObjectName("lcd")
        self.prog_label.setMinimumWidth(320)
        lay.addWidget(self.spinner)
        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setFormat("%p%")
        self.b_cancel = QPushButton("Cancel")
        self.b_cancel.setEnabled(False)
        self.b_cancel.clicked.connect(self.cancel)
        self.b_open = QPushButton("Open folder")
        self.b_open.setToolTip("Open the folder of the current or last rip (or your music folder)")
        self.b_open.clicked.connect(self._open_output)
        self.b_retry = QPushButton("Retry failed")
        self.b_retry.setToolTip("Rip the failed tracks again. Their new files replace the old ones, and the "
                                "log, cue sheet and playlist are rewritten for the whole album.")
        self.b_retry.clicked.connect(lambda: self.retry_failed())
        self.b_retry.hide()
        lay.addWidget(self.prog_label)
        lay.addWidget(self.prog, 1)
        lay.addWidget(self.b_cancel)
        lay.addWidget(self.b_retry)
        lay.addWidget(self.b_open)
        return f

    def _build_menu(self):
        mb = self.menuBar()

        def act(menu, text, slot, key=None):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
            a.triggered.connect(slot)
            menu.addAction(a)
            return a
        m = mb.addMenu("&Disc")
        act(m, "Read disc", self.read_disc, "F5")
        self.a_eject = act(m, "Close tray" if self._tray_open else "Eject", self.do_eject, "Ctrl+E")
        m.addSeparator()
        act(m, "Quit", self.close, "Ctrl+Q")
        m = mb.addMenu("&Metadata")
        act(m, "Look up disc", self.lookup, "F6")
        act(m, "Search by artist / album…", self.search_dialog, "Ctrl+F")
        act(m, "Clear metadata", self._clear_meta)
        m.addSeparator()
        act(m, "Copy MusicBrainz disc ID", self._copy_discid)
        act(m, "Submit disc ID to MusicBrainz…", self._submit_mb)
        m = mb.addMenu("&Cover")
        act(m, "Choose cover art…", self.choose_cover, "F7")
        act(m, "Load from file…", self._cover_from_file)
        act(m, "Edit / crop cover…", self.edit_cover)
        act(m, "Remove cover", self._remove_cover)
        m = mb.addMenu("&Action")
        act(m, "Rip selected tracks", lambda: self.rip(False), "F8")
        act(m, "Rip image + CUE", lambda: self.rip(True), "Shift+F8")
        act(m, "Cancel", self.cancel, "Esc")
        m.addSeparator()
        act(m, "Select all tracks", lambda: self._check_all(True), "Ctrl+A")
        act(m, "Select no tracks", lambda: self._check_all(False), "Ctrl+Shift+A")
        m = mb.addMenu("&Options")
        act(m, "Settings…", self.settings, "F9")
        sk = m.addMenu("Skin")
        grp = QActionGroup(self)
        for name in theme_names():
            a = QAction(name, self, checkable=True)
            a.setChecked(name == self.cfg["ui"]["theme"])
            a.triggered.connect(lambda _=False, n=name: self._set_theme(n))
            grp.addAction(a)
            sk.addAction(a)
        m = mb.addMenu("&Help")
        act(m, "Check for updates…", lambda: self.check_updates(manual=True))
        act(m, "Open log folder", self._open_log_folder)
        act(m, "About Spindle", self._about)

    def _build_status(self):
        sb = self.statusBar()
        self.st_drive = QLabel()
        self.st_offset = QLabel()
        self.st_id = QLabel()
        self.st_id.setTextInteractionFlags(Qt.TextSelectableByMouse)
        sb.addWidget(self.st_drive)
        sb.addWidget(self.st_offset)
        sb.addPermanentWidget(self.st_id)

    # ================================================================ helpers
    def apply_theme(self, name):
        QApplication.instance().setStyleSheet(stylesheet(name))
        if hasattr(self, "check_delegate"):
            self.check_delegate.skin = THEMES.get(name)          # None -> native checkboxes
        if hasattr(self, "pl_play"):
            self._refresh_icons()
        self._unify()
        QTimer.singleShot(0, self._unify)      # once more after Qt finishes re-polishing
        self.delegate.colors = colors(name)
        self.table.viewport().update()
        if hasattr(self, "tagger"):
            self.tagger.set_colors(colors(name))
        if hasattr(self, "converter"):
            self.converter.set_colors(colors(name))
        if hasattr(self, "viz"):
            from PySide6.QtGui import QColor
            self.viz.bg = QColor(colors(name)["lcd_bg"]) if name in THEMES else None   # skins: LCD colour
            self.viz.skin = THEMES.get(name)
            self.viz.update()

    def _unify(self):
        """Line up the header's controls: every field, dropdown, spin box and button gets one height."""
        h = unify_heights([self.drive_box, self.lcd, self.f_artist, self.f_album, self.f_year, self.f_genre,
                           self.f_disc, self.f_discs, self.f_label, self.f_catalog, self.release_box,
                           self.b_search, self.f_out, self.b_browse, self.b_formats], reference=self.f_artist)
        if h:
            self.b_browse.setFixedWidth(h)
            # the cover fills the header: the drive row plus five field rows, with the gaps between
            self.cover_lbl.set_side(6 * h + 5 * 8)
        if hasattr(self, "tagger"):
            self.tagger.unify()
        if hasattr(self, "converter"):
            self.converter.unify()

    def changeEvent(self, e):
        # the desktop switched light/dark: refresh colours derived from the palette
        from PySide6.QtCore import QEvent
        if e.type() == QEvent.PaletteChange and hasattr(self, "delegate"):
            self.delegate.colors = colors(self.cfg["ui"]["theme"])
            if hasattr(self, "pl_play"):
                self._refresh_icons()
            if hasattr(self, "tagger"):
                self.tagger.set_colors(self.delegate.colors)
            if hasattr(self, "converter"):
                self.converter.set_colors(self.delegate.colors)
        super().changeEvent(e)

    def _working(self, text: str, block: bool = False):
        """Show activity. block=True greys out the page with a centred message until _idle()."""
        self.prog_label.setText(text)
        self.spinner.start()
        if block:
            self.overlay.show_message(text)

    def _idle(self, text: str = "Ready"):
        self.prog_label.setText(text)
        self.spinner.stop()
        self.overlay.finish()

    def _set_theme(self, name):
        self.cfg["ui"]["theme"] = name
        self.cfg.save()
        self.apply_theme(name)

    def log(self, msg: str):
        self.log_view.appendPlainText(msg)

    def _check_tools(self):
        if not self.simulate and not can_rip():
            self.log("⚠ cdparanoia not found - install the 'cdparanoia' package to rip.")
        avail = encoders.availability()
        missing = [encoders.FORMATS[f].label for f in self.cfg["output"]["formats"] if not avail.get(f)]
        if missing:
            self.log("⚠ No encoder for: " + ", ".join(missing) + " (install it or ffmpeg)")
        for e in metadata.plugin_errors:
            self.log(f"⚠ plugin error: {e}")
        try:
            import mutagen  # noqa: F401
        except ImportError:
            self.log("⚠ mutagen (python3-mutagen) is missing - files can't be tagged.")

    def _set_busy(self, busy: bool, ripping: bool = False):
        self.busy = busy
        for b in (self.b_read, self.b_lookup, self.b_cover, self.b_rip, self.b_image, self.b_eject,
                  self.drive_box, self.b_search, self.release_box):
            b.setEnabled(not busy)
        self.b_cancel.setEnabled(ripping)
        self._lock_edits(ripping)
        if hasattr(self, "b_retry"):
            self._update_retry_button()
        if hasattr(self, "pl_play"):
            self._update_player_buttons()
        if not busy and self.toc is None:
            for b in (self.b_lookup, self.b_cover, self.b_rip, self.b_image):
                b.setEnabled(False)

    def _lock_edits(self, locked: bool):
        """While ripping, the tags were already captured for the job - don't let them be edited."""
        if getattr(self, "_edits_locked", False) == locked:
            return
        self._edits_locked = locked
        for w in (self.f_artist, self.f_album, self.f_year, self.f_genre, self.f_disc, self.f_discs,
                  self.f_label, self.f_catalog, self.f_va, self.f_out, self.b_formats, self.cover_lbl):
            w.setEnabled(not locked)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers if locked else
                                   QAbstractItemView.EditKeyPressed | QAbstractItemView.SelectedClicked)
        for r in range(self.table.rowCount()):
            chk = self.table.item(r, C_CHECK)
            if chk is not None:
                chk.setFlags(Qt.ItemIsEnabled if locked else Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
        self.cover_lbl.setToolTip("Locked while ripping" if locked else "Click to choose cover art")

    def _populate_drives(self):
        self.drive_box.blockSignals(True)
        self.drive_box.clear()
        self._drive_infos = list_drives()
        if self.simulate:
            self.drive_box.addItem("SPINDLE Virtual CD-ROM (simulated)", "simulated")
        for d in self._drive_infos:
            self.drive_box.addItem(f"{d.name}  [{d.device}]", d.device)
        if not self.drive_box.count():
            self.drive_box.addItem("No optical drive found", "")
        i = self.drive_box.findData(self.cfg["drive"]["device"])
        self.drive_box.setCurrentIndex(max(0, i))
        self.drive_box.blockSignals(False)
        self._drive_changed()

    def _drive_info(self):
        dev = self.drive_box.currentData()
        return next((d for d in self._drive_infos if d.device == dev), None)

    def _drive_changed(self, *_):
        dev = self.drive_box.currentData()
        info = self._drive_info()
        if dev == "simulated":
            self.drive = SimulatedDrive(flaky_track=3)
        elif dev:
            self.drive = make_drive(dev, info.name if info else dev)
            if self.cfg["drive"].get("device") != dev and len(self._drive_infos) > 1:
                self.cfg["drive"]["device"] = dev      # remember the user's pick
                self.cfg.save()
            # recall the offset saved for this drive model
            known = self.cfg["drive"].get("offsets_by_model", {})
            if info and info.accuraterip_name in known:
                self.cfg["drive"]["read_offset"] = known[info.accuraterip_name]
        else:
            self.drive = None
        self._last_status = -1
        self._set_tray_open(False)
        self._update_status_bar()
        if info and info.model and info.accuraterip_name not in self.cfg["drive"].get("offsets_by_model", {}):
            self.log(f"Tip: set the read offset for {info.name} in Settings ▸ Drive (\"Look up in AccurateRip\").")

    def _update_status_bar(self):
        self.st_drive.setText(self.drive.name if self.drive else "No drive")
        self.st_offset.setText(f"Read offset {int(self.cfg['drive']['read_offset']):+d}")
        self.st_id.setText(f"MB disc ID {self.toc.musicbrainz_id()}" if self.toc else "")

    def _update_format_button(self):
        fm = self.cfg["output"]["formats"]
        txt = " + ".join(encoders.FORMATS[f].label.split(" (")[0] for f in fm if f in encoders.FORMATS) or "No format"
        self.b_formats.setText(f"Format: {txt}")

    def _fill_format_menu(self):
        self.fmt_menu.clear()
        avail = encoders.availability()
        for key, f in encoders.FORMATS.items():
            a = QAction(f"{f.label}" + ("" if avail[key] else "  (not installed)"), self.fmt_menu, checkable=True)
            a.setChecked(key in self.cfg["output"]["formats"])
            a.setEnabled(bool(avail[key]))
            a.toggled.connect(lambda on, k=key: self._toggle_format(k, on))
            self.fmt_menu.addAction(a)
        self.fmt_menu.addSeparator()
        self.fmt_menu.addAction("Encoder options…", lambda: self.settings(tab=2))

    def _toggle_format(self, key, on):
        fm = self.cfg["output"]["formats"]
        if on and key not in fm:
            fm.append(key)
        elif not on and key in fm:
            if len(fm) == 1:
                return
            fm.remove(key)
        self.cfg.save()
        self._update_format_button()

    def _out_changed(self):
        self.cfg["output"]["directory"] = self.f_out.text().strip()
        self.cfg.save()

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Save music to", os.path.expanduser(self.f_out.text()))
        if d:
            self.f_out.setText(d)
            self._out_changed()

    # ================================================================== drive
    def _poll_drive(self):
        if self.busy or not self.drive:
            return
        if isinstance(self.drive, SimulatedDrive):
            st = CDS_DISC_OK
        else:
            st = drive_status(self.drive.device)
        if not (system.WINDOWS and st != CDS_DISC_OK):
            # Windows can't tell an open tray from an empty one: keep what our own Eject/Close did
            self._set_tray_open(st == CDS_TRAY_OPEN)
        if st == self._last_status:
            return
        prev, self._last_status = self._last_status, st
        if st == CDS_DISC_OK and prev != CDS_DISC_OK and self.cfg["drive"]["auto_read"]:
            # only a freshly inserted disc may auto-rip - never one you re-read by hand
            self._auto_rip_pending = bool(self.cfg["drive"].get("auto_rip")) and prev != -1
            self.read_disc()
        elif st in (CDS_TRAY_OPEN, 1) and self.toc is not None:
            self._no_disc("TRAY OPEN" if st == CDS_TRAY_OPEN else "NO DISC")

    def _no_disc(self, text="NO DISC"):
        """Disc gone: clear everything that belonged to it (details, tracks, cover, now playing).
        The log keeps the record, and Open folder still points at the last rip."""
        self.player.set_disc(None, None)
        self.toc = None
        self._lookup_serial += 1                   # drop any lookup still in flight for the old disc
        self._auto_rip_pending = False
        if self._counting:
            self._stop_countdown("Auto-rip cancelled - the disc was removed")
        self._clear_disc_fields()
        self.lcd.setText(text)
        self._update_status_bar()
        self._set_busy(False)
        self._update_player_buttons()

    def _clear_disc_fields(self):
        self.album = None
        self.candidates = []
        self.release_box.clear()
        for w in (self.f_artist, self.f_album, self.f_year, self.f_label, self.f_catalog):
            w.clear()
        self.f_genre.setCurrentText("")
        self.f_disc.setValue(1)
        self.f_discs.setValue(1)
        self.f_va.setChecked(False)
        self.table.setRowCount(0)
        self.cover_lbl.set_loading(False)
        self._remove_cover()
        self._mark_playing(None)
        self._pl_base = "Not playing"
        self._set_pl_text(self._pl_base)
        self.pl_seek.setValue(0)
        self.pl_seek.setEnabled(False)
        self.pl_cur.setText("0:00")
        self.pl_total.setText("0:00")

    def read_disc(self):
        if not self.drive or self.busy:
            return
        self.player.stop()
        self._set_busy(True)
        self.lcd.setText("READING TOC…")
        self._working("Reading the disc…", block=True)

        def done(toc):
            self._set_busy(False)
            self._show_toc(toc)
            self.lookup()            # keeps the overlay up, just with a new message

        def fail(msg):
            self._set_busy(False)
            self._idle()
            self._no_disc("NO DISC")
            self.log(msg)
        run_async(self.drive.read_toc, on_done=done, on_error=fail)

    def _show_toc(self, toc: TOC):
        self.toc = toc
        self.player.set_disc(self.drive, toc)
        self.candidates = []
        self.release_box.clear()
        self.cover = None
        self.cover_lbl.clear_cover()
        mins = toc.total_seconds
        self.lcd.setText(f"{len(toc.audio_tracks):02d} TRACKS   {int(mins // 60):02d}:{int(mins % 60):02d}"
                         + ("   +DATA" if len(toc.audio_tracks) < len(toc.tracks) else ""))
        self.table.setRowCount(0)
        self.table.setRowCount(len(toc.audio_tracks))
        for r, t in enumerate(toc.audio_tracks):
            chk = QTableWidgetItem()
            chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            chk.setCheckState(Qt.Checked)
            self.table.setItem(r, C_CHECK, chk)
            num = self._ro(r, C_NUM, f"{t.number:02d}", Qt.AlignCenter)
            num.setData(Qt.UserRole, t.number)
            num.setToolTip("Click to play")
            self.table.setItem(r, C_TITLE, QTableWidgetItem(f"Track {t.number:02d}"))
            self.table.setItem(r, C_ARTIST, QTableWidgetItem(""))
            self._ro(r, C_LEN, t.length_str, Qt.AlignRight | Qt.AlignVCenter)
            self._ro(r, C_STATUS, "")
            self._ro(r, C_CRC, "")
            self._ro(r, C_AR, "")
        self.album = AlbumMeta.blank(toc)
        self.album.mb_disc_id = toc.musicbrainz_id()
        self._fill_fields(self.album)
        self._update_status_bar()
        self.log(f"Disc: {len(toc.audio_tracks)} audio tracks, MusicBrainz ID {toc.musicbrainz_id()}, "
                 f"FreeDB {toc.freedb_id():08x}" + (f", MCN {toc.mcn}" if toc.mcn else ""))
        self.prog.setValue(0)
        self.prog_label.setText("Ready")
        self._update_player_buttons()

    def _ro(self, r, c, text, align=None):
        it = QTableWidgetItem(text)
        it.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        if align is not None:
            it.setTextAlignment(align)
        self.table.setItem(r, c, it)
        return it

    def _row_of(self, n: int) -> int:
        for r in range(self.table.rowCount()):
            if self._num(r) == n:
                return r
        return -1

    def do_eject(self):
        """The Eject button: opens the tray, or closes it when it's already open."""
        self._eject(close=self._tray_open)

    def _eject(self, close=False):
        if not self.drive or isinstance(self.drive, SimulatedDrive):
            return
        try:
            eject(self.drive.device, close)
        except DriveError as e:
            self.log(str(e))
            return
        self._set_tray_open(not close)          # the next drive poll confirms (or corrects) this
        if not close:
            self._no_disc("TRAY OPEN")

    def _set_tray_open(self, is_open: bool):
        """Eject turns into Close while the tray is open."""
        if is_open == self._tray_open or not hasattr(self, "b_eject"):
            self._tray_open = is_open
            return
        self._tray_open = is_open
        self.b_eject.setText("Close" if is_open else "Eject")
        self.b_eject.setToolTip("Close the tray (Ctrl+E)" if is_open else "Eject (Ctrl+E)")
        if hasattr(self, "a_eject"):
            self.a_eject.setText("Close tray" if is_open else "Eject")
        self._refresh_icons()

    # =============================================================== metadata
    def lookup(self):
        if not self.toc or self.busy:
            return
        self._lookup_serial += 1
        serial = self._lookup_serial
        self._set_busy(True)
        self._working("Searching MusicBrainz and other databases…", block=True)
        toc = self.toc

        def done(results):
            if serial != self._lookup_serial:
                return
            self._set_busy(False)
            self.candidates = results
            if not results or not (len(results) == 1 and self.cfg["metadata"]["auto_pick_single"]):
                self._idle()         # a dialog follows; otherwise the details step keeps the overlay
            self._fill_release_box()
            if not results:
                self.log("No metadata found for this disc. Try Metadata ▸ Search, or type the details in.")
                if self._auto_rip_pending:
                    self._auto_rip_pending = False
                    self._notify_auto_rip_skipped("no matching release was found")
                    return
                self.search_dialog()
            elif self._auto_rip_pending and len(results) > 1:
                self._working("Loading release details…", block=True)
                self.log(f"Auto-rip: {len(results)} releases match - using the best one "
                         f"({results[0].summary() or results[0].source}). Change it in Release if needed.")
                self._apply_candidate(results[0])
            elif len(results) == 1 and self.cfg["metadata"]["auto_pick_single"]:
                self._apply_candidate(results[0])
            else:
                dlg = ReleaseDialog(self.cfg, toc, results, self)
                if dlg.exec() and dlg.selected():
                    self._merge_candidates(dlg.candidates)
                    self._apply_candidate(dlg.selected())
                else:
                    self._apply_candidate(results[0])

        def fail(msg):
            self._set_busy(False)
            self._idle()
            self.log(f"Lookup failed: {msg}")
            if self._auto_rip_pending:
                self._auto_rip_pending = False
                self._notify_auto_rip_skipped("the metadata lookup failed")
        run_async(metadata.lookup_all, self.cfg, toc, self._log_from_thread, on_done=done, on_error=fail)

    def _log_from_thread(self, msg):
        # metadata providers log from worker threads; the signal marshals to the GUI thread
        self._bridge.message.emit(msg)

    def _merge_candidates(self, cands):
        for c in cands:
            if c not in self.candidates:
                self.candidates.append(c)
        self._fill_release_box()

    def _fill_release_box(self):
        self.release_box.clear()
        for a in self.candidates:
            self.release_box.addItem(f"{a.artist} – {a.title}   ({a.summary() or a.source}) · {a.source}")
        if not self.candidates:
            self.release_box.addItem("(no matches)")

    def _release_picked(self, i):
        if 0 <= i < len(self.candidates):
            self._apply_candidate(self.candidates[i])

    def search_dialog(self):
        if not self.toc:
            return
        a = self._collect()
        dlg = ReleaseDialog(self.cfg, self.toc, self.candidates, self,
                            artist="" if a.artist == "Unknown Artist" else a.artist,
                            album="" if a.title == "Unknown Album" else a.title)
        if dlg.exec() and dlg.selected():
            self._merge_candidates(dlg.candidates)
            self._apply_candidate(dlg.selected())

    def _apply_candidate(self, cand: AlbumMeta):
        idx = self.candidates.index(cand) if cand in self.candidates else -1
        if idx >= 0:
            self.release_box.setCurrentIndex(idx)
        self._fill_fields(cand)
        self.log(f"Using {cand.source}: {cand.artist} – {cand.title} {cand.summary()}")
        toc = self.toc
        self._set_busy(True)
        self._working("Loading release details…", block=True)

        def done(full: AlbumMeta):
            self._set_busy(False)
            self._idle()
            if self.toc is not toc:
                return
            full.mb_disc_id = full.mb_disc_id or toc.musicbrainz_id()
            self._fill_fields(full)
            then = self._begin_auto_rip_countdown if self._auto_rip_pending else None
            self._auto_rip_pending = False
            if self.cfg["coverart"]["auto_fetch"] and self.cover is None:
                self._auto_cover(then=then)
            elif then:
                then()

        def fail(msg):
            self._set_busy(False)
            self._idle()
            self.log(f"Couldn't fetch details: {msg}")
            if self._auto_rip_pending:
                self._auto_rip_pending = False
                self._begin_auto_rip_countdown()      # basic details are already filled in
        run_async(metadata.complete, self.cfg, cand, toc, on_done=done, on_error=fail)

    def _fill_fields(self, a: AlbumMeta):
        self.album = a
        self.f_artist.setText(a.artist)
        self.f_album.setText(a.title + (f" ({a.disc_subtitle})" if a.disc_subtitle and a.disc_subtitle not in a.title else ""))
        self.f_year.setText(a.date or a.original_date)
        self.f_genre.setCurrentText(a.genre)
        self.f_disc.setValue(max(1, a.disc_number))
        self.f_discs.setValue(max(1, a.disc_total))
        self.f_label.setText(a.label)
        self.f_catalog.setText(a.catalog)
        self.f_va.setChecked(a.various)
        for r in range(self.table.rowCount()):
            n = self._num(r)
            t = a.track(n)
            self.table.item(r, C_TITLE).setText(t.title or f"Track {n:02d}")
            self.table.item(r, C_ARTIST).setText(t.artist if t.artist != a.artist else "")
        self.table.setColumnHidden(C_ARTIST, not (a.various or self._has_track_artists()))

    def _has_track_artists(self):
        return any(self.table.item(r, C_ARTIST) and self.table.item(r, C_ARTIST).text()
                   for r in range(self.table.rowCount()))

    def _collect(self) -> AlbumMeta:
        """Read the (possibly hand-edited) fields back into an AlbumMeta."""
        a = AlbumMeta.from_dict(self.album.to_dict()) if self.album else AlbumMeta.blank(self.toc)
        a.artist = self.f_artist.text().strip()
        a.title = self.f_album.text().strip()
        if a.disc_subtitle and a.title.endswith(f"({a.disc_subtitle})"):
            a.title = a.title[: -len(a.disc_subtitle) - 2].strip()
        yr = self.f_year.text().strip()
        if yr != (a.date or a.original_date):
            a.date = yr
        a.genre = self.f_genre.currentText().strip()
        a.disc_number, a.disc_total = self.f_disc.value(), self.f_discs.value()
        a.label, a.catalog = self.f_label.text().strip(), self.f_catalog.text().strip()
        a.compilation = self.f_va.isChecked()
        if a.compilation and not a.artist:
            a.artist = "Various Artists"
        for r in range(self.table.rowCount()):
            n = self._num(r)
            t = a.track(n)
            t.title = self.table.item(r, C_TITLE).text().strip()
            t.artist = self.table.item(r, C_ARTIST).text().strip() or a.artist
        a.mb_disc_id = a.mb_disc_id or (self.toc.musicbrainz_id() if self.toc else "")
        return a

    def _clear_meta(self):
        if self.toc:
            self.album = AlbumMeta.blank(self.toc)
            self.album.mb_disc_id = self.toc.musicbrainz_id()
            self._fill_fields(self.album)

    def _copy_discid(self):
        if self.toc:
            QApplication.clipboard().setText(self.toc.musicbrainz_id())

    def _submit_mb(self):
        if self.toc:
            url = (f"https://musicbrainz.org/cdtoc/attach?id={self.toc.musicbrainz_id()}"
                   f"&tracks={len(self.toc.audio_tracks)}&toc={self.toc.musicbrainz_toc().replace(' ', '+')}")
            QDesktopServices.openUrl(QUrl(url))

    # ================================================================== cover
    def _auto_cover(self, then=None):
        album = self._collect()
        toc = self.toc
        self._working("Finding cover art…")
        self.cover_lbl.set_loading(True)

        def done(c):
            self._idle()
            self.cover_lbl.set_loading(False)
            if self.toc is not toc:
                return                             # the disc changed while we were searching
            if c:
                self._set_cover(c)
            else:
                self.log("No cover art found automatically. Click the cover box to search or load a file.")
            if then:
                then()

        def fail(m):
            self._idle()
            self.cover_lbl.set_loading(False)
            self.log(f"Cover search failed: {m}")
            if then and self.toc is toc:
                then()
        run_async(coverart.auto_pick, self.cfg, album, self._log_from_thread, on_done=done, on_error=fail)

    def _set_cover(self, c: CoverCandidate):
        self.cover = c
        if c.data and self.cover_lbl.set_image(c.data):
            self.cover_lbl.setToolTip(f"{c.source} · {c.width}×{c.height}\nClick to change")

    def choose_cover(self):
        if not self.toc:
            return
        dlg = CoverDialog(self.cfg, self._collect(), self)
        if dlg.exec() and dlg.result_cover:
            self._set_cover(dlg.result_cover)

    def _cover_from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose cover image", os.path.expanduser("~"),
                                              "Images (*.jpg *.jpeg *.png)")
        if path:
            try:
                self._set_cover(coverart.from_file(path))
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, "Cover art", str(e))

    def edit_cover(self):
        if not self.cover or not self.cover.data:
            QMessageBox.information(self, "Cover art", "Choose a cover first.")
            return
        from .cover_editor import edit_cover
        edited = edit_cover(self, self.cover.data, self.cfg)
        if edited:
            self._set_cover(edited)

    def _cover_menu(self, pos):
        if not self.toc or getattr(self, "_edits_locked", False):
            return
        m = QMenu(self)
        m.addAction("Find cover art…", self.choose_cover)
        a = m.addAction("Edit / crop…", self.edit_cover)
        a.setEnabled(bool(self.cover and self.cover.data))
        m.addAction("Load from file…", self._cover_from_file)
        r = m.addAction("Remove", self._remove_cover)
        r.setEnabled(self.cover is not None)
        m.exec(self.cover_lbl.mapToGlobal(pos))

    def _remove_cover(self):
        self.cover = None
        self.cover_lbl.clear_cover()

    # ==================================================================== rip
    def _check_all(self, on: bool):
        for r in range(self.table.rowCount()):
            self.table.item(r, C_CHECK).setCheckState(Qt.Checked if on else Qt.Unchecked)

    def _header_clicked(self, col):
        if col == C_CHECK and self.table.rowCount():
            anyoff = any(self.table.item(r, C_CHECK).checkState() != Qt.Checked for r in range(self.table.rowCount()))
            self._check_all(anyoff)

    def rip(self, image: bool, tracks: list[int] | None = None, retry_of: RipJob | None = None):
        if self._counting:
            self._stop_countdown()
        if not self.toc or self.busy or not self.drive:
            return
        if tracks is None:
            tracks = [self._num(r) for r in range(self.table.rowCount())
                      if self.table.item(r, C_CHECK).checkState() == Qt.Checked]
        if not tracks and not image:
            QMessageBox.information(self, "Rip", "Tick at least one track.")
            return
        self.player.stop()          # the drive can't play and rip at the same time
        if not self.simulate and not can_rip():
            QMessageBox.critical(self, "Rip", "cdparanoia isn't installed.\n\nInstall it with your package "
                                 "manager (e.g. sudo apt install cdparanoia) and try again.")
            return
        album = self._collect()
        if album.title in ("", "Unknown Album") and QMessageBox.question(
                self, "Rip", "The album has no title yet. Rip anyway?") != QMessageBox.Yes:
            return
        self._out_changed()
        import copy
        cfg = Config.__new__(Config)              # private copy so the rip isn't affected by later edits
        dict.__init__(cfg, copy.deepcopy(dict(self.cfg)))
        cfg.path = self.cfg.path
        if image:
            cfg["output"]["mode"] = "image"
            tracks = [t.number for t in self.toc.audio_tracks]
        else:
            cfg["output"]["mode"] = "tracks"
        if self.cover is not None:
            cfg["coverart"]["auto_fetch"] = False
        for n in tracks:
            r = self._row_of(n)
            for c in (C_STATUS, C_CRC, C_AR):
                self.table.item(r, c).setText("")
            self.table.item(r, C_STATUS).setData(STATE_ROLE, "")
            self.table.item(r, C_STATUS).setText("Queued")
        self.rep = QtReporter()
        self.rep.logged.connect(self.log)
        self.rep.status.connect(self._on_status)
        self.rep.progress.connect(self._on_track_progress)
        self.rep.overall_progress.connect(self._on_overall)
        self.rep.finished_track.connect(self._on_track_done)
        self.job = RipJob(cfg, self.drive, self.toc, album, tracks, self.cover, self.rep, retry_of=retry_of)
        self._set_busy(True, ripping=True)
        self.prog.setValue(0)
        self.prog_label.setText("Ripping…")
        self.lcd.setText("RIPPING")
        run_async(self.job.run, on_done=self._rip_done, on_error=self._rip_failed)

    def cancel(self):
        if self._counting:
            self._stop_countdown("Auto-rip cancelled - press Rip when you're ready")
            return
        if self.job and self.busy:
            self.job.cancel.set()
            self.prog_label.setText("Cancelling…")

    # ------------------------------------------------------------- auto-rip
    AUTO_RIP_DELAY = 5

    def _begin_auto_rip_countdown(self):
        if not self.toc or self.busy:
            return
        self._counting = True
        self._countdown_left = self.AUTO_RIP_DELAY
        self._countdown_toc = self.toc
        self.b_cancel.setEnabled(True)
        self.b_cancel.setText("Cancel auto-rip")
        self.lcd.setText("AUTO-RIP")
        self._tick_countdown()
        self._countdown.start()

    def _tick_countdown(self):
        if not self._counting:
            return
        if self.busy:                                  # you started something yourself - step aside quietly
            self._stop_countdown()
            return
        if self.toc is not self._countdown_toc:        # disc was ejected or changed
            self._stop_countdown("Auto-rip cancelled")
            return
        if self._countdown_left <= 0:
            self._stop_countdown()
            self.log("Auto-rip: starting")
            self.rip(False)
            return
        self.prog_label.setText(f"Auto-rip starts in {self._countdown_left} s…  (Cancel to stop)")
        self._countdown_left -= 1

    def _stop_countdown(self, message: str = ""):
        self._countdown.stop()
        self._counting = False
        self._countdown_left = 0
        self.b_cancel.setText("Cancel")
        if not self.busy:
            self.b_cancel.setEnabled(False)
        if message:
            self.prog_label.setText(message)
            if self.toc:
                mins = self.toc.total_seconds
                self.lcd.setText(f"{len(self.toc.audio_tracks):02d} TRACKS   {int(mins // 60):02d}:{int(mins % 60):02d}")

    def _notify_auto_rip_skipped(self, why: str):
        self.log(f"Auto-rip skipped: {why}. Fill in the details and press Rip.")
        self.prog_label.setText("Auto-rip skipped - check the details")
        QApplication.alert(self)

    @staticmethod
    def _short_status(text: str) -> str:
        """Keep the Status column compact; the details live in the log and the AccurateRip column."""
        if (text.startswith(("Accurate", "Copy OK")) or text in ("Copy finished", "Not in database",
                                                                 "AccurateRip unreachable")):
            return "Success"
        if text.startswith("CRC mismatch"):
            return "CRC mismatch"
        if "suspicious" in text:
            return "Suspicious"
        return text

    # ---------------------------------------------------------------- retry
    def _failed_tracks(self) -> list[int]:
        """Tracks of the last rip of the disc in the drive that didn't come out right."""
        job = self.job
        if not job or self.toc is None or job.toc.musicbrainz_id() != self.toc.musicbrainz_id():
            return []
        return [n for n in job.track_order if n in job.results and not job.results[n].good]

    def _update_retry_button(self):
        failed = [] if self.busy else self._failed_tracks()
        self.b_retry.setText(f"Retry failed ({len(failed)})" if len(failed) > 1 else "Retry failed")
        self.b_retry.setVisible(bool(failed))

    def retry_failed(self, tracks: list[int] | None = None):
        failed = self._failed_tracks()
        tracks = [n for n in (tracks or failed) if n in failed]
        if not tracks or self.busy:
            return
        prev = self.job
        image = prev.cfg["output"].get("mode") == "image"
        self.log(("Retrying the disc image" if image else
                  f"Retrying track{'s' if len(tracks) > 1 else ''} {', '.join(map(str, tracks))}") + "…")
        self.rip(image, tracks=None if image else tracks, retry_of=prev)

    def _on_status(self, n, text):
        r = self._row_of(n)
        if r < 0:
            return
        it = self.table.item(r, C_STATUS)
        it.setToolTip(text)
        text = self._short_status(text)
        if text == "Success" and it.data(STATE_ROLE) == "warn":
            it.setText("Not verified")     # consistent copy, but AccurateRip has a different checksum
            return
        it.setText(text)
        if text.startswith(("Copying", "Testing", "Re-reading", "Encoding")):
            it.setData(STATE_ROLE, "")
        elif text == "Success":
            it.setData(STATE_ROLE, "good")
            it.setData(PROGRESS_ROLE, None)
        elif "mismatch" in text.lower() or "fail" in text.lower():
            it.setData(STATE_ROLE, "bad")
            it.setData(PROGRESS_ROLE, None)
        if text in ("Copying", "Testing") or text.startswith("Re-reading"):
            self.table.scrollToItem(it)

    def _on_track_progress(self, n, frac):
        r = self._row_of(n)
        if r < 0:
            return
        it = self.table.item(r, C_STATUS)
        it.setData(PROGRESS_ROLE, frac)
        base = it.text().split(" ")[0] if it.text() else "Copying"
        if base in ("Copying", "Testing", "Re-reading"):
            it.setText(f"{base} {int(frac * 100)}%")

    def _on_overall(self, frac, text):
        self.prog.setValue(int(frac * 1000))
        if text:
            self.prog_label.setText(text)

    def _on_track_done(self, n, res: TrackResult):
        r = self._row_of(n)
        if r < 0:
            return
        self.table.item(r, C_STATUS).setData(PROGRESS_ROLE, None)
        self.table.item(r, C_CRC).setText(f"{res.copy_crc:08X}" if res.copy_crc else "")
        if res.test_crc is not None and res.test_crc != res.copy_crc:
            self.table.item(r, C_CRC).setText(f"{res.copy_crc:08X} ≠ {res.test_crc:08X}")
        ar = self.table.item(r, C_AR)
        if res.ar:
            short = {"accurate": f"Accurate ({res.ar.confidence})", "mismatch": "Not accurate",
                     "absent": "Not in database", "unavailable": "Unreachable"}.get(res.ar.status, res.ar.text())
            ar.setText(short)
            ar.setToolTip(res.ar.text() + f"\nARv1 {res.ar_v1:08X}  ARv2 {res.ar_v2:08X}")
            ar.setForeground(self._color("good" if res.ar.status == "accurate" else
                                         "warn" if res.ar.status in ("absent", "unavailable") else "bad"))
        st = self.table.item(r, C_STATUS)
        # green ✓ when good; amber ! when the copy is consistent but AccurateRip disagrees
        state = "bad" if not res.good else ("warn" if res.ar and res.ar.status == "mismatch" else "good")
        st.setData(STATE_ROLE, state)
        if state == "warn":
            st.setText("Not verified")
        elif state == "good" and not st.text().startswith("Encoding"):
            st.setText("Success")

    def _color(self, key):
        from PySide6.QtGui import QBrush, QColor
        return QBrush(QColor(colors(self.cfg["ui"]["theme"])[key]))

    def _rip_done(self, ok: bool):
        job = self.job
        self._set_busy(False)
        self.lcd.setText("DONE" if ok else ("CANCELLED" if job.cancel.is_set() else "DONE - CHECK LOG"))
        good = sum(1 for r in job.results.values() if r.good)
        acc = sum(1 for r in job.results.values() if r.ar and r.ar.status == "accurate")
        if not job.cancel.is_set():
            self.prog_label.setText(f"{good}/{len(job.results)} OK · {acc} AccurateRip verified")
            self._finish_sound(problem=not ok or bool(job.errors) or good < len(job.results))
            if job.errors:
                QMessageBox.warning(self, "Rip finished with problems", "\n".join(job.errors[:12]))
            if ok and self.cfg["drive"]["eject_when_done"]:
                self._eject(False)
        QApplication.alert(self)

    def _rip_failed(self, msg):
        self._set_busy(False)
        self.lcd.setText("ERROR")
        self.prog_label.setText("Rip failed")
        self.log(f"Rip failed: {msg}")
        self._finish_sound(problem=True)
        QMessageBox.critical(self, "Rip failed", msg)

    def _finish_sound(self, problem: bool):
        snd = self.cfg["sound"]
        try:
            sounds.play(sounds.resolve(snd, problem), int(snd.get("volume", 80)) / 100)
        except Exception as e:                  # a missing sound must never spoil a finished rip
            self.log(f"Couldn't play the finish sound: {e}")

    def _open_output(self):
        """During a rip, the album folder it's writing to; afterwards, the last rip's folder; before any
        rip, the music folder. Folders that don't exist yet fall back to the nearest one that does."""
        job = self.job
        if job and self.busy and self.b_cancel.isEnabled() and job.formats and job.track_order:
            n = None if job.cfg["output"].get("mode") == "image" else job.track_order[0]
            target = os.path.dirname(job._path_for(job.formats[0], n))
        elif job and job.output_dir:
            target = job.output_dir
        else:
            target = os.path.expanduser(self.cfg["output"]["directory"] or "~/Music")
        while not os.path.isdir(target) and os.path.dirname(target) != target:
            target = os.path.dirname(target)
        if os.path.isdir(target):
            QDesktopServices.openUrl(QUrl.fromLocalFile(target))

    # =============================================================== settings
    def settings(self, tab: int = 0):
        self.player.stop()                      # key-disc detection needs the drive
        dlg = SettingsDialog(self.cfg, self._drive_info(), self, key_disc=lambda: (self.drive, self.toc),
                             ripping=lambda: self.busy and self.b_cancel.isEnabled())
        if isinstance(tab, str):
            tab = next((i for i in range(dlg.tabs.count()) if dlg.tabs.tabText(i) == tab), 0)
        if isinstance(tab, int):
            dlg.tabs.setCurrentIndex(tab)
        if dlg.exec():
            self.f_out.setText(self.cfg["output"]["directory"])
            self._update_format_button()
            self._update_status_bar()
            self.log("Settings saved.")
        else:
            self.cfg.__init__(self.cfg.path)   # discard any half-applied changes
        self.apply_theme(self.cfg["ui"]["theme"])

    # ================================================================ updates
    def check_updates(self, manual: bool = False):
        repo = updater.repo_for(self.cfg)
        if not repo:
            if manual:
                if QMessageBox.question(
                        self, "Check for updates",
                        "Spindle doesn't know which GitHub repository to update from.\n\n"
                        "Set it in Settings ▸ Updates (for example  yourname/spindle)?") == QMessageBox.Yes:
                    self.settings("Updates")
            return
        if manual:
            self._working("Checking for updates…")

        def done(info):
            if manual:
                self._idle()
            self._update_info = info
            if not info.available:
                if manual:
                    QMessageBox.information(self, "Check for updates",
                                            f"You have the latest version (Spindle {info.current}).")
                return
            self.log(f"Spindle {info.latest} is available (you have {info.current}).")
            if manual:
                self._show_update(info)
            elif info.latest != self.cfg["update"].get("skipped"):
                self.update_bar.show_for(info)

        def fail(msg):
            if manual:
                self._idle()
                QMessageBox.warning(self, "Check for updates", f"Couldn't check for updates: {msg}")
            else:
                self.log(f"Update check skipped: {msg}")
        run_async(updater.check, repo, on_done=done, on_error=fail)

    def _show_update(self, info):
        if info is None:
            return
        self.update_bar.hide()
        UpdateDialog(info, busy=lambda: self.busy, parent=self).exec()

    def _skip_update(self):
        if self._update_info:
            self.cfg["update"]["skipped"] = self._update_info.latest
            self.cfg.save()
            self.log(f"Skipping Spindle {self._update_info.latest}; Help ▸ Check for updates still offers it.")
        self.update_bar.hide()

    def _open_log_folder(self):
        from .. import crashlog
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(crashlog.log_dir())))

    def _about(self):
        QMessageBox.about(self, "About Spindle",
                          f"<h3>Spindle {__version__}</h3><p>Secure CD ripping (cdparanoia extraction on Linux) "
                          "with AccurateRip verification and test &amp; copy, MusicBrainz / GnuDB / plugin metadata, "
                          "automatic cover art, and FLAC, ALAC, WavPack, MP3, Opus, Vorbis and AAC output.</p>"
                          "<p>Metadata © MusicBrainz contributors (CC0). Verification data from AccurateRip.</p>")

    def closeEvent(self, e):
        if self.busy and self.job and not self.job.cancel.is_set() and self.b_cancel.isEnabled():
            if QMessageBox.question(self, "Quit", "A rip is in progress. Cancel it and quit?") != QMessageBox.Yes:
                e.ignore()
                return
            self.job.cancel.set()
        conv = getattr(self, "converter", None)
        if conv is not None and conv.job is not None:
            if QMessageBox.question(self, "Quit", "Files are being converted. Stop and quit? "
                                    "(Files already converted are kept.)") != QMessageBox.Yes:
                e.ignore()
                return
            conv.job.cancel.set()
        self.player.stop()
        self.cfg["ui"]["geometry"] = bytes(self.saveGeometry().toBase64()).decode()
        self.cfg.save()
        super().closeEvent(e)
