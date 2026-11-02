"""Windows look logic, testable anywhere: title bar colours per theme and the System theme's dark/light
switch (Windows 10 path: Fusion + dark palette when dark, the native style when light)."""
import os, sys
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest import mock

from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

app = QApplication.instance() or QApplication([])

from spindle.gui import winstyle            # noqa: E402
from spindle.gui.themes import THEMES       # noqa: E402


def test_colorref():
    assert winstyle.colorref("#102030") == 0x302010          # Windows wants 0x00BBGGRR


def test_titlebar_colours():
    winstyle._State.theme_colors = THEMES["Amp Classic"]
    dark, caption, text, border = winstyle.titlebar_colors()
    assert dark and caption == THEMES["Amp Classic"]["title_top"] and text and border
    winstyle._State.theme_colors = None                       # System: match the window colour
    dark, caption, text, border = winstyle.titlebar_colors()
    assert caption == app.palette().color(QPalette.Window).name() and border is None


def test_system_theme_follows_dark_light():
    native = app.style().name().lower()
    winstyle._State.native_style = "windows"                  # stands in for Windows 10's style
    try:
        with mock.patch.object(winstyle, "system_is_dark", return_value=True):
            winstyle._system_look(app)
        assert app.style().name().lower() == "fusion"
        assert app.palette().color(QPalette.Window).name() == "#202020"
        with mock.patch.object(winstyle, "system_is_dark", return_value=False):
            winstyle._system_look(app)
        assert app.style().name().lower() == "windows"
        assert app.palette().color(QPalette.Window).lightness() > 128
    finally:
        app.setStyle(native)
        app.setPalette(app.style().standardPalette())


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
