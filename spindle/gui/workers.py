"""Background execution helpers for the Qt front-end.

Tasks run on a plain Python thread pool and report back through ONE long-lived QObject. No Qt object is
created or destroyed per task: earlier versions made a small QObject for every task and let Python
clean it up, and on Windows that clean-up could land in the middle of delivering a result, which made
Qt abort (Spindle 1.15.1 crashed that way)."""
from __future__ import annotations

import concurrent.futures as cf
import itertools
import threading
import traceback

from PySide6.QtCore import QObject, Signal

from ..pipeline import Reporter


class _Dispatcher(QObject):
    """Lives as long as Spindle. Worker threads emit; Qt delivers on the GUI thread."""
    finished = Signal(int, bool, object)          # task id, succeeded, result or error message

    def __init__(self):
        super().__init__()
        self.callbacks: dict[int, tuple] = {}
        self.finished.connect(self._deliver)

    def _deliver(self, task: int, ok: bool, payload) -> None:
        on_done, on_error = self.callbacks.pop(task, (None, None))
        cb = on_done if ok else on_error
        if cb is None:
            return
        try:
            cb(payload)
        except Exception:  # noqa: BLE001 - a bug in a callback goes to the log, never into Qt's event loop
            from .. import crashlog
            crashlog.write(f"Error handling the result of a background task:\n{traceback.format_exc()}")


_pool = cf.ThreadPoolExecutor(max_workers=8, thread_name_prefix="spindle")
_ids = itertools.count(1)
_lock = threading.Lock()
_dispatcher: _Dispatcher | None = None


def _get_dispatcher() -> _Dispatcher:
    global _dispatcher
    if _dispatcher is None:                       # created on first use, on the GUI thread
        _dispatcher = _Dispatcher()
    return _dispatcher


def run_async(fn, *args, on_done=None, on_error=None, **kwargs) -> None:
    """Run fn(*args) on a worker thread; on_done(result) / on_error(message) run on the GUI thread."""
    d = _get_dispatcher()
    with _lock:
        task = next(_ids)
    d.callbacks[task] = (on_done, on_error)

    def work():
        try:
            result = fn(*args, **kwargs)
            ok = True
        except Exception as e:  # noqa: BLE001
            from .. import crashlog
            crashlog.write(f"Background task {getattr(fn, '__name__', fn)} failed:\n" + traceback.format_exc())
            result, ok = str(e) or e.__class__.__name__, False
        try:
            d.finished.emit(task, ok, result)
        except RuntimeError:                      # Spindle is shutting down: nobody left to tell
            pass
    _pool.submit(work)


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
