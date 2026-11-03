# PyInstaller recipe for Spindle.exe:  pyinstaller --noconfirm windows/spindle.spec
# Builds a folder (dist/Spindle) rather than a single .exe: it starts faster and the installer and
# the portable zip are both made from it.
import os

root = os.path.abspath(os.path.join(SPECPATH, ".."))

# Qt pieces Spindle doesn't use - leaving them out keeps the download much smaller
unused_qt = ["QtWebEngineCore", "QtWebEngineWidgets", "QtWebEngineQuick", "QtWebChannel", "QtWebSockets",
             "QtQuick", "QtQuick3D", "QtQuickWidgets", "QtQml", "Qt3DCore", "Qt3DRender", "Qt3DInput",
             "Qt3DLogic", "Qt3DAnimation", "Qt3DExtras", "QtCharts", "QtDataVisualization", "QtGraphs",
             "QtPdf", "QtPdfWidgets", "QtBluetooth", "QtNfc", "QtPositioning", "QtLocation", "QtSensors",
             "QtSerialPort", "QtSerialBus", "QtSql", "QtTest", "QtDesigner", "QtHelp", "QtOpenGLWidgets",
             "QtRemoteObjects", "QtScxml", "QtStateMachine", "QtTextToSpeech", "QtSpatialAudio", "QtHttpServer"]

a = Analysis(
    [os.path.join(root, "windows", "launcher.py")],
    pathex=[root],
    datas=[(os.path.join(root, "spindle", "gui", "assets"), os.path.join("spindle", "gui", "assets")),
           (os.path.join(root, "plugins"), "plugins"),
           (os.path.join(root, "skins"), "skins")],
    hiddenimports=["spindle.gui.app", "spindle.gui.main_window", "spindle.gui.convert_page",
                   "spindle.gui.tagger_page", "spindle.gui.update_ui", "spindle.windrive", "PySide6.QtMultimedia",
                   "mutagen", "mutagen.easyid3", "mutagen.easymp4"],
    excludes=["tkinter"] + [f"PySide6.{m}" for m in unused_qt],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Spindle", console=False,
          icon=os.path.join(root, "windows", "spindle.ico"), upx=False)
coll = COLLECT(exe, a.binaries, a.datas, name="Spindle", upx=False)
