"""The log catches hard crashes (with a stack trace) and notices when the last run didn't close normally."""
import os, subprocess, sys, tempfile
from unittest import mock
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env(home):
    env = dict(os.environ, XDG_DATA_HOME=home, LOCALAPPDATA=home, PYTHONPATH=ROOT)
    return env


def test_hard_crash_is_logged_and_noticed():
    home = tempfile.mkdtemp()
    crash = ("from spindle import crashlog\n"
             "crashlog.install()\n"
             "def picking_a_release():\n"
             "    import faulthandler; faulthandler._sigsegv()\n"
             "picking_a_release()\n")
    p = subprocess.run([sys.executable, "-c", crash], env=_env(home), capture_output=True, text=True)
    assert p.returncode != 0, "the process should have crashed"
    from spindle import crashlog
    with mock.patch.dict(os.environ, _env(home)):           # same folder the crashed process used
        log = crashlog.log_path().read_text(encoding="utf-8", errors="replace")
        assert "Fatal Python error" in log and "picking_a_release" in log, log
        # next start: the crash is noticed, then a normal close clears it
        tail = crashlog.install()
        assert "picking_a_release" in tail
        crashlog.clean_exit()
        crashlog._fh.close()
        crashlog._fh = None
        assert crashlog.install() == ""
        crashlog.clean_exit()
        crashlog._fh.close()
        crashlog._fh = None


def test_thread_errors_are_logged():
    home = tempfile.mkdtemp()
    code = ("from spindle import crashlog\n"
            "crashlog.install()\n"
            "import threading\n"
            "def boom(): raise ValueError('lookup went wrong')\n"
            "t = threading.Thread(target=boom); t.start(); t.join()\n"
            "crashlog.clean_exit()\n")
    subprocess.run([sys.executable, "-c", code], env=_env(home), capture_output=True)
    from spindle import crashlog
    with mock.patch.dict(os.environ, _env(home)):
        log = crashlog.log_path().read_text(encoding="utf-8")
    assert "lookup went wrong" in log and "closed normally" in log, log


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
