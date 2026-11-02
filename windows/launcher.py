"""Entry point for the packaged Windows program (Spindle.exe)."""
import sys

from spindle.gui.app import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
