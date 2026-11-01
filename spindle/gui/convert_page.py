"""The Convert tab: turn music you already have into another format, keeping tags and cover art."""
from __future__ import annotations

import os

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu, QProgressBar,
                               QPushButton, QRadioButton, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import convert, encoders
from .widgets import PROGRESS_ROLE, STATE_ROLE, Spinner, StatusDelegate, unify_heights
from .workers import run_async

C_FILE, C_FOLDER, C_FORMAT, C_LEN, C_STATUS = range(5)


def _fmt_len(sec: float) -> str:
    return f"{int(sec // 60)}:{int(sec % 60):02d}" if sec else ""


class _Bridge(QObject):
    status = Signal(int, str, str)


class ConvertPage(QWidget):
    logged = Signal(str)

    def __init__(self, cfg, colors: dict, open_settings=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.items: list[convert.Item] = []
        self.job: convert.ConvertJob | None = None
        self.open_settings = open_settings or (lambda tab: None)
        self._bridge = _Bridge()
        self._bridge.status.connect(self._on_status)
        self._scanning = 0
        self.setAcceptDrops(True)
        self._build(colors)
        self._load_choices()

    # ================================================================ build
    def _build(self, colors):
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        bar = QHBoxLayout()
        bar.setSpacing(6)

        def btn(text, slot, tip=""):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)
            return b
        self.b_add_folder = btn("Add Folder…", self.add_folder,
                                "Add a folder of music (subfolders included). You can also drag files and folders here.")
        self.b_add_files = btn("Add Files…", self.add_files)
        bar.addSpacing(12)
        self.b_remove = btn("Remove", self.remove_selected, "Remove the selected files from the list (files are not touched)")
        self.b_clear = btn("Clear", self.clear_all)
        bar.addStretch(1)
        self.spinner = Spinner(16)
        bar.addWidget(self.spinner)
        self.count = QLabel("")
        self.count.setObjectName("dim")
        bar.addWidget(self.count)
        root.addLayout(bar)

        self.stack = QStackedWidget()
        empty = QLabel("Drop music files or folders here\n\nor use “Add Folder…”.\n\nSpindle converts them to the "
                       "format you choose below and copies their tags and cover art.\nYour original files are never changed.")
        empty.setAlignment(Qt.AlignCenter)
        empty.setObjectName("dim")
        empty.setWordWrap(True)
        self.stack.addWidget(empty)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["File", "Folder", "Format", "Length", "Status"])
        self.table.verticalHeader().hide()
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(C_FILE, QHeaderView.Stretch)
        h.setSectionResizeMode(C_FOLDER, QHeaderView.Stretch)
        for c, w in ((C_FORMAT, 80), (C_LEN, 70), (C_STATUS, 220)):
            h.setSectionResizeMode(c, QHeaderView.Interactive)
            self.table.setColumnWidth(c, w)
        self.delegate = StatusDelegate(colors, self.table)
        self.table.setItemDelegateForColumn(C_STATUS, self.delegate)
        self.stack.addWidget(self.table)
        root.addWidget(self.stack, 1)

        # ---- options
        box = QFrame()
        box.setObjectName("header")
        g = QGridLayout(box)
        g.setContentsMargins(12, 10, 12, 10)
        g.setHorizontalSpacing(10)
        g.setVerticalSpacing(8)

        def lab(t):
            x = QLabel(t)
            x.setObjectName("fieldLabel")
            return x
        self.fmt = QComboBox()
        avail = encoders.availability()
        for key, f in encoders.FORMATS.items():
            self.fmt.addItem(f.label + ("" if avail.get(key) else "  (encoder not installed)"), key)
            if not avail.get(key):
                self.fmt.model().item(self.fmt.count() - 1).setEnabled(False)
        self.fmt.currentIndexChanged.connect(self._choices_changed)
        self.fmt_desc = QLabel("")
        self.fmt_desc.setObjectName("dim")
        self.b_options = QPushButton("Encoder options…")
        self.b_options.setToolTip("The same settings as for ripping (Settings ▸ Formats)")
        self.b_options.clicked.connect(lambda: (self.open_settings("Formats"), self._choices_changed()))
        frow = QHBoxLayout()
        frow.setSpacing(8)
        frow.addWidget(self.fmt)
        frow.addWidget(self.b_options)
        frow.addWidget(self.fmt_desc, 1)
        g.addWidget(lab("Convert to"), 0, 0)
        g.addLayout(frow, 0, 1)

        self.r_beside = QRadioButton("Next to the originals")
        self.r_folder = QRadioButton("In this folder:")
        grp = QButtonGroup(self)
        grp.addButton(self.r_beside)
        grp.addButton(self.r_folder)
        self.r_beside.toggled.connect(self._choices_changed)
        self.folder = QLineEdit()
        self.folder.setPlaceholderText("Choose a folder - subfolders are kept (Album/01.flac → folder/Album/01.mp3)")
        self.folder.textChanged.connect(lambda _: self.r_folder.setChecked(True) if self.folder.hasFocus() else None)
        self.b_browse = QPushButton("Browse…")
        self.b_browse.clicked.connect(self._browse)
        drow = QHBoxLayout()
        drow.setSpacing(8)
        drow.addWidget(self.r_beside)
        drow.addSpacing(8)
        drow.addWidget(self.r_folder)
        drow.addWidget(self.folder, 1)
        drow.addWidget(self.b_browse)
        g.addWidget(lab("Save to"), 1, 0)
        g.addLayout(drow, 1, 1)

        self.c_tags = QCheckBox("Copy tags and cover art")
        self.c_overwrite = QCheckBox("Replace files that already exist (otherwise they're skipped)")
        orow = QHBoxLayout()
        orow.setSpacing(16)
        orow.addWidget(self.c_tags)
        orow.addWidget(self.c_overwrite)
        orow.addStretch(1)
        g.addLayout(orow, 2, 1)
        self.hint = QLabel("")
        self.hint.setObjectName("dim")
        self.hint.setWordWrap(True)
        self.hint.setMinimumWidth(420)
        g.addWidget(self.hint, 3, 1)
        g.setColumnStretch(1, 1)
        root.addWidget(box)

        # ---- progress + go
        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.prog_label = QLabel("Ready")
        self.prog_label.setObjectName("lcd")
        self.prog_label.setMinimumWidth(320)
        self.prog = QProgressBar()
        self.prog.setRange(0, 1000)
        self.prog.setFormat("%p%")
        self.b_go = QPushButton("Convert")
        self.b_go.setObjectName("primary")
        self.b_go.clicked.connect(self._go_or_cancel)
        prow.addWidget(self.prog_label)
        prow.addWidget(self.prog, 1)
        prow.addWidget(self.b_go)
        root.addLayout(prow)
        self._update_counts()

    def unify(self):
        unify_heights([self.fmt, self.b_options, self.folder, self.b_browse], reference=self.folder)

    def set_colors(self, colors: dict):
        self.delegate.colors = colors
        self.table.viewport().update()

    # ============================================================ choices
    def _load_choices(self):
        c = self.cfg["convert"]
        i = self.fmt.findData(c.get("format", "mp3"))
        if i < 0 or not self.fmt.model().item(i).isEnabled():
            i = next((j for j in range(self.fmt.count()) if self.fmt.model().item(j).isEnabled()), 0)
        self.fmt.setCurrentIndex(i)
        self.folder.setText(c.get("folder", ""))
        (self.r_folder if c.get("folder") else self.r_beside).setChecked(True)
        self.c_tags.setChecked(c.get("copy_tags", True))
        self.c_overwrite.setChecked(c.get("overwrite", False))
        self._choices_changed()

    def _save_choices(self):
        self.cfg["convert"].update(format=self.fmt.currentData(), copy_tags=self.c_tags.isChecked(),
                                   overwrite=self.c_overwrite.isChecked(),
                                   folder=self.folder.text().strip() if self.r_folder.isChecked() else "")
        self.cfg.save()

    def _choices_changed(self, *_):
        key = self.fmt.currentData()
        if key:
            self.fmt_desc.setText(encoders.describe(key, self.cfg["encoders"].get(key, {})))
        self._update_hint()

    def _browse(self):
        start = self.folder.text().strip() or os.path.expanduser(self.cfg["output"]["directory"])
        d = QFileDialog.getExistingDirectory(self, "Save converted files in", start)
        if d:
            self.folder.setText(d)
            self.r_folder.setChecked(True)

    def _update_hint(self):
        key = self.fmt.currentData()
        notes = []
        if key and encoders.FORMATS[key].lossless:
            lossy = sorted({convert.label(it.fmt) for it in self.items
                            if it.fmt in encoders.FORMATS and not encoders.FORMATS[it.fmt].lossless})
            if lossy:
                notes.append(f"Converting {' and '.join(lossy)} to {convert.label(key)} won't bring back quality "
                             "the lossy files have already lost - the files just get bigger.")
        same = sum(1 for it in self.items if it.fmt == key)
        if same and self.r_beside.isChecked():
            notes.append(f"{same} file(s) are already {convert.label(key)} and will be skipped "
                         "(they'd replace themselves). Choose a folder to re-encode them.")
        self.hint.setText("  ".join(notes))
        self.hint.setVisible(bool(notes))

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
        exts = " ".join(f"*{e}" for e in sorted(convert.AUDIO_EXT))
        paths, _ = QFileDialog.getOpenFileNames(self, "Add music files", os.path.expanduser(self.cfg["output"]["directory"]),
                                                f"Audio files ({exts})")
        if paths:
            self.add_paths(paths)

    def add_paths(self, paths: list[str]):
        if self.job:
            return
        known = {it.src for it in self.items}
        self._scanning += 1
        self.spinner.start()
        self.count.setText("Reading files…")

        def scan():
            found = []
            for src, base in convert.list_audio(paths):
                if src in known:
                    continue
                known.add(src)
                fmt, length, bits = convert.probe(src)
                found.append(convert.Item(src, base, fmt, length, bits))
            return found

        def done(found):
            self._scanning -= 1
            if not self._scanning:
                self.spinner.stop()
            for it in found:
                self.items.append(it)
                self._add_row(it)
            self._update_counts()
            self._update_hint()
            if not found:
                self.logged.emit("Convert: no new audio files found there.")

        def fail(msg):
            self._scanning -= 1
            if not self._scanning:
                self.spinner.stop()
            self._update_counts()
            self.logged.emit(f"Convert: couldn't read the files: {msg}")
        run_async(scan, on_done=done, on_error=fail)

    def _add_row(self, it: convert.Item):
        r = self.table.rowCount()
        self.table.insertRow(r)
        cells = (os.path.basename(it.src), os.path.dirname(it.src), convert.label(it.fmt), _fmt_len(it.length), "")
        for c, text in enumerate(cells):
            item = QTableWidgetItem(text)
            if c == C_FOLDER:
                item.setToolTip(text)
            if c == C_LEN:
                item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, c, item)

    def remove_selected(self):
        if self.job:
            return
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)
            del self.items[r]
        self._update_counts()
        self._update_hint()

    def clear_all(self):
        if self.job:
            return
        self.items.clear()
        self.table.setRowCount(0)
        self.prog.setValue(0)
        self.prog_label.setText("Ready")
        self._update_counts()
        self._update_hint()

    def _menu(self, pos):
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        it = self.items[row]
        m = QMenu(self)
        a_open = m.addAction("Open folder of the converted file" if it.state == "good" and it.dst
                             else "Open folder of the original")
        a_rm = m.addAction("Remove from list")
        a_rm.setEnabled(self.job is None)
        chosen = m.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is a_open:
            from PySide6.QtCore import QUrl
            from PySide6.QtGui import QDesktopServices
            path = it.dst if it.state == "good" and it.dst else it.src
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))
        elif chosen is a_rm:
            self.table.selectRow(row)
            self.remove_selected()

    def _update_counts(self):
        n = len(self.items)
        total = sum(it.length for it in self.items)
        if not self._scanning:
            self.count.setText(f"{n} file{'s' if n != 1 else ''}" + (f" · {_fmt_len(total)}" if total < 3600 else
                                                                    f" · {total / 3600:.1f} h") if n else "")
        self.stack.setCurrentIndex(1 if n else 0)
        self.b_go.setEnabled(bool(n) or self.job is not None)

    # ============================================================ converting
    def _go_or_cancel(self):
        if self.job:
            self.job.cancel.set()
            self.prog_label.setText("Cancelling - finishing the files in progress…")
            self.b_go.setEnabled(False)
            return
        self.start()

    def start(self):
        key = self.fmt.currentData()
        if not self.items or not key or self.job:
            return
        folder = self.folder.text().strip() if self.r_folder.isChecked() else None
        if self.r_folder.isChecked() and not folder:
            self.prog_label.setText("Choose a folder to save to first")
            self.folder.setFocus()
            return
        self._save_choices()
        for r, it in enumerate(self.items):
            it.state, it.message = "", ""
            st = self.table.item(r, C_STATUS)
            st.setText("Queued")
            st.setData(STATE_ROLE, "")
            st.setData(PROGRESS_ROLE, None)
            st.setToolTip("")
        self._done = 0
        self.prog.setValue(0)
        self.job = convert.ConvertJob(
            list(self.items), key, self.cfg["encoders"].get(key, {}), folder=folder,
            copy_tags=self.c_tags.isChecked(), overwrite=self.c_overwrite.isChecked(),
            id3v23=self.cfg["encoders"]["mp3"].get("id3v23", False),
            workers=self.cfg["output"].get("parallel_encoders", 2), on_status=self._bridge.status.emit)
        self._set_running(True)
        self.prog_label.setText(f"Converting {len(self.items)} file(s) to {convert.label(key)}…")
        self.logged.emit(f"Convert: {len(self.items)} file(s) to {encoders.describe(key, self.cfg['encoders'].get(key, {}))}"
                         + (f", into {folder}" if folder else ", next to the originals"))
        run_async(self.job.run, on_done=self._finished, on_error=self._failed)

    def _set_running(self, running: bool):
        for w in (self.b_add_folder, self.b_add_files, self.b_remove, self.b_clear, self.fmt, self.b_options,
                  self.r_beside, self.r_folder, self.folder, self.b_browse, self.c_tags, self.c_overwrite):
            w.setEnabled(not running)
        self.b_go.setText("Cancel" if running else "Convert")
        self.b_go.setObjectName("" if running else "primary")
        self.b_go.style().unpolish(self.b_go)
        self.b_go.style().polish(self.b_go)
        self.b_go.setEnabled(True)
        if running:
            self.spinner.start()
        else:
            self.spinner.stop()

    def _on_status(self, i: int, state: str, text: str):
        if not self.job or i >= len(self.job.items):
            return
        it = self.job.items[i]
        try:
            r = self.items.index(it)
        except ValueError:
            return
        st = self.table.item(r, C_STATUS)
        st.setText(text)
        st.setToolTip(text + (f"\n→ {it.dst}" if it.dst else ""))
        if state == "working":
            st.setData(STATE_ROLE, "")
            st.setData(PROGRESS_ROLE, 0.5)     # busy look; encoders don't report progress
            self.table.scrollToItem(st)
            return
        st.setData(PROGRESS_ROLE, None)
        st.setData(STATE_ROLE, {"good": "good", "warn": "warn", "bad": "bad", "skip": "warn"}[state])
        if state == "bad":
            self.logged.emit(f"Convert: {os.path.basename(it.src)}: {text}")
        self._done += 1
        self.prog.setValue(int(1000 * self._done / max(1, len(self.job.items))))

    def _finished(self, counts: dict):
        job, self.job = self.job, None
        self._set_running(False)
        self._update_counts()
        parts = [f"{counts['good']} converted"]
        if counts["skip"]:
            parts.append(f"{counts['skip']} skipped")
        if counts["bad"]:
            parts.append(f"{counts['bad']} failed")
        if counts["cancelled"]:
            parts.append(f"{counts['cancelled']} not converted (cancelled)")
            for r, it in enumerate(self.items):
                if it.state == "" and self.table.item(r, C_STATUS).text() == "Queued":
                    self.table.item(r, C_STATUS).setText("Cancelled")
        self.prog.setValue(1000)
        self.prog_label.setText(" · ".join(parts))
        self.logged.emit("Convert finished: " + ", ".join(parts))

    def _failed(self, msg: str):
        self.job = None
        self._set_running(False)
        self._update_counts()
        self.prog_label.setText(f"Convert failed: {msg}")
        self.logged.emit(f"Convert failed: {msg}")
