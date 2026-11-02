"""Background execution helpers for the Qt front-end."""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal

from ..pipeline import Reporter


class _Signals(QObject):
    done = Signal(object)
    failed = Signal(str)


class _Runnable(QRunnable):
    def __init__(self, fn, args, kwargs):
        super().__init__()
        self.fn, self.args, self.kwargs = fn, args, kwargs
        self.signals = _Signals()
        self.setAutoDelete(True)

    def run(self):
        try:
            result = self.fn(*self.args, **self.kwargs)
        except Exception as e:  # noqa: BLE001
            msg = str(e) or e.__class__.__name__
            from .. import crashlog
            crashlog.write(f"Background task {getattr(self.fn, '__name__', self.fn)} failed:\n"
                           + traceback.format_exc())
            self.signals.failed.emit(msg)
        else:
            self.signals.done.emit(result)


_alive: set[_Signals] = set()


def run_async(fn, *args, on_done=None, on_error=None, **kwargs) -> None:
    """Run fn(*args) on the thread pool; callbacks fire on the GUI thread."""
    r = _Runnable(fn, args, kwargs)
    sig = r.signals
    _alive.add(sig)

    # Keep the messenger alive until its callback has completely finished - callbacks can open windows
    # (Choose release…) that run for a while inside the signal - and let it go only afterwards.
    def _release():
        QTimer.singleShot(0, lambda: _alive.discard(sig))

    def _done(res):
        try:
            if on_done:
                on_done(res)
        finally:
            _release()

    def _fail(msg):
        try:
            if on_error:
                on_error(msg)
        finally:
            _release()
    sig.done.connect(_done)
    sig.failed.connect(_fail)
    QThreadPool.globalInstance().start(r)


class QtReporter(QObject, Reporter):
    logged = Signal(str)
    status = Signal(int, str)
    progress = Signal(int, float)
    overall_progress = Signal(float, str)
    finished_track = Signal(int, object)

    def log(self, msg):
        self.logged.emit(msg)

    def track_status(self, n, text):
        self.status.emit(n, text)

    def track_progress(self, n, frac):
        self.progress.emit(n, float(frac))

    def overall(self, frac, text=""):
        self.overall_progress.emit(float(frac), text)

    def track_done(self, n, result):
        self.finished_track.emit(n, result)
