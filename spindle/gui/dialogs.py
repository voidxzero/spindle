"""Dialogs: release chooser, cover art picker, settings."""
from __future__ import annotations

import os

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices, QIcon, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QGroupBox, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem,
                               QMessageBox, QPushButton, QRadioButton, QScrollArea, QSpinBox,
                               QStackedWidget, QTabWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import accuraterip, coverart, encoders, metadata, naming, net
from ..config import DATA_DIR
from ..disc import DriveInfo, list_drives
from ..metadata.base import AlbumMeta, CoverCandidate, TrackMeta
from .themes import theme_names
from .widgets import LoadingOverlay, ProviderList, Spinner
from .workers import run_async


# ---------------------------------------------------------------------------
class ReleaseDialog(QDialog):
    """Pick one of several matches, or search by artist/album."""

    def __init__(self, cfg, toc, candidates: list[AlbumMeta], parent=None, artist="", album="",
                 search_fn=None):
        super().__init__(parent)
        self.cfg, self.toc = cfg, toc
        # search_fn(artist, album) -> list[AlbumMeta]; defaults to a disc-aware metadata search
        self.search_fn = search_fn or (lambda ar, al: metadata.search_all(self.cfg, ar, al, self.toc))
        self.setWindowTitle("Choose release")
        self.resize(980, 480)
        self.candidates = list(candidates)
        lay = QVBoxLayout(self)
        info = QLabel(f"{len(candidates)} release(s) match this disc." if candidates else
                      "This disc wasn't found by its ID. Search by artist and album instead:")
        info.setObjectName("dim")
        lay.addWidget(info)
        row = QHBoxLayout()
        self.artist = QLineEdit(artist)
        self.artist.setPlaceholderText("Artist")
        self.album = QLineEdit(album)
        self.album.setPlaceholderText("Album")
        self.search_btn = QPushButton("Search")
        self.search_btn.clicked.connect(self._search)
        self.album.returnPressed.connect(self._search)
        row.setSpacing(8)
        row.addWidget(self.artist, 2)
        row.addWidget(self.album, 3)
        self.spinner = Spinner(16)
        row.addWidget(self.spinner)
        row.addWidget(self.search_btn)
        lay.addLayout(row)
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Artist", "Album", "Date", "Country", "Label / Cat#", "Format", "Source"])
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        # short columns hug their content; Album and Label/Cat# share whatever width is left
        for col in (0, 2, 3, 5, 6):
            h.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        h.setMinimumSectionSize(48)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self.table.doubleClicked.connect(self.accept)
        lay.addWidget(self.table, 1)
        self.overlay = LoadingOverlay(self.table)
        self.tracks = QLabel()
        self.tracks.setObjectName("dim")
        self.tracks.setWordWrap(True)
        lay.addWidget(self.tracks)
        self.table.itemSelectionChanged.connect(self._preview)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Use this release")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self._fill()

    def _fill(self):
        self.table.setRowCount(len(self.candidates))
        for r, a in enumerate(self.candidates):
            disc = f"{a.disc_number}/{a.disc_total}" if a.disc_total > 1 else a.media
            vals = [a.artist, a.title + (f"  ({a.extra.get('disambiguation')})" if a.extra.get("disambiguation") else ""),
                    a.date, a.country, " ".join(x for x in (a.label, a.catalog) if x), disc, a.source]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v or "")
                it.setToolTip(v or "")
                self.table.setItem(r, c, it)
        if self.candidates:
            self.table.selectRow(0)

    def _preview(self):
        a = self.selected()
        if a:
            self.tracks.setText("  ·  ".join(f"{t.number}. {t.title}" for t in a.tracks[:30]))

    def _search(self):
        self.search_btn.setEnabled(False)
        self.search_btn.setText("Searching…")
        self.spinner.start()
        self.overlay.show_message("Searching MusicBrainz…")

        def done(res):
            self.spinner.stop()
            self.overlay.finish()
            self.search_btn.setEnabled(True)
            self.search_btn.setText("Search")
            self.candidates = res + [c for c in self.candidates if c not in res]
            self._fill()
            if not res:
                QMessageBox.information(self, "Search", "Nothing found with that many tracks.")

        def fail(msg):
            self.spinner.stop()
            self.overlay.finish()
            self.search_btn.setEnabled(True)
            self.search_btn.setText("Search")
            QMessageBox.warning(self, "Search failed", msg)
        run_async(self.search_fn, self.artist.text().strip(), self.album.text().strip(),
                  on_done=done, on_error=fail)

    def selected(self) -> AlbumMeta | None:
        rows = self.table.selectionModel().selectedRows()
        return self.candidates[rows[0].row()] if rows else None


