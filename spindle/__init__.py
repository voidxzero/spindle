"""Spindle - an all-in-one secure CD ripper for Linux and Windows.

Linux and Windows have their own version numbers and changelogs (CHANGELOG.md is Linux's,
CHANGELOG-windows.md is Windows'). The literal __version__ line below is the *Linux* version:
installed Linux copies and install.sh read that exact line from GitHub, so keep its form and keep it
first. Windows releases are GitHub Releases tagged vX.Y.Z with the Windows version.
"""
import sys

__version__ = "1.16.0"
WINDOWS_VERSION = "1.16.0"

LINUX_VERSION = __version__
if sys.platform == "win32":
    __version__ = WINDOWS_VERSION        # what the running program reports, everywhere

APP_NAME = "Spindle"
APP_ID = "spindle"
USER_AGENT = f"Spindle/{__version__} ( CD ripper )"
