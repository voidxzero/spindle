"""Platform differences in one place: where settings and caches live, running helper programs, and
the tools bundled with the Windows build. Everything else in Spindle asks this module instead of
checking the operating system itself."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"
LINUX = sys.platform.startswith("linux")
FROZEN = bool(getattr(sys, "frozen", False))          # running from the packaged Windows build


def _env_path(var: str, default: Path) -> Path:
    v = os.environ.get(var)
    return Path(v) if v else default


def config_dir() -> Path:
    """Settings: ~/.config/spindle on Linux, %APPDATA%\\Spindle on Windows."""
    if WINDOWS:
        return _env_path("APPDATA", Path.home() / "AppData" / "Roaming") / "Spindle"
    return _env_path("XDG_CONFIG_HOME", Path.home() / ".config") / "spindle"


def data_dir() -> Path:
    """Plugins and install records: ~/.local/share/spindle, or %LOCALAPPDATA%\\Spindle."""
    if WINDOWS:
        return _env_path("LOCALAPPDATA", Path.home() / "AppData" / "Local") / "Spindle"
    return _env_path("XDG_DATA_HOME", Path.home() / ".local" / "share") / "spindle"


def cache_dir() -> Path:
    """Temporary rip files, sounds, skin images: ~/.cache/spindle, or %LOCALAPPDATA%\\Spindle\\Cache.
    XDG_CACHE_HOME is honoured on every platform (the tests use it to keep things in a temp folder)."""
    if os.environ.get("XDG_CACHE_HOME") or not WINDOWS:
        return _env_path("XDG_CACHE_HOME", Path.home() / ".cache") / "spindle"
    return data_dir() / "Cache"


def app_dir() -> Path:
    """Folder holding the program: the .exe's folder for the Windows build, else the source tree."""
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_bin() -> Path | None:
    """Encoders shipped with the Windows build (ffmpeg.exe and friends)."""
    if not FROZEN:
        return None
    for d in (app_dir() / "bin", app_dir() / "_internal" / "bin"):
        if d.is_dir():
            return d
    return None


def setup_path() -> None:
    """Put bundled tools first on PATH so shutil.which() finds them."""
    b = bundled_bin()
    if b and str(b) not in os.environ.get("PATH", "").split(os.pathsep):
        os.environ["PATH"] = str(b) + os.pathsep + os.environ.get("PATH", "")


def _no_window(kw: dict) -> dict:
    # a windowed .exe would otherwise flash a console window for every ffmpeg/encoder run
    if sys.platform == "win32":
        kw.setdefault("creationflags", getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000))
    return kw


def run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, **_no_window(kw))


def popen(cmd, **kw) -> subprocess.Popen:
    return subprocess.Popen(cmd, **_no_window(kw))


def music_dir() -> Path:
    return Path.home() / "Music"