# ---------------------------------------------------------------------------
class CoverDialog(QDialog):
    def __init__(self, cfg, album: AlbumMeta, parent=None):
        super().__init__(parent)
        self.cfg, self.album = cfg, album
        self.result_cover: CoverCandidate | None = None
        self.setWindowTitle("Cover art")
        self.resize(860, 560)
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.artist = QLineEdit(album.artist)
        self.title = QLineEdit(album.title)
        go = QPushButton("Search again")
        go.clicked.connect(self._search)
        local = QPushButton("From file…")
        local.clicked.connect(self._from_file)
        row.setSpacing(8)
        row.addWidget(QLabel("Artist"))
        row.addWidget(self.artist, 2)
        row.addWidget(QLabel("Album"))
        row.addWidget(self.title, 3)
        row.addWidget(go)
        row.addWidget(local)
        lay.addLayout(row)
        srow = QHBoxLayout()
        srow.setSpacing(8)
        self.spinner = Spinner(16)
        srow.addWidget(self.spinner)
        self.status = QLabel("Searching…")
        self.status.setObjectName("dim")
        srow.addWidget(self.status, 1)
        lay.addLayout(srow)
        self._pending = 0
        self.grid = QListWidget()
        self.grid.setViewMode(QListWidget.IconMode)
        self.grid.setIconSize(QSize(200, 200))
        self.grid.setGridSize(QSize(230, 268))
        self.grid.setResizeMode(QListWidget.Adjust)
        self.grid.setMovement(QListWidget.Static)
        self.grid.setWordWrap(True)
        self.grid.itemDoubleClicked.connect(lambda _: self.accept())
        lay.addWidget(self.grid, 1)
        self.overlay = LoadingOverlay(self.grid)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.b_edit = bb.addButton("Edit / Crop…", QDialogButtonBox.ActionRole)
        self.b_edit.setToolTip("Crop, rotate or resize the selected image")
        self.b_edit.clicked.connect(self._edit)
        bb.button(QDialogButtonBox.Ok).setText("Use selected")
        bb.button(QDialogButtonBox.Ok).setObjectName("primary")
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.cands: list[CoverCandidate] = []
        self._search()

    def _search(self):
        q = AlbumMeta.from_dict(self.album.to_dict())
        q.artist, q.title = self.artist.text().strip(), self.title.text().strip()
        self.grid.clear()
        self.cands = []
        self._pending = 0
        self.status.setText("Searching cover art sources…")
        self.spinner.start()
        self.overlay.show_message("Searching Cover Art Archive, iTunes, Deezer…")

        def fail(m):
            self.overlay.finish()
            self.spinner.stop()
            self.status.setText(f"Search failed: {m}")
        run_async(coverart.find_all, self.cfg, q, on_done=self._got, on_error=fail)

    def _got(self, cands: list[CoverCandidate]):
        self.overlay.finish()
        cands = sorted(cands, key=lambda c: -c.score)
        if not cands:
            self.spinner.stop()
            self.status.setText("Nothing found. Try editing the search or load a file.")
        for c in cands:
            self._add(c)
        self._update_status()

    def _update_status(self):
        n = len(self.cands)
        if self._pending:
            self.spinner.start()
            self.status.setText(f"Loading images… {n - self._pending} of {n} ready")
        elif n:
            self.spinner.stop()
            self.status.setText(f"{n} image(s). Double-click to use one, or Edit / Crop… to adjust it.")

    def _add(self, c: CoverCandidate):
        self.cands.append(c)
        it = QListWidgetItem(f"{c.source}\n{c.description}\nloading…")
        it.setData(Qt.UserRole, len(self.cands) - 1)
        it.setTextAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self.grid.addItem(it)
        if c.data is not None:
            self._show_thumb(it, c, c.data)
            return

        it.setIcon(QIcon(self._placeholder()))
        self._pending += 1

        def fetch():
            # full image so we can show true dimensions; thumbnails are small anyway
            return coverart.download(c)

        def done(data, it=it, c=c):
            self._pending -= 1
            self._show_thumb(it, c, data)
            self._update_status()

        def fail(m, it=it, c=c):
            self._pending -= 1
            it.setText(f"{c.source}\n{c.description}\n(failed)")
            self._update_status()
        run_async(fetch, on_done=done, on_error=fail)

    def _placeholder(self) -> QPixmap:
        """Grey tile shown while an image downloads."""
        from PySide6.QtGui import QColor, QPainter
        pm = QPixmap(200, 200)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(self.palette().mid().color())
        col.setAlpha(90)
        p.setBrush(col)
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(10, 10, 180, 180, 10, 10)
        p.setPen(self.palette().text().color())
        p.drawText(pm.rect(), Qt.AlignCenter, "Loading…")
        p.end()
        return pm

    def _show_thumb(self, it, c, data):
        pm = QPixmap()
        if pm.loadFromData(data):
            it.setIcon(QIcon(pm.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)))
        size = f"{c.width}×{c.height}" if c.width else "?"
        it.setText(f"{c.source} · {size}\n{c.description}")

    def _edit(self):
        it = self.grid.currentItem()
        if it is None:
            QMessageBox.information(self, "Cover art", "Select an image first.")
            return
        c = self.cands[it.data(Qt.UserRole)]
        if c.data is None:
            QMessageBox.information(self, "Cover art", "That image is still downloading (or failed).")
            return
        from .cover_editor import edit_cover
        edited = edit_cover(self, c.data, self.cfg)
        if edited:
            self._add(edited)
            self.grid.setCurrentRow(self.grid.count() - 1)
            self._update_status()

    def _from_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "Choose cover image", os.path.expanduser("~"),
                                              "Images (*.jpg *.jpeg *.png)")
        if not path:
            return
        try:
            c = coverart.from_file(path)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Cover art", str(e))
            return
        self._add(c)
        self.grid.setCurrentRow(self.grid.count() - 1)

    def accept(self):
        it = self.grid.currentItem()
        if it is None:
            return super().reject()
        c = self.cands[it.data(Qt.UserRole)]
        if c.data is None:
            QMessageBox.information(self, "Cover art", "That image is still downloading (or failed).")
            return
        self.result_cover = c
        super().accept()


