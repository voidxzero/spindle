"""Update notification bar and the update dialog."""
from __future__ import annotations

import sys

from PySide6.QtCore import QProcess, Signal
from PySide6.QtWidgets import (QApplication, QDialog, QDialogButtonBox, QFrame, QHBoxLayout, QLabel,
                               QPlainTextEdit, QPushButton, QTextBrowser, QToolButton, QVBoxLayout)

from .. import updater
from .widgets import Spinner
from .workers import run_async


class UpdateBar(QFrame):
    """Slim bar across the top of the window: 'Spindle X is available'."""
    details = Signal()
    skip = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("updateBar")
        self.setFrameShape(QFrame.StyledPanel)
        h = QHBoxLayout(self)
        h.setContentsMargins(10, 4, 4, 4)
        h.setSpacing(8)
        self.label = QLabel()
        h.addWidget(self.label, 1)
        self.b_update = QPushButton("Update…")
        self.b_update.setDefault(True)
        self.b_update.clicked.connect(self.details.emit)
        b_skip = QPushButton("Skip this version")
        b_skip.clicked.connect(self.skip.emit)
        b_close = QToolButton()
        b_close.setText("✕")
        b_close.setAutoRaise(True)
        b_close.setToolTip("Hide until next start")
        b_close.clicked.connect(self.hide)
        for w in (self.b_update, b_skip, b_close):
            h.addWidget(w)
        self.hide()

    def show_for(self, info: updater.UpdateInfo):
        self.label.setText(f"<b>Spindle {info.latest}</b> is available - you have {info.current}.")
        self.show()


def restart_spindle():
    """Start a fresh Spindle (the newly installed code) and close this one."""
    lib = updater.installed_lib()
    code = ("import sys; sys.path.insert(0, %r); from spindle.gui.app import main; "
            "sys.exit(main(sys.argv[1:]))" % str(lib)) if lib else \
        "import sys; from spindle.gui.app import main; sys.exit(main(sys.argv[1:]))"
    QProcess.startDetached(sys.executable, ["-c", code, *sys.argv[1:]])
    for w in QApplication.topLevelWidgets():
        if w.objectName() == "SpindleMain":
            w.close()
    QApplication.quit()


class UpdateDialog(QDialog):
    """What's new, then download + install with a live log, then restart."""

    def __init__(self, info: updater.UpdateInfo, busy=lambda: False, parent=None):
        super().__init__(parent)
        self.info, self.busy = info, busy
        self.setWindowTitle("Update Spindle")
        self.resize(560, 460)
        v = QVBoxLayout(self)
        v.setSpacing(10)
        head = QLabel(f"<h3 style='margin:0'>Spindle {info.latest} is available</h3>"
                      f"You have {info.current}. Updates come from "
                      f"<a href='https://github.com/{info.repo}'>github.com/{info.repo}</a>.")
        head.setOpenExternalLinks(True)
        head.setWordWrap(True)
        v.addWidget(head)
        v.addWidget(QLabel("What's new:"))
        self.notes = QTextBrowser()
        self.notes.setOpenExternalLinks(True)
        self.notes.setMarkdown(info.notes or "_No changelog for this version._")
        v.addWidget(self.notes, 1)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.hide()
        v.addWidget(self.log_view, 1)
        row = QHBoxLayout()
        self.spin = Spinner(18)
        self.status = QLabel()
        self.status.setWordWrap(True)
        row.addWidget(self.spin)
        row.addWidget(self.status, 1)
        v.addLayout(row)
        self.buttons = QDialogButtonBox()
        self.b_go = self.buttons.addButton("Update now", QDialogButtonBox.AcceptRole)
        self.b_later = self.buttons.addButton("Later", QDialogButtonBox.RejectRole)
        self.buttons.accepted.connect(self._start)
        self.buttons.rejected.connect(self.reject)
        v.addWidget(self.buttons)
        self.installed = False
        if updater.installed_lib() is None:
            self.b_go.setEnabled(False)
            self.status.setText("You're running Spindle from a source folder, so it can't update itself. "
                                "In that folder run:  git pull && ./install.sh")
        elif busy():
            self.status.setText("Finish or cancel the current rip first - Spindle restarts after updating.")
            self.b_go.setEnabled(False)

    def _log(self, line: str):
        self.log_view.appendPlainText(line)

    def _start(self):
        if self.installed:
            restart_spindle()
            return
        if self.busy():
            self.status.setText("Finish or cancel the current rip first - Spindle restarts after updating.")
            return
        self.b_go.setEnabled(False)
        self.b_later.setEnabled(False)
        self.notes.hide()
        self.log_view.show()
        self.spin.start()
        self.status.setText("Downloading and installing… (your settings are kept)")
        from PySide6.QtCore import QObject, Signal as _Sig

        class _Pipe(QObject):
            line = _Sig(str)
        self._pipe = _Pipe()
        self._pipe.line.connect(self._log)
        run_async(updater.install, self.info.repo, self.info.latest, self._pipe.line.emit,
                  on_done=self._done, on_error=self._failed)

    def _done(self, version: str):
        self.spin.stop()
        self.installed = True
        self.status.setText(f"<b>Spindle {version} is installed.</b> Spindle will restart to finish updating.")
        self.b_go.setText("Restart now")
        self.b_go.setEnabled(True)
        self.b_later.hide()                     # the new files are in place: running on would mix versions

    def reject(self):
        if self.installed:
            restart_spindle()
            return
        if self.b_later.isEnabled():
            super().reject()

    def _failed(self, msg: str):
        self.spin.stop()
        self.status.setText(f"Update failed: {msg}. The log above has the details.")
        self.b_go.setText("Try again")
        self.b_go.setEnabled(True)
        self.b_later.setText("Close")
        self.b_later.setEnabled(True)
