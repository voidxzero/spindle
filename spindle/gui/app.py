"""Application entry point for the GUI."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def app_icon():
    """Spindle's own icon at every size. (Asking the icon theme first is unreliable: some Qt
    versions hand back a non-null but empty icon when the name isn't found.)"""
    from PySide6.QtGui import QIcon
    assets = Path(__file__).with_name("assets")
    icon = QIcon()
    for sz in (16, 24, 32, 48, 64, 128, 256, 512):
        f = assets / f"spindle-{sz}.png"
        if f.exists():
            icon.addFile(str(f))
    svg = assets / "spindle.svg"
    if icon.isNull() and svg.exists():
        icon.addFile(str(svg))
    return icon


def main(argv=None) -> int:
    from .. import crashlog
    crashed = crashlog.install()          # first: so even a crash while starting is recorded
    ap = argparse.ArgumentParser(prog="spindle")
    ap.add_argument("--simulate", action="store_true", help="add a virtual CD drive for trying things out")
    ap.add_argument("--selftest", metavar="REPORT", help=argparse.SUPPRESS)   # used by the Windows build
    args, qt_args = ap.parse_known_args(argv)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Spindle needs PySide6. Run the installer (./install.sh) or: pip install PySide6 mutagen numpy",
              file=sys.stderr)
        return 1
    from .. import APP_NAME, system
    system.setup_path()                   # Windows build: find the bundled ffmpeg and encoders
    if system.WINDOWS:
        try:                              # taskbar groups and shows Spindle's own icon, not Python's
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Spindle.CDRipper")
        except Exception:  # noqa: BLE001
            pass
    from .main_window import MainWindow

    app = QApplication([sys.argv[0], *qt_args])
    crashlog.install_qt()
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName("spindle")
    icon = app_icon()
    app.setWindowIcon(icon)
    if args.simulate:
        os.environ.setdefault("SPINDLE_SIM_SLOW", "1")   # make the virtual drive rip at a watchable pace
    if args.selftest:
        rc = selftest(app, args.selftest)
        crashlog.clean_exit()
        return rc
    win = MainWindow(simulate=args.simulate or bool(os.environ.get("SPINDLE_SIMULATE")))
    win.setWindowIcon(icon)
    win.show()
    if crashed:
        _report_crash(win, crashed)
    rc = app.exec()
    crashlog.clean_exit()
    return rc


def _selftest_release_flow(app) -> dict:
    """What happens when you pick a release: look a real disc up on MusicBrainz, apply the first match,
    load its details and find the cover - in this build, with the real window. Network trouble is
    recorded, not failed; a crash kills the process, which the build notices."""
    import time
    from .. import metadata
    from ..disc import TOC
    from .main_window import MainWindow
    rep: dict = {}
    win = MainWindow(simulate=True)
    win.cfg["drive"]["auto_read"] = False
    win.show()
    toc = TOC.from_musicbrainz_toc("1 6 95462 150 15363 32314 46592 63414 80489")
    win._show_toc(toc)
    try:
        found = metadata.lookup_all(win.cfg, toc)
    except Exception as e:  # noqa: BLE001
        rep["lookup"] = f"error: {e}"
        win.close()
        return rep
    rep["lookup"] = len(found)
    if not found:
        win.close()
        return rep
    win.candidates = found
    win._fill_release_box()
    win._apply_candidate(found[0])
    end = time.time() + 60
    while time.time() < end and (win.busy or win.spinner.isVisible()):
        app.processEvents()
        time.sleep(0.05)
    for _ in range(20):
        app.processEvents()
        time.sleep(0.05)
    rep["album"] = f"{win.f_artist.text()} - {win.f_album.text()}"
    rep["cover"] = bool(win.cover and win.cover.data)
    rep["finished"] = not win.busy
    win.close()
    return rep


def _report_crash(win, tail: str) -> None:
    """The previous run ended without closing normally: say so, and where the details are."""
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QDesktopServices
    from PySide6.QtWidgets import QMessageBox
    from .. import crashlog
    box = QMessageBox(win)
    box.setIcon(QMessageBox.Warning)
    box.setWindowTitle("Spindle closed unexpectedly")
    box.setText("Spindle closed unexpectedly last time.")
    box.setInformativeText(f"The details were saved in {crashlog.log_path()}. If it happens again, please "
                           "send that file along with what you were doing.")
    box.setDetailedText(tail[-4000:])
    open_btn = box.addButton("Open log folder", QMessageBox.ActionRole)
    box.addButton(QMessageBox.Ok)
    box.exec()
    if box.clickedButton() is open_btn:
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(crashlog.log_dir())))


def selftest(app, report: str) -> int:
    """Check a packaged build end to end without a person at the screen: the window builds, the
    bundled encoders are found, and a file converts with its tags and cover. Writes a JSON report."""
    import json
    import tempfile
    import traceback
    import wave
    from .. import __version__, convert, encoders, system, tagger
    out: dict = {"version": __version__, "frozen": system.FROZEN, "ok": False}
    try:
        out["bin"] = str(system.bundled_bin() or "")
        out["encoders"] = encoders.availability()
        from ..disc import list_drives
        out["drives"] = [f"{d.name} ({d.device})" for d in list_drives()]   # Windows: real DeviceIoControl code
        from .main_window import MainWindow
        win = MainWindow(simulate=False)
        win.show()
        app.processEvents()
        out["tabs"] = [win.tabs.tabText(i) for i in range(win.tabs.count())]
        win.close()
        tmp = tempfile.mkdtemp(prefix="spindle-selftest-")
        src = os.path.join(tmp, "01 - Tone.wav")
        with wave.open(src, "wb") as w:
            w.setnchannels(2)
            w.setsampwidth(2)
            w.setframerate(44100)
            w.writeframes(bytes(4 * 44100))
        flac = os.path.join(tmp, "01 - Tone.flac")
        encoders.encode("flac", {}, src, flac)
        tagger.write(flac, {"TITLE": "Tone ☂", "ARTIST": "Spindle", "TRACKNUMBER": "1"}, None)
        results = {}
        for fmt in ("mp3", "opus", "vorbis", "aac", "alac", "wavpack"):
            it = convert.Item(flac, "", *convert.probe(flac))
            it.dst = os.path.join(tmp, "out", f"t.{fmt}.{encoders.FORMATS[fmt].ext}")
            try:
                convert.convert_file(it, fmt, {})
                results[fmt] = tagger.read(it.dst)[0].get("title") == "Tone ☂"
            except Exception as e:  # noqa: BLE001
                results[fmt] = f"error: {e}"
        out["convert"] = results
        out["ok"] = all(v is True for v in results.values()) and all(out["encoders"].values())
        out["release_flow"] = _selftest_release_flow(app)
    except Exception:  # noqa: BLE001
        out["error"] = traceback.format_exc()
    with open(report, "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    return 0 if out["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
