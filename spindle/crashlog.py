"""Spindle's log file: errors, Qt warnings and - for hard crashes - where every thread was. The Windows
build has no console, so without this a crash leaves no trace at all.

The log is <data dir>/spindle.log (~/.local/share/spindle or %LOCALAPPDATA%\\Spindle). A marker file
notices when the previous run ended without closing normally, so Spindle can say so next time."""
from __future__ import annotations

import datetime
import faulthandler
import os
import sys
import threading
import traceback
from pathlib import Path

from . import __version__, system

MAX_BYTES = 1_000_000            # start a fresh log beyond this (the old one is kept as spindle.log.1)
_fh = None                       # kept open for faulthandler, which writes from a crashing process
_lock = threading.Lock()


def log_dir() -> Path:
    return system.data_dir()


def log_path() -> Path:
    return log_dir() / "spindle.log"


def _marker() -> Path:
    return log_dir() / "running"


def write(text: str) -> None:
    if _fh is None:
        return
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with _lock:
        try:
            _fh.write(f"[{stamp}] {text.rstrip()}\n")
            _fh.flush()
        except (OSError, ValueError):
            pass


def _excepthook(kind, value, tb):
    write("Unhandled error:\n" + "".join(traceback.format_exception(kind, value, tb)))


def _thread_hook(args):
    if args.exc_type is SystemExit:
        return
    write(f"Unhandled error in thread {getattr(args.thread, 'name', '?')}:\n"
          + "".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))


def install() -> str:
    """Start logging. Returns the tail of the log if the previous run crashed, else ''."""
    global _fh
    crashed = ""
    try:
        d = log_dir()
        d.mkdir(parents=True, exist_ok=True)
        p = log_path()
        if _marker().exists() and p.exists():
            crashed = p.read_text(encoding="utf-8", errors="replace")[-6000:]
        if p.exists() and p.stat().st_size > MAX_BYTES:
            os.replace(p, p.with_name("spindle.log.1"))
        _fh = open(p, "a", encoding="utf-8", errors="replace")
        _marker().write_text(str(os.getpid()))
    except OSError:
        return ""
    write(f"--- Spindle {__version__} starting ({sys.platform}, Python {sys.version.split()[0]}"
          f"{', packaged' if system.FROZEN else ''}) ---")
    faulthandler.enable(file=_fh, all_threads=True)       # hard crashes: every thread's Python stack
    sys.excepthook = _excepthook
    threading.excepthook = _thread_hook
    if sys.stderr is None:                                 # windowed .exe: stray prints go to the log
        sys.stderr = _fh
    if sys.stdout is None:
        sys.stdout = _fh
    return crashed


def install_qt() -> None:
    """Route Qt's own warnings and errors into the log as well."""
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler
    except ImportError:
        return
    names = {QtMsgType.QtWarningMsg: "Qt warning", QtMsgType.QtCriticalMsg: "Qt error",
             QtMsgType.QtFatalMsg: "Qt fatal error"}

    def handler(kind, context, message):
        if kind in names and "propagateSizeHints" not in message:
            write(f"{names[kind]}: {message}")
    qInstallMessageHandler(handler)


def clean_exit() -> None:
    write("--- Spindle closed normally ---")
    try:
        _marker().unlink()
    except OSError:
        pass
