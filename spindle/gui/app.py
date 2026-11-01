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
    ap = argparse.ArgumentParser(prog="spindle")
    ap.add_argument("--simulate", action="store_true", help="add a virtual CD drive for trying things out")
    args, qt_args = ap.parse_known_args(argv)
    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("Spindle needs PySide6. Run the installer (./install.sh) or: pip install PySide6 mutagen numpy",
              file=sys.stderr)
        return 1
    from .. import APP_NAME
    from .main_window import MainWindow

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_NAME)
    app.setDesktopFileName("spindle")
    icon = app_icon()
    app.setWindowIcon(icon)
    if args.simulate:
        os.environ.setdefault("SPINDLE_SIM_SLOW", "1")   # make the virtual drive rip at a watchable pace
    win = MainWindow(simulate=args.simulate or bool(os.environ.get("SPINDLE_SIMULATE")))
    win.setWindowIcon(icon)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
