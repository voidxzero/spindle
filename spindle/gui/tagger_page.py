"""The Tag Files tab: fix up tags, artwork and file names for music you already have."""
from __future__ import annotations

import os
from collections import deque

from PySide6.QtCore import QPointF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QListWidget, QListWidgetItem, QMessageBox, QPushButton,
                               QRadioButton, QSpinBox, QSplitter, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from .. import acoustid, naming, tagfiles, tagger
from ..metadata import musicbrainz as mb
from ..metadata.base import AlbumMeta, TrackMeta
from .dialogs import CoverDialog, ReleaseDialog
from .widgets import CoverLabel, LoadingOverlay, Spinner, unify_heights
from .workers import run_async

T_FILE, T_NUM, T_TITLE, T_ARTIST, T_LEN, T_OLD = range(6)
STATUS_TEXT = {"new": "Waiting", "working": "Identifying…", "matched": "Matched", "check": "Please check",
               "nomatch": "No match", "saved": "Saved", "error": "Error"}


def _fmt(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}" if sec else ""


class TaggerPage(QWidget):
    logged = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.albums: list[tagfiles.FileAlbum] = []
        self.current: tagfiles.FileAlbum | None = None
        self._queue: deque[tagfiles.FileAlbum] = deque()
        self._identifying = False
        self._loading = False         # suppress edit signals while filling widgets
        self.colors = {"good": "#2ea043", "warn": "#d29922", "bad": "#da3633", "accent": "#3584e4", "dim": "#888888"}
        self.setAcceptDrops(True)
        self._build()

    # ================================================================ build
    def _build(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(6)

        def btn(text, slot, tip="", primary=False):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            if primary:
                b.setObjectName("primary")
            bar.addWidget(b)
            return b
        btn("Add Folder…", self.add_folder, "Add a folder of music (subfolders included). You can also drag folders here.")
        btn("Add Files…", self.add_files)
        bar.addSpacing(12)
        self.b_identify = btn("Identify Again", self.identify_selected, "Look the selected album up again")
        self.b_saveall = btn("Save All Matched", self.save_all, "Save every album marked Matched")
        self.b_rename = btn("Rename Files…", self.rename_files,
                            "Batch-rename files on disk: from their tags, or with find & replace")
        bar.addSpacing(12)
        btn("Remove", self.remove_selected, "Remove the selected album from the list (files are not touched)")
        btn("Clear", self.clear_all)
        bar.addStretch(1)
        self.spinner = Spinner(16)
        bar.addWidget(self.spinner)
        self.status = QLabel("")
        self.status.setObjectName("dim")
        bar.addWidget(self.status)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal)
        root.addWidget(split, 1)
        # ---- left: albums
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setIconSize(QSize(14, 14))
        self.list.setSpacing(2)
        self.list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list.currentRowChanged.connect(self._select_album)
        ll.addWidget(self.list)
        split.addWidget(left)
        # ---- right: details
        self.stack = QStackedWidget()
        empty = QLabel("Drop music folders here\n\nor use “Add Folder…”.\n\nSpindle groups the files into albums, "
                       "looks each one up on MusicBrainz,\nand shows you exactly what will change before anything is saved.")
        empty.setAlignment(Qt.AlignCenter)
        empty.setObjectName("dim")
        empty.setWordWrap(True)
        self.stack.addWidget(empty)
        detail = self._build_detail()
        self.stack.addWidget(detail)
        self.detail_overlay = LoadingOverlay(detail)
        split.addWidget(self.stack)
        split.setStretchFactor(0, 1)
        split.setStretchFactor(1, 3)
        split.setSizes([300, 900])

    def _build_detail(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        hdr = QFrame()
        hdr.setObjectName("header")
        g = QGridLayout(hdr)
        g.setContentsMargins(12, 12, 12, 12)
        g.setHorizontalSpacing(12)
        g.setVerticalSpacing(8)
        self.cover = CoverLabel(150)
        self.cover.clicked.connect(self.choose_cover)
        g.addWidget(self.cover, 0, 0, 6, 1, Qt.AlignTop)

        def lab(t):
            x = QLabel(t)
            x.setObjectName("fieldLabel")
            return x
        self.match = QComboBox()
        self.match.setMinimumContentsLength(30)
        self.match.activated.connect(self._match_picked)
        mrow = QHBoxLayout()
        mrow.setSpacing(8)
        mrow.addWidget(self.match, 1)
        b = QPushButton("Search…")
        b.clicked.connect(self.search)
        self.b_search = b
        mrow.addWidget(b)
        self.b_fp = QPushButton("Identify by Audio")
        self.b_fp.setToolTip("Fingerprint the files with AcoustID (for untagged or badly tagged files)")
        self.b_fp.clicked.connect(self.fingerprint)
        mrow.addWidget(self.b_fp)
        g.addWidget(lab("Match"), 0, 1)
        g.addLayout(mrow, 0, 2, 1, 3)
        self.f_artist, self.f_album, self.f_year = QLineEdit(), QLineEdit(), QLineEdit()
        self.f_year.setFixedWidth(120)
        self.f_genre = QLineEdit()
        self.f_label, self.f_catalog = QLineEdit(), QLineEdit()
        self.f_disc, self.f_discs = QSpinBox(), QSpinBox()
        for sp in (self.f_disc, self.f_discs):
            sp.setRange(1, 99)
            sp.setFixedWidth(64)
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
        self.verdict = QLabel("")
        self.verdict.setWordWrap(True)
        g.addWidget(self.verdict, 4, 1, 1, 4)
        g.setColumnStretch(2, 1)
        lay.addWidget(hdr)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["File", "#", "Title", "Artist", "Length", "Currently"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setDefaultSectionSize(26)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(T_FILE, QHeaderView.Interactive)
        h.setSectionResizeMode(T_TITLE, QHeaderView.Stretch)
        for c, wd in ((T_FILE, 220), (T_NUM, 44), (T_ARTIST, 180), (T_LEN, 70), (T_OLD, 200)):
            self.table.setColumnWidth(c, wd)
        self.table.itemChanged.connect(self._cell_edited)
        lay.addWidget(self.table, 1)

        foot = QHBoxLayout()
        foot.setSpacing(8)
        self.rename = QCheckBox("Rename files using my naming template")
        self.rename.setChecked(self.cfg["tagger"].get("rename", False))
        self.organize = QCheckBox("and move them into my music folder")
        self.organize.setChecked(self.cfg["tagger"].get("organize", False))
        self.rename.toggled.connect(self._opts_changed)
        self.organize.toggled.connect(self._opts_changed)
        foot.addWidget(self.rename)
        foot.addWidget(self.organize)
        foot.addStretch(1)
        self.b_revert = QPushButton("Revert")
        self.b_revert.setToolTip("Throw away your edits and go back to the matched release")
        self.b_revert.clicked.connect(self.revert)
        self.b_save = QPushButton("Save Album")
        self.b_save.setObjectName("primary")
        self.b_save.clicked.connect(self.save_current)
        foot.addWidget(self.b_revert)
        foot.addWidget(self.b_save)
        lay.addLayout(foot)
        for e in (self.f_artist, self.f_album, self.f_year, self.f_genre, self.f_label, self.f_catalog):
            e.editingFinished.connect(self._fields_edited)
        self.f_disc.valueChanged.connect(self._fields_edited)
        self.f_discs.valueChanged.connect(self._fields_edited)
        return w

    def unify(self):
        h = unify_heights([self.match, self.b_search, self.b_fp, self.f_artist, self.f_album, self.f_year,
                           self.f_genre, self.f_label, self.f_catalog, self.f_disc, self.f_discs],
                          reference=self.f_artist)
        if h:
            self.cover.set_side(4 * h + 3 * 8 + 24)    # match row + three field rows + the verdict line
        unify_heights([self.b_revert, self.b_save], reference=self.f_artist)

    def set_colors(self, colors: dict):
        self.colors.update({k: colors[k] for k in ("good", "warn", "bad", "accent", "dim") if k in colors})
        self._refresh_list()
        if self.current:
            self._fill_table()

    # ============================================================ adding
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    def dropEvent(self, e):
        paths = [u.toLocalFile() for u in e.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)

    def add_folder(self):
        d = QFileDialog.getExistingDirectory(self, "Add music folder", os.path.expanduser(self.cfg["output"]["directory"]))
        if d:
            self.add_paths([d])

    def add_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "Add music files", os.path.expanduser(self.cfg["output"]["directory"]),
                                                "Audio (*.flac *.mp3 *.m4a *.ogg *.opus *.wv *.wav)")
        if files:
            self.add_paths(files)

    def add_paths(self, paths: list[str]):
        self._busy("Reading files…")

        def done(files):
            self._idle()
            known = {f.path for a in self.albums for f in a.files}
            files = [f for f in files if f.path not in known]
            bad = [f for f in files if f.error]
            for f in bad:
                self.logged.emit(f"Tag Files: can't read {f.name}: {f.error}")
            new = tagfiles.group(files)
            if not new:
                self.status.setText("No new audio files found.")
                return
            self.albums.extend(new)
            self._refresh_list()
            if self.list.currentRow() < 0:
                self.list.setCurrentRow(self.albums.index(new[0]))
            self.logged.emit(f"Tag Files: added {sum(len(a.files) for a in new)} files in {len(new)} album(s)")
            for a in new:
                self._queue.append(a)
            self._next_identify()
        run_async(tagfiles.scan, paths, on_done=done,
                  on_error=lambda m: (self._idle(), QMessageBox.warning(self, "Tag Files", m)))

    # ======================================================== identifying
    def _next_identify(self):
        if self._identifying:
            return
        while self._queue and self._queue[0] not in self.albums:
            self._queue.popleft()
        if not self._queue:
            self._idle(f"{sum(1 for a in self.albums if a.status == 'matched')} of {len(self.albums)} album(s) matched")
            return
        fa = self._queue.popleft()
        self._identifying = True
        left = len(self._queue)
        self._busy(f"Identifying {fa.title}…" + (f"  ({left} more queued)" if left else ""))
        fa.status = "working"
        self._refresh_list()
        if fa is self.current:
            self.detail_overlay.show_message("Looking this album up on MusicBrainz…")

        def done(_):
            self._identifying = False
            self._after_identify(fa)
            self._next_identify()

        def fail(msg):
            self._identifying = False
            fa.status, fa.message = "error", msg
            self._refresh_list()
            if fa is self.current:
                self._show_album(fa)
            self._next_identify()
        run_async(tagfiles.identify, self.cfg, fa, self.logged.emit, on_done=done, on_error=fail)

    def _after_identify(self, fa):
        if fa.edited is None:
            self._from_existing(fa)
        self._refresh_list()
        if fa is self.current:
            self._show_album(fa)
        if fa.status in ("matched", "check") and self.cfg["coverart"].get("auto_fetch", True):
            self._load_existing_cover(fa, then_fetch=True)

    def _from_existing(self, fa):
        """No match: start from the files' current tags so the user can fix things by hand."""
        a = AlbumMeta(title=fa.guess_album, artist=fa.guess_artist or tagfiles._folder_artist(fa.folder),
                      date=next((f.tag("date") for f in fa.files if f.tag("date")), ""),
                      genre=next((f.tag("genre") for f in fa.files if f.tag("genre")), ""), source="Existing tags")
        nums = [f.track_no for f in fa.files]
        unique = len(set(nums)) == len(nums) and all(nums)
        fa.assignment = {}
        for i, f in enumerate(fa.files):
            n = nums[i] if unique else i + 1
            a.tracks.append(TrackMeta(n, f.title_guess, f.tag("artist") or a.artist))
            fa.assignment[i] = n
        fa.edited = a

    def identify_selected(self):
        if self.current:
            self.current.status = "new"
            self._queue.appendleft(self.current)
            self._next_identify()

    def fingerprint(self):
        fa = self.current
        if not fa:
            return
        if not self.cfg["metadata"].get("acoustid_key"):
            QMessageBox.information(self, "Identify by audio",
                                    "Add a free AcoustID API key in Settings ▸ Metadata to identify files by their audio.")
            return
        if not acoustid.available():
            QMessageBox.information(self, "Identify by audio",
                                    "The fpcalc tool is needed. On Fedora: sudo dnf install chromaprint-tools")
            return
        self._busy(f"Fingerprinting {len(fa.files)} file(s)…")
        fa.status = "working"
        self._refresh_list()

        def done(_):
            self._idle()
            self._after_identify(fa)
        run_async(tagfiles.identify, self.cfg, fa, self.logged.emit, use_fingerprints=True,
                  on_done=done, on_error=lambda m: (self._idle(), self.logged.emit(f"Fingerprint failed: {m}")))

    def search(self):
        fa = self.current
        if not fa:
            return
        self._collect()

        def search_fn(artist, album):
            out = []
            for rel in mb.search_releases(artist, album, 8)[:6]:
                full = mb.fetch_release(rel["id"])
                albums = mb.release_albums(full)
                if albums:   # best-fitting disc of each release
                    out.append(max(albums, key=lambda a: tagfiles.score(fa, a).score))
            return out
        a = fa.edited
        dlg = ReleaseDialog(self.cfg, None, [c.album for c in fa.candidates], self,
                            artist=a.artist if a else fa.guess_artist, album=a.title if a else fa.guess_album,
                            search_fn=search_fn)
        dlg.setWindowTitle("Find the right release")
        if dlg.exec() and dlg.selected():
            chosen = dlg.selected()
            for i, c in enumerate(fa.candidates):
                if c.album is chosen:
                    tagfiles.choose(fa, i)
                    break
            else:
                fa.candidates.insert(0, tagfiles.score(fa, chosen))
                tagfiles.choose(fa, 0)
            self._after_identify(fa)

    # =========================================================== cover art
    def _load_existing_cover(self, fa, then_fetch=False):
        def load():
            return tagger.read_cover(fa.files[0].path) if fa.files else None

        def done(data):
            fa.existing_cover = data
            if fa is self.current and fa.cover is None and data:
                self.cover.set_image(data)
            if then_fetch and not data and fa.cover is None:
                self._fetch_cover(fa)
        run_async(load, on_done=done)

    def _fetch_cover(self, fa):
        if fa is self.current:
            self.cover.set_loading(True)

        def done(c):
            if fa is self.current:
                self.cover.set_loading(False)
            if c:
                fa.cover = c
                if fa is self.current:
                    self._show_cover(fa)
        run_async(tagfiles.find_cover, self.cfg, fa, self.logged.emit, on_done=done,
                  on_error=lambda m: self.cover.set_loading(False))

    def _show_cover(self, fa):
        data = fa.cover.data if fa.cover and fa.cover.data else fa.existing_cover
        if data:
            self.cover.set_image(data)
            src = f"New: {fa.cover.source} {fa.cover.width}×{fa.cover.height}" if fa.cover else "Current embedded cover"
            self.cover.setToolTip(src + "\nClick to change")
        else:
            self.cover.clear_cover()

    def choose_cover(self):
        fa = self.current
        if not fa:
            return
        self._collect()
        dlg = CoverDialog(self.cfg, fa.edited or AlbumMeta(title=fa.guess_album, artist=fa.guess_artist), self)
        if dlg.exec() and dlg.result_cover:
            fa.cover = dlg.result_cover
            self._show_cover(fa)

    # ============================================================== display
    def _status_icon(self, status: str) -> QIcon:
        color = {"matched": "good", "saved": "good", "check": "warn", "nomatch": "bad", "error": "bad"}.get(status)
        pm = QPixmap(14, 14)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        if color:
            p.setBrush(QColor(self.colors[color]))
            p.setPen(Qt.NoPen)
            p.drawEllipse(1, 1, 12, 12)
            if status == "saved":
                pen = QPen(QColor("#ffffff"), 1.8)
                pen.setCapStyle(Qt.RoundCap)
                p.setPen(pen)
                p.drawPolyline([QPointF(4, 7.2), QPointF(6.2, 9.4), QPointF(10.2, 4.8)])
        else:
            p.setPen(QColor(self.colors["dim"]))
            p.drawEllipse(2, 2, 10, 10)
        p.end()
        return QIcon(pm)

    def _refresh_list(self):
        self.list.blockSignals(True)
        row = self.list.currentRow()
        self.list.clear()
        for a in self.albums:
            it = QListWidgetItem(self._status_icon(a.status),
                                 f"{a.title}\n{len(a.files)} file(s) · {a.message or STATUS_TEXT.get(a.status, '')}")
            it.setToolTip(a.folder)
            self.list.addItem(it)
        if 0 <= row < self.list.count():
            self.list.setCurrentRow(row)
        self.list.blockSignals(False)
        self.b_saveall.setEnabled(any(a.status == "matched" for a in self.albums))

    def _select_album(self, row):
        self._collect()
        if 0 <= row < len(self.albums):
            self._show_album(self.albums[row])
        else:
            self.current = None
            self.stack.setCurrentIndex(0)

    def _show_album(self, fa):
        self.current = fa
        self.stack.setCurrentIndex(1)
        if fa.edited is None and fa.status not in ("new", "working"):
            self._from_existing(fa)
        self._loading = True
        self.match.clear()
        for c in fa.candidates:
            self.match.addItem(c.label())
        if not fa.candidates:
            self.match.addItem("Identifying…" if fa.status in ("new", "working") else "No match — edit by hand or Search…")
        self.match.setCurrentIndex(max(0, fa.chosen))
        a = fa.edited
        if a:
            self.f_artist.setText(a.artist)
            self.f_album.setText(a.title)
            self.f_year.setText(a.date or a.original_date)
            self.f_genre.setText(a.genre)
            self.f_label.setText(a.label)
            self.f_catalog.setText(a.catalog)
            self.f_disc.setValue(max(1, a.disc_number))
            self.f_discs.setValue(max(1, a.disc_total))
        else:
            for e in (self.f_artist, self.f_album, self.f_year, self.f_genre, self.f_label, self.f_catalog):
                e.clear()
            self.f_artist.setPlaceholderText(fa.guess_artist)
            self.f_album.setPlaceholderText(fa.guess_album)
        self._loading = False
        self._fill_table()
        self._update_verdict()
        if fa.cover is None and fa.existing_cover is None:
            self.cover.clear_cover()
            self._load_existing_cover(fa)
        else:
            self._show_cover(fa)
        busy = fa.status in ("new", "working")
        for wdg in (self.b_save, self.b_revert, self.b_fp):
            wdg.setEnabled(not busy)
        if busy:
            self.detail_overlay.show_message("Looking this album up on MusicBrainz…"
                                             if fa.status == "working" else "Waiting to be looked up…")
        else:
            self.detail_overlay.finish()

    def _update_verdict(self):
        fa = self.current
        if not fa:
            return
        color = {"matched": "good", "saved": "good", "check": "warn", "nomatch": "bad", "error": "bad"}.get(fa.status)
        unmatched = len(fa.files) - len(fa.assignment)
        changes = self._count_changes()
        bits = [fa.message or STATUS_TEXT.get(fa.status, "")]
        if fa.edited and fa.status not in ("new", "working"):
            bits.append(f"{changes} file(s) will change" if changes else "No tag changes")
        if unmatched:
            bits.append(f"{unmatched} file(s) didn't match a track and will be skipped")
        self.verdict.setText(" · ".join(b for b in bits if b))
        self.verdict.setStyleSheet(f"color: {self.colors[color]}; font-weight: bold;" if color else "")

    def _count_changes(self) -> int:
        fa = self.current
        if not fa or not fa.edited:
            return 0
        n = 0
        for i, f in enumerate(fa.files):
            t = tagfiles.track_for(fa, i)
            if t and (t.title != f.tag("title") or (t.artist or fa.edited.artist) != f.tag("artist")
                      or fa.edited.title != f.tag("album") or str(t.number) != f.tag("tracknumber")
                      or fa.edited.artist != f.tag("albumartist") or fa.cover is not None):
                n += 1
        return n

    def _fill_table(self):
        fa = self.current
        if not fa:
            return
        self._loading = True
        self.table.setRowCount(len(fa.files))
        rel_lengths = {t.number: t.length_ms / 1000 for t in fa.edited.tracks} if fa.edited else {}
        bold = QFont()
        bold.setBold(True)
        for i, f in enumerate(fa.files):
            t = tagfiles.track_for(fa, i)

            def item(text, editable=False, changed=False, color=None, tip=""):
                it = QTableWidgetItem(text)
                flags = Qt.ItemIsEnabled | Qt.ItemIsSelectable
                if editable:
                    flags |= Qt.ItemIsEditable
                it.setFlags(flags)
                if changed:
                    it.setFont(bold)
                    it.setForeground(QBrush(QColor(self.colors["accent"])))
                if color:
                    it.setForeground(QBrush(QColor(self.colors[color])))
                if tip:
                    it.setToolTip(tip)
                return it
            rel = os.path.relpath(f.path, fa.folder)
            self.table.setItem(i, T_FILE, item(rel, tip=f.path))
            if t:
                old_t, old_a = f.tag("title"), f.tag("artist")
                artist = t.artist or (fa.edited.artist if fa.edited else "")
                self.table.setItem(i, T_NUM, item(str(t.number), True, str(t.number) != f.tag("tracknumber")))
                self.table.setItem(i, T_TITLE, item(t.title, True, t.title != old_t, tip=f"Currently: {old_t or '(empty)'}"))
                self.table.setItem(i, T_ARTIST, item(artist, True, artist != old_a, tip=f"Currently: {old_a or '(empty)'}"))
            else:
                self.table.setItem(i, T_NUM, item("", True))
                self.table.setItem(i, T_TITLE, item("(no matching track — will be skipped)", False, color="warn"))
                self.table.setItem(i, T_ARTIST, item(""))
            rl = rel_lengths.get(t.number, 0) if t else 0
            off = rl and abs(rl - f.length) > 5
            self.table.setItem(i, T_LEN, item(_fmt(f.length), color="bad" if off else None,
                                              tip=f"File {_fmt(f.length)} · release {_fmt(rl)}" if rl else ""))
            cur = f.tag("title") or "(no title)"
            old = item(cur, color="dim")
            self.table.setItem(i, T_OLD, old)
        self._loading = False

    # ================================================================ edits
    def _match_picked(self, idx):
        fa = self.current
        if fa and 0 <= idx < len(fa.candidates):
            tagfiles.choose(fa, idx)
            self._show_album(fa)
            self._refresh_list()
            if fa.existing_cover is None and fa.cover is None and self.cfg["coverart"].get("auto_fetch", True):
                self._fetch_cover(fa)

    def _fields_edited(self, *_):
        if not self._loading:
            self._collect()
            self._fill_table()
            self._update_verdict()

    def _cell_edited(self, it: QTableWidgetItem):
        if self._loading or not self.current or not self.current.edited:
            return
        fa, i, col = self.current, it.row(), it.column()
        if col == T_NUM:
            try:
                n = int(it.text())
            except ValueError:
                self._fill_table()
                return
            # swap with whichever file had that number
            other = next((j for j, m in fa.assignment.items() if m == n and j != i), None)
            if other is not None and i in fa.assignment:
                fa.assignment[other] = fa.assignment[i]
            elif other is not None:
                del fa.assignment[other]
            if n not in [t.number for t in fa.edited.tracks]:
                fa.edited.tracks.append(TrackMeta(n, fa.files[i].title_guess, fa.edited.artist))
            fa.assignment[i] = n
        else:
            t = tagfiles.track_for(fa, i)
            if t and col == T_TITLE:
                t.title = it.text().strip()
            elif t and col == T_ARTIST:
                t.artist = it.text().strip()
        self._fill_table()
        self._update_verdict()

    def _collect(self):
        """Push header-field edits into the album being edited."""
        fa = self.current
        if not fa or not fa.edited or self._loading:
            return
        a = fa.edited
        old_artist = a.artist
        a.artist = self.f_artist.text().strip()
        a.title = self.f_album.text().strip()
        yr = self.f_year.text().strip()
        if yr != (a.date or a.original_date):
            a.date = yr
        a.genre = self.f_genre.text().strip()
        a.label, a.catalog = self.f_label.text().strip(), self.f_catalog.text().strip()
        a.disc_number, a.disc_total = self.f_disc.value(), self.f_discs.value()
        if old_artist != a.artist:
            for t in a.tracks:          # tracks that just inherited the album artist follow the edit
                if t.artist == old_artist:
                    t.artist = a.artist

    def _opts_changed(self):
        self.cfg["tagger"]["rename"] = self.rename.isChecked()
        self.cfg["tagger"]["organize"] = self.organize.isChecked()
        self.organize.setEnabled(self.rename.isChecked())
        self.cfg.save()

    def revert(self):
        fa = self.current
        if fa and fa.candidates and fa.chosen >= 0:
            tagfiles.choose(fa, fa.chosen)
        elif fa:
            fa.edited = None
            self._from_existing(fa)
        if fa:
            self._show_album(fa)

    # ================================================================ saving
    def _confirm_move(self, fa) -> bool:
        if not self.rename.isChecked():
            return True
        org = self.organize.isChecked() and self.organize.isEnabled()
        sample = [tagfiles.target_path(self.cfg, fa, i, org) for i in range(min(3, len(fa.files)))]
        return QMessageBox.question(
            self, "Rename files", "Files will be renamed" + (" and moved" if org else "") + ", for example:\n\n"
            + "\n".join(sample) + "\n\nContinue?") == QMessageBox.Yes

    def save_current(self):
        fa = self.current
        if not fa:
            return
        self._collect()
        if not fa.edited or not fa.assignment:
            QMessageBox.information(self, "Save", "Nothing to save yet.")
            return
        if not self._confirm_move(fa):
            return
        self._save([fa])

    def save_all(self):
        self._collect()
        todo = [a for a in self.albums if a.status == "matched" and a.edited]
        if not todo:
            return
        if self.rename.isChecked() and not self._confirm_move(todo[0]):
            return
        self._save(todo)

    def _save(self, albums):
        rename = self.rename.isChecked()
        organize = rename and self.organize.isChecked()
        self._busy(f"Saving {len(albums)} album(s)…")

        def work():
            errors = []
            for fa in albums:
                try:
                    tagfiles.save(self.cfg, fa, rename, organize, self.logged.emit)
                except Exception as e:  # noqa: BLE001
                    fa.status, fa.message = "error", str(e)
                    errors.append(f"{fa.title}: {e}")
            return errors

        def done(errors):
            self._idle(f"Saved {len(albums) - len(errors)} album(s)")
            self._refresh_list()
            if self.current:
                self._show_album(self.current)
            if errors:
                QMessageBox.warning(self, "Some albums couldn't be saved", "\n".join(errors[:10]))
            for fa in albums:
                if fa.status == "saved":
                    self.logged.emit(f"Tag Files: saved {fa.title}")
        run_async(work, on_done=done, on_error=lambda m: (self._idle(), QMessageBox.warning(self, "Save", m)))

    # ================================================================ rename
    def rename_files(self):
        if not self.albums:
            QMessageBox.information(self, "Rename files", "Add a folder of music first.")
            return
        self._collect()
        dlg = RenameDialog(self.cfg, self.albums, self.current, self)
        if dlg.exec() and dlg.renamed:
            self.logged.emit(f"Tag Files: renamed {dlg.renamed} file(s)")
            self._idle(f"Renamed {dlg.renamed} file(s)")
            if self.current:
                self._fill_table()

    # ================================================================== misc
    def remove_selected(self):
        row = self.list.currentRow()
        if 0 <= row < len(self.albums):
            del self.albums[row]
            self.current = None
            self._refresh_list()
            self.list.setCurrentRow(min(row, len(self.albums) - 1))
            if not self.albums:
                self.stack.setCurrentIndex(0)

    def clear_all(self):
        self.albums.clear()
        self._queue.clear()
        self.current = None
        self._refresh_list()
        self.stack.setCurrentIndex(0)

    def _busy(self, text):
        self.status.setText(text)
        self.spinner.start()

    def _idle(self, text=""):
        self.spinner.stop()
        self.status.setText(text)


class RenameDialog(QDialog):
    """Batch-rename files on disk, from their tags or with find & replace, with a live preview."""

    def __init__(self, cfg, albums, current, parent=None):
        super().__init__(parent)
        self.cfg, self.albums, self.current = cfg, albums, current
        self.setWindowTitle("Rename files")
        self.resize(900, 560)
        lay = QVBoxLayout(self)
        lay.setSpacing(8)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        self.m_pattern = QRadioButton("Name from tags:")
        self.m_replace = QRadioButton("Find and replace:")
        self.m_pattern.setChecked(True)
        last = cfg["output"]["template"].split("/")[-1] or "%tracknumber% - %title%"
        self.pattern = QLineEdit(last)
        self.pattern.setToolTip("Fields: " + " ".join(f"%{x}%" for x in naming.FIELDS) +
                                "\nText in [brackets] is left out when a field inside it is empty.")
        self.find, self.replace = QLineEdit(), QLineEdit()
        self.find.setPlaceholderText("Find")
        self.replace.setPlaceholderText("Replace with")
        self.regex = QCheckBox("Regular expression")
        self.case = QCheckBox("Match case")
        grid.addWidget(self.m_pattern, 0, 0)
        grid.addWidget(self.pattern, 0, 1, 1, 3)
        grid.addWidget(self.m_replace, 1, 0)
        grid.addWidget(self.find, 1, 1)
        grid.addWidget(self.replace, 1, 2)
        opts = QHBoxLayout()
        opts.setSpacing(8)
        opts.addWidget(self.regex)
        opts.addWidget(self.case)
        grid.addLayout(opts, 1, 3)
        self.scope = QComboBox()
        self.scope.addItem("Only the selected album", "one")
        self.scope.addItem(f"All {len(albums)} albums in the list", "all")
        if current is None:
            self.scope.setCurrentIndex(1)
        grid.addWidget(QLabel("Apply to:"), 2, 0)
        grid.addWidget(self.scope, 2, 1)
        grid.setColumnStretch(1, 2)
        grid.setColumnStretch(2, 2)
        lay.addLayout(grid)
        hint = QLabel("Names come from each file's current tags — save tag changes first if you just edited them. "
                      "Files stay in their folders and keep their extensions.")
        hint.setObjectName("dim")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["Current name", "New name"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setShowGrid(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setWordWrap(False)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        lay.addWidget(self.table, 1)
        self.summary = QLabel()
        lay.addWidget(self.summary)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.ok = bb.button(QDialogButtonBox.Ok)
        self.ok.setText("Rename")
        self.ok.setObjectName("primary")
        bb.accepted.connect(self._apply)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        for w in (self.pattern, self.find, self.replace):
            w.textChanged.connect(self._preview)
            w.textEdited.connect(self._auto_mode)
        for w in (self.m_pattern, self.m_replace, self.regex, self.case):
            w.toggled.connect(self._preview)
        self.scope.currentIndexChanged.connect(self._preview)
        self.plans: list[tagfiles.RenamePlan] = []
        self.renamed = 0
        self._preview()

    def _auto_mode(self):
        # typing in a field selects its mode
        if self.sender() is self.pattern:
            self.m_pattern.setChecked(True)
        else:
            self.m_replace.setChecked(True)

    def _files(self):
        albums = [self.current] if self.scope.currentData() == "one" and self.current else self.albums
        return [f for a in albums for f in a.files]

    def _preview(self, *_):
        safe = self.cfg["output"].get("safe_filenames", True)
        if self.m_pattern.isChecked():
            self.plans = tagfiles.plan_renames(self._files(), "pattern", pattern=self.pattern.text(), safe=safe)
            unknown = naming.unknown_fields(self.pattern.text())
        else:
            self.plans = tagfiles.plan_renames(self._files(), "replace", find=self.find.text(),
                                               replace=self.replace.text(), regex=self.regex.isChecked(),
                                               case=self.case.isChecked(), safe=safe)
            unknown = []
        bold = QFont()
        bold.setBold(True)
        self.table.setRowCount(len(self.plans))
        dim, bad, accent = (self.parent().colors.get(k, "#888") if hasattr(self.parent(), "colors") else "#888"
                            for k in ("dim", "bad", "accent"))
        for r, p in enumerate(self.plans):
            old = QTableWidgetItem(os.path.basename(p.file.path))
            old.setToolTip(p.file.path)
            new = QTableWidgetItem(os.path.basename(p.new_path))
            if p.problem:
                new.setForeground(QBrush(QColor(bad)))
                new.setToolTip(p.problem)
                new.setText(f"{os.path.basename(p.new_path)}   ⚠ {p.problem}")
            elif p.changed:
                new.setFont(bold)
                new.setForeground(QBrush(QColor(accent)))
            else:
                new.setForeground(QBrush(QColor(dim)))
            self.table.setItem(r, 0, old)
            self.table.setItem(r, 1, new)
        changes = sum(1 for p in self.plans if p.changed and not p.problem)
        problems = sum(1 for p in self.plans if p.problem)
        msg = f"{changes} file(s) will be renamed"
        if problems:
            msg += f" · {problems} skipped (see ⚠)"
        if unknown:
            msg += " · unknown field(s): " + ", ".join(f"%{u}%" for u in unknown)
        self.summary.setText(msg)
        self.ok.setEnabled(changes > 0)

    def _apply(self):
        try:
            self.renamed = tagfiles.apply_renames(self.plans)
        except OSError as e:
            QMessageBox.warning(self, "Rename files", f"Renaming stopped: {e}")
            return
        self.accept()
