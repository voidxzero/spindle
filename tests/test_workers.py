"""Background tasks: every result arrives exactly once, on the GUI thread, even when callbacks open nested
windows and Python cleans up constantly (the situation that crashed Spindle 1.15.1 on Windows)."""
import gc, os, sys, threading, time
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from spindle.gui import workers          # noqa: E402


def _pump_until(cond, timeout=60):
    end = time.time() + timeout
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.002)
    assert cond(), "timed out"


def test_results_arrive_once_on_gui_thread():
    gui = threading.get_ident()
    got, errors = [], []

    def work(i):
        time.sleep(0.001 * (i % 5))
        if i % 7 == 0:
            raise ValueError(f"task {i} failed")
        return i

    def done(i):
        assert threading.get_ident() == gui
        got.append(i)
        if i % 10 == 1:                       # like a dialog opening inside the callback
            loop = QEventLoop()
            QTimer.singleShot(5, lambda: (gc.collect(), loop.quit()))
            loop.exec()
        gc.collect()

    def failed(msg):
        assert threading.get_ident() == gui
        errors.append(msg)
        gc.collect()

    n = 400
    for i in range(n):
        workers.run_async(work, i, on_done=done, on_error=failed)
    _pump_until(lambda: len(got) + len(errors) == n)
    for _ in range(50):                       # nothing arrives twice
        app.processEvents()
    assert sorted(got) == [i for i in range(n) if i % 7], "missing or duplicate results"
    assert len(errors) == len([i for i in range(n) if i % 7 == 0]) and all("failed" in e for e in errors)
    assert workers._get_dispatcher().callbacks == {}, "callbacks left behind"


def test_callback_errors_dont_break_later_tasks():
    seen = []

    def bad(_):
        seen.append("bad")
        raise RuntimeError("bug in a callback")

    logged = []
    from spindle import crashlog
    orig = crashlog.write
    crashlog.write = logged.append
    try:
        workers.run_async(lambda: 1, on_done=bad)
        workers.run_async(lambda: 2, on_done=seen.append)
        _pump_until(lambda: 2 in seen)
    finally:
        crashlog.write = orig
    assert seen[0] == "bad" and any("bug in a callback" in x for x in logged), logged


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