# ---------------------------------------------------------------------------
class SettingsDialog(QDialog):
    def __init__(self, cfg, drive: DriveInfo | None, parent=None):
        super().__init__(parent)
        self.cfg, self.drive = cfg, drive
        self.setWindowTitle("Spindle settings")
        self.resize(760, 600)
        lay = QVBoxLayout(self)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self._drive_tab()
        self._output_tab()
        self._formats_tab()
        self._meta_tab()
        self._cover_tab()
        self._plugins_tab()
        self._look_tab()
        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setObjectName("primary")
        bb.accepted.connect(self._save)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)

    # --- Drive -------------------------------------------------------------
    def _drive_tab(self):
        d = self.cfg["drive"]
        w = QWidget()
        f = QFormLayout(w)
        self.device = QComboBox()
        self.device.addItem("Automatic (first drive)", "")
        for dr in list_drives():
            self.device.addItem(f"{dr.name}  ({dr.device})", dr.device)
        i = self.device.findData(d["device"])
        self.device.setCurrentIndex(max(0, i))
        f.addRow("Drive", self.device)
        off_row = QHBoxLayout()
        self.offset = QSpinBox()
        self.offset.setRange(-3000, 3000)
        self.offset.setValue(int(d["read_offset"]))
        self.offset.setSuffix(" samples")
        detect = QPushButton("Look up in AccurateRip")
        detect.clicked.connect(self._detect_offset)
        self.b_detect = detect
        self.detect_spin = Spinner(16)
        off_row.addWidget(self.offset)
        off_row.addWidget(detect)
        off_row.addWidget(self.detect_spin)
        off_row.addStretch(1)
        f.addRow("Read offset correction", off_row)
        hint = QLabel("Every drive model reads audio a few samples early or late. Correcting for it is what "
                      "makes AccurateRip verification possible. Look yours up once; Spindle remembers it per drive.")
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        f.addRow("", hint)
        self.paranoia = QComboBox()
        for k, lbl in (("full", "Secure – full paranoia (recommended)"),
                       ("no-extra", "Burst-ish – paranoia without extra verification"),
                       ("off", "Fast – no paranoia (not recommended)")):
            self.paranoia.addItem(lbl, k)
        self.paranoia.setCurrentIndex(max(0, self.paranoia.findData(d["paranoia"])))
        f.addRow("Read mode", self.paranoia)
        self.retries = QSpinBox()
        self.retries.setRange(1, 200)
        self.retries.setValue(int(d["max_retries"]))
        f.addRow("Max re-reads per bad sector", self.retries)
        self.tnc = QCheckBox("Test && copy (read twice and compare CRCs when AccurateRip can't verify)")
        self.tnc.setChecked(d["test_and_copy"])
        f.addRow("", self.tnc)
        self.rereads = QSpinBox()
        self.rereads.setRange(0, 10)
        self.rereads.setValue(int(d["retry_on_mismatch"]))
        f.addRow("Extra passes on CRC mismatch", self.rereads)
        self.autoread = QCheckBox("Read disc and look up metadata as soon as a CD is inserted")
        self.autoread.setChecked(d["auto_read"])
        f.addRow("", self.autoread)
        self.ejectdone = QCheckBox("Eject when the rip is finished")
        self.ejectdone.setChecked(d["eject_when_done"])
        f.addRow("", self.ejectdone)
        self.tabs.addTab(w, "Drive")

    def _detect_offset(self):
        dev = self.device.currentData()
        drive = next((x for x in list_drives() if x.device == dev), None) if dev else self.drive
        if not drive or not drive.model:
            QMessageBox.information(self, "Offset", "Couldn't identify the drive model.")
            return

        self.b_detect.setEnabled(False)
        self.b_detect.setText("Looking up…")
        self.detect_spin.start()

        def idle():
            self.b_detect.setEnabled(True)
            self.b_detect.setText("Look up in AccurateRip")
            self.detect_spin.stop()

        def done(val):
            idle()
            if val is None:
                QMessageBox.information(self, "Offset", f"{drive.accuraterip_name} isn't in the AccurateRip "
                                        "offset list. Search for it at accuraterip.com/driveoffsets.htm.")
            else:
                self.offset.setValue(val)
                QMessageBox.information(self, "Offset", f"{drive.accuraterip_name}: {val:+d} samples")
        run_async(accuraterip.detect_offset, drive, on_done=done,
                  on_error=lambda m: (idle(), QMessageBox.warning(self, "Offset", f"Lookup failed: {m}")))

    # --- Output ------------------------------------------------------------
    def _output_tab(self):
        o = self.cfg["output"]
        w = QWidget()
        f = QFormLayout(w)
        row = QHBoxLayout()
        self.outdir = QLineEdit(o["directory"])
        b = QPushButton("Browse…")
        b.clicked.connect(lambda: self._browse(self.outdir))
        row.addWidget(self.outdir, 1)
        row.addWidget(b)
        f.addRow("Music folder", row)
        mode = QHBoxLayout()
        self.mode_tracks = QRadioButton("One file per track")
        self.mode_image = QRadioButton("Single file image + CUE")
        (self.mode_image if o["mode"] == "image" else self.mode_tracks).setChecked(True)
        g = QButtonGroup(w)
        g.addButton(self.mode_tracks)
        g.addButton(self.mode_image)
        mode.addWidget(self.mode_tracks)
        mode.addWidget(self.mode_image)
        mode.addStretch(1)
        f.addRow("Layout", mode)
        self.tmpl = QLineEdit(o["template"])
        self.va_tmpl = QLineEdit(o["va_template"])
        self.img_tmpl = QLineEdit(o["image_template"])
        f.addRow("File naming", self.tmpl)
        f.addRow("Various artists", self.va_tmpl)
        f.addRow("Image naming", self.img_tmpl)
        self.preview = QLabel()
        self.preview.setObjectName("dim")
        self.preview.setWordWrap(True)
        f.addRow("Preview", self.preview)
        help_ = QLabel("Fields: " + " ".join(f"%{x}%" for x in naming.FIELDS) +
                       "<br>Use / for folders. Text in [brackets] disappears when a field inside it is empty.")
        help_.setWordWrap(True)
        help_.setObjectName("dim")
        f.addRow("", help_)
        for e in (self.tmpl, self.va_tmpl, self.img_tmpl):
            e.textChanged.connect(self._update_preview)
        self.safe = QCheckBox("Windows/FAT-safe file names")
        self.safe.setChecked(o["safe_filenames"])
        f.addRow("", self.safe)
        self.overwrite = QCheckBox("Overwrite existing files (otherwise add a number)")
        self.overwrite.setChecked(o["overwrite"])
        f.addRow("", self.overwrite)
        ex = self.cfg["extras"]
        self.x_log = QCheckBox("Extraction log (.log)")
        self.x_cue = QCheckBox("Cue sheet (.cue)")
        self.x_m3u = QCheckBox("Playlist (.m3u8)")
        self.x_rg = QCheckBox("ReplayGain 2.0 tags (track + album)")
        for cb, k in ((self.x_log, "write_log"), (self.x_cue, "write_cue"), (self.x_m3u, "write_m3u"),
                      (self.x_rg, "replaygain")):
            cb.setChecked(ex[k])
            f.addRow("" if k != "write_log" else "Also write", cb)
        self._update_preview()
        self.tabs.addTab(w, "Output")

    def _update_preview(self):
        a = AlbumMeta(title="OK Computer", artist="Radiohead", date="1997")
        t = TrackMeta(2, "Paranoid Android", "Radiohead")
        try:
            p = naming.render(self.tmpl.text(), naming.values(a, t, 12))
            unknown = naming.unknown_fields(self.tmpl.text() + self.va_tmpl.text() + self.img_tmpl.text())
            warn = ("<br><span style='color:#e5534b'>Unknown field(s), will be left out: "
                    + ", ".join(f"%{u}%" for u in unknown) + "</span>") if unknown else ""
            self.preview.setText(p + ".flac" + warn)
        except Exception as e:  # noqa: BLE001
            self.preview.setText(str(e))

    def _browse(self, edit):
        d = QFileDialog.getExistingDirectory(self, "Choose folder", os.path.expanduser(edit.text()))
        if d:
            edit.setText(d)

    # --- Formats -----------------------------------------------------------
    def _formats_tab(self):
        w = QWidget()
        lay = QHBoxLayout(w)
        left = QVBoxLayout()
        left.addWidget(QLabel("Encode to (tick one or more):"))
        self.fmt_list = QListWidget()
        avail = encoders.availability()
        chosen = set(self.cfg["output"]["formats"])
        for key, fm in encoders.FORMATS.items():
            how = avail[key]
            label = f"{fm.label}   [{'built-in' if how == 'copy' else how or 'not installed'}]"
            it = QListWidgetItem(label)
            it.setData(Qt.UserRole, key)
            it.setFlags(it.flags() | Qt.ItemIsUserCheckable)
            it.setCheckState(Qt.Checked if key in chosen else Qt.Unchecked)
            if how is None:
                it.setFlags(it.flags() & ~Qt.ItemIsEnabled)
                it.setToolTip(f"Install {fm.native or 'ffmpeg'} or ffmpeg to enable this format")
            self.fmt_list.addItem(it)
        left.addWidget(self.fmt_list, 1)
        par = QHBoxLayout()
        par.addWidget(QLabel("Parallel encoders"))
        self.parallel = QSpinBox()
        self.parallel.setRange(1, 16)
        self.parallel.setValue(int(self.cfg["output"]["parallel_encoders"]))
        par.addWidget(self.parallel)
        left.addLayout(par)
        note = QLabel("When you pick several formats, lossy copies go in their own folder "
                      "(e.g. MP3/Artist/Album) next to the lossless files.")
        note.setWordWrap(True)
        note.setObjectName("dim")
        left.addWidget(note)
        lay.addLayout(left, 1)
        self.fmt_stack = QStackedWidget()
        self.enc_widgets: dict[str, dict] = {}
        for key in encoders.FORMATS:
            self.fmt_stack.addWidget(self._enc_page(key))
        box = QGroupBox("Encoder options")
        bl = QVBoxLayout(box)
        bl.addWidget(self.fmt_stack)
        bl.addStretch(1)
        lay.addWidget(box, 1)
        self.fmt_list.currentRowChanged.connect(self.fmt_stack.setCurrentIndex)
        self.fmt_list.setCurrentRow(0)
        self.tabs.addTab(w, "Formats")

    def _enc_page(self, key):
        o = self.cfg["encoders"].get(key, {})
        page = QWidget()
        f = QFormLayout(page)
        wd: dict = {}

        def spin(name, lo, hi, val, label, suffix=""):
            s = QSpinBox()
            s.setRange(lo, hi)
            s.setValue(int(val))
            if suffix:
                s.setSuffix(suffix)
            f.addRow(label, s)
            wd[name] = s
        if key == "flac":
            spin("level", 0, 8, o.get("level", 8), "Compression level")
            c = QCheckBox("Verify while encoding")
            c.setChecked(o.get("verify", True))
            f.addRow("", c)
            wd["verify"] = c
        elif key == "mp3":
            m = QComboBox()
            m.addItem("VBR (recommended)", "vbr")
            m.addItem("CBR", "cbr")
            m.setCurrentIndex(0 if o.get("mode", "vbr") == "vbr" else 1)
            f.addRow("Mode", m)
            wd["mode"] = m
            spin("vbr_quality", 0, 9, o.get("vbr_quality", 0), "VBR quality (V0 = best)")
            b = QComboBox()
            for br in (128, 160, 192, 224, 256, 320):
                b.addItem(f"{br} kbps", br)
            b.setCurrentIndex(max(0, b.findData(o.get("bitrate", 320))))
            f.addRow("CBR bitrate", b)
            wd["bitrate"] = b
            c = QCheckBox("Write ID3v2.3 (for old players) instead of v2.4")
            c.setChecked(o.get("id3v23", False))
            f.addRow("", c)
            wd["id3v23"] = c
        elif key == "opus":
            spin("bitrate", 32, 510, o.get("bitrate", 160), "Bitrate", " kbps")
        elif key == "vorbis":
            spin("quality", -1, 10, o.get("quality", 6), "Quality")
        elif key == "aac":
            spin("bitrate", 64, 512, o.get("bitrate", 256), "Bitrate", " kbps")
        elif key == "wavpack":
            m = QComboBox()
            for k in ("fast", "normal", "high", "extra"):
                m.addItem(k.title(), k)
            m.setCurrentIndex(max(0, m.findData(o.get("mode", "high"))))
            f.addRow("Compression", m)
            wd["mode"] = m
        else:
            lbl = QLabel("No options.")
            lbl.setObjectName("dim")
            f.addRow(lbl)
        self.enc_widgets[key] = wd
        return page

    # --- Metadata ----------------------------------------------------------
    def _meta_tab(self):
        m = self.cfg["metadata"]
        w = QWidget()
        f = QFormLayout(w)
        names = [c.name for c in metadata.all_metadata_classes()]
        self.meta_list = ProviderList(names, m["providers"], m["disabled"])
        f.addRow("Sources (in order)", self.meta_list)
        self.auto_single = QCheckBox("Use the release automatically when there's only one match")
        self.auto_single.setChecked(m["auto_pick_single"])
        f.addRow("", self.auto_single)
        self.country = QLineEdit(m["prefer_country"])
        self.country.setMaxLength(2)
        self.country.setPlaceholderText("e.g. US")
        f.addRow("Preferred release country", self.country)
        self.mbids = QCheckBox("Write MusicBrainz IDs (lets Picard/beets/Navidrome recognise the files)")
        self.mbids.setChecked(m["write_mb_ids"])
        f.addRow("", self.mbids)
        self.gnudb = QLineEdit(m["gnudb_user"])
        f.addRow("GnuDB user name", self.gnudb)
        self.contact = QLineEdit(m["contact"])
        self.contact.setPlaceholderText("optional e-mail or URL sent with MusicBrainz requests")
        f.addRow("Contact", self.contact)
        self.acoustid = QLineEdit(m.get("acoustid_key", ""))
        self.acoustid.setPlaceholderText("optional – lets Tag Files identify untagged files by their audio")
        f.addRow("AcoustID API key", self.acoustid)
        from .. import acoustid as _ac
        hint = QLabel('Get a free key at <a href="https://acoustid.org/new-application">acoustid.org</a>. '
                      + ("Fingerprinting tool found." if _ac.available() else
                         "Also needs the fpcalc tool (Fedora: <i>sudo dnf install chromaprint-tools</i>)."))
        hint.setOpenExternalLinks(True)
        hint.setWordWrap(True)
        hint.setObjectName("dim")
        f.addRow("", hint)
        self.tabs.addTab(w, "Metadata")

    def _cover_tab(self):
        c = self.cfg["coverart"]
        w = QWidget()
        f = QFormLayout(w)
        names = [k.name for k in metadata.all_cover_classes()]
        self.cover_list = ProviderList(names, c["providers"], c["disabled"])
        f.addRow("Sources (in order)", self.cover_list)
        self.c_auto = QCheckBox("Find artwork automatically after looking up the disc")
        self.c_auto.setChecked(c["auto_fetch"])
        f.addRow("", self.c_auto)
        self.c_embed = QCheckBox("Embed artwork in every file")
        self.c_embed.setChecked(c["embed"])
        f.addRow("", self.c_embed)
        self.c_max = QSpinBox()
        self.c_max.setRange(0, 4000)
        self.c_max.setSingleStep(100)
        self.c_max.setValue(int(c["embed_max_px"]))
        self.c_max.setSpecialValueText("original size")
        self.c_max.setSuffix(" px")
        f.addRow("Shrink embedded copy to", self.c_max)
        self.c_save = QCheckBox("Save full-size image in the album folder as")
        self.c_save.setChecked(c["save_file"])
        self.c_name = QLineEdit(c["filename"])
        r = QHBoxLayout()
        r.addWidget(self.c_save)
        r.addWidget(self.c_name)
        f.addRow("", r)
        self.c_min = QSpinBox()
        self.c_min.setRange(0, 3000)
        self.c_min.setSingleStep(100)
        self.c_min.setValue(int(c["min_px"]))
        self.c_min.setSuffix(" px")
        f.addRow("Prefer images at least", self.c_min)
        self.tabs.addTab(w, "Cover art")

    # --- Plugins -----------------------------------------------------------
    def _plugins_tab(self):
        w = QWidget()
        lay = QVBoxLayout(w)
        top = QHBoxLayout()
        pdir = DATA_DIR / "plugins"
        lbl = QLabel(f"Plugin folder: {pdir}")
        lbl.setObjectName("dim")
        lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(lbl, 1)
        ob = QPushButton("Open folder")

        def openit():
            pdir.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdir)))
        ob.clicked.connect(openit)
        top.addWidget(ob)
        lay.addLayout(top)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        inner = QWidget()
        il = QVBoxLayout(inner)
        self.plugin_fields: dict[tuple[str, str], QLineEdit] = {}
        seen = set()
        for cls in metadata.all_metadata_classes() + metadata.all_cover_classes():
            if not getattr(cls, "settings", None):
                continue
            for key, (label, default) in cls.settings.items():
                if (cls.name, key) in seen:
                    continue
                seen.add((cls.name, key))
                box = next((b for b in inner.findChildren(QGroupBox) if b.title() == cls.name), None)
                if box is None:
                    box = QGroupBox(cls.name)
                    QFormLayout(box)
                    il.addWidget(box)
                e = QLineEdit(str(self.cfg.plugin(cls.name).get(key, default)))
                if "token" in key or "key" in key or "secret" in key:
                    e.setEchoMode(QLineEdit.PasswordEchoOnEdit)
                box.layout().addRow(label, e)
                self.plugin_fields[(cls.name, key)] = e
        if metadata.plugin_errors:
            err = QLabel("Plugins that failed to load:\n" + "\n".join(metadata.plugin_errors))
            err.setWordWrap(True)
            il.addWidget(err)
        info = QLabel("A plugin is a .py file that subclasses MetadataProvider or CoverProvider "
                      "(see discogs.py in the plugin folder). Restart Spindle after adding one.")
        info.setWordWrap(True)
        info.setObjectName("dim")
        il.addWidget(info)
        il.addStretch(1)
        scroll.setWidget(inner)
        lay.addWidget(scroll, 1)
        self.tabs.addTab(w, "Plugins")

    def _look_tab(self):
        w = QWidget()
        f = QFormLayout(w)
        self.theme = QComboBox()
        self.theme.addItems(theme_names())
        self.theme.setCurrentText(self.cfg["ui"]["theme"])
        self.theme.currentTextChanged.connect(lambda t: self.parent() and self.parent().apply_theme(t))
        f.addRow("Skin", self.theme)
        self.tabs.addTab(w, "Appearance")

    # --- save --------------------------------------------------------------
    def _save(self):
        c = self.cfg
        d = c["drive"]
        d.update(device=self.device.currentData(), read_offset=self.offset.value(),
                 paranoia=self.paranoia.currentData(), max_retries=self.retries.value(),
                 test_and_copy=self.tnc.isChecked(), retry_on_mismatch=self.rereads.value(),
                 auto_read=self.autoread.isChecked(), eject_when_done=self.ejectdone.isChecked())
        drv = self.drive
        dev = self.device.currentData()
        if dev:
            drv = next((x for x in list_drives() if x.device == dev), drv)
        if drv and drv.model:
            d.setdefault("offsets_by_model", {})[drv.accuraterip_name] = self.offset.value()
        fmts = [self.fmt_list.item(i).data(Qt.UserRole) for i in range(self.fmt_list.count())
                if self.fmt_list.item(i).checkState() == Qt.Checked]
        if not fmts:
            QMessageBox.warning(self, "Settings", "Pick at least one output format.")
            self.tabs.setCurrentIndex(2)
            return
        c["output"].update(directory=self.outdir.text().strip() or os.path.expanduser("~/Music"),
                           mode="image" if self.mode_image.isChecked() else "tracks",
                           template=self.tmpl.text(), va_template=self.va_tmpl.text(),
                           image_template=self.img_tmpl.text(), safe_filenames=self.safe.isChecked(),
                           overwrite=self.overwrite.isChecked(), formats=fmts,
                           parallel_encoders=self.parallel.value())
        for key, wd in self.enc_widgets.items():
            o = c["encoders"].setdefault(key, {})
            for name, widget in wd.items():
                if isinstance(widget, QSpinBox):
                    o[name] = widget.value()
                elif isinstance(widget, QCheckBox):
                    o[name] = widget.isChecked()
                elif isinstance(widget, QComboBox):
                    o[name] = widget.currentData()
        c["extras"].update(write_log=self.x_log.isChecked(), write_cue=self.x_cue.isChecked(),
                           write_m3u=self.x_m3u.isChecked(), replaygain=self.x_rg.isChecked())
        c["metadata"].update(providers=self.meta_list.order(), disabled=self.meta_list.disabled(),
                             auto_pick_single=self.auto_single.isChecked(),
                             prefer_country=self.country.text().strip().upper(),
                             write_mb_ids=self.mbids.isChecked(), gnudb_user=self.gnudb.text().strip(),
                             contact=self.contact.text().strip(), acoustid_key=self.acoustid.text().strip())
        c["coverart"].update(providers=self.cover_list.order(), disabled=self.cover_list.disabled(),
                             auto_fetch=self.c_auto.isChecked(), embed=self.c_embed.isChecked(),
                             embed_max_px=self.c_max.value(), save_file=self.c_save.isChecked(),
                             filename=self.c_name.text().strip() or "cover.jpg", min_px=self.c_min.value())
        for (name, key), e in self.plugin_fields.items():
            c.plugin(name)[key] = e.text().strip()
        c["ui"]["theme"] = self.theme.currentText()
        net.contact = c["metadata"]["contact"]
        c.save()
        self.accept()
