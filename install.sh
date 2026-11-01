#!/usr/bin/env bash
# Spindle installer: installs system tools (optional), a private Python env, a launcher and a menu entry.
#   ./install.sh            install for the current user (~/.local)
#   ./install.sh --no-deps  skip the package-manager step
set -euo pipefail

APP=spindle
SRC="$(cd "$(dirname "$0")" && pwd)"
PREFIX="${XDG_DATA_HOME:-$HOME/.local/share}/$APP"
BIN="$HOME/.local/bin"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor/scalable/apps"
DEPS=1
[[ "${1:-}" == "--no-deps" ]] && DEPS=0

say() { printf '\033[1;33m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;31m!!\033[0m %s\n' "$*"; }

if [[ $DEPS == 1 ]]; then
  say "Installing ripping/encoding tools with your package manager (sudo may ask for your password)"
  if command -v apt-get >/dev/null; then
    sudo apt-get install -y cdparanoia flac lame opus-tools vorbis-tools wavpack ffmpeg \
      python3-venv python3-pip libxcb-cursor0 libchromaprint-tools || warn "some packages failed to install"
  elif command -v dnf >/dev/null; then
    # Fedora: everything needed is in the official repos. Use ffmpeg-free unless RPM Fusion's full
    # ffmpeg is already installed (the two conflict). Python libs come from Fedora too, so pip has
    # little or nothing left to download.
    FF=ffmpeg-free
    rpm -q ffmpeg >/dev/null 2>&1 && FF=ffmpeg
    if dnf --version 2>/dev/null | grep -qi dnf5; then SKIP=--skip-unavailable; else SKIP=--setopt=strict=0; fi
    sudo dnf install -y $SKIP cdparanoia flac lame opus-tools vorbis-tools wavpack fdkaac "$FF" \
      xcb-util-cursor python3-pip python3-pyside6 python3-mutagen python3-numpy python3-pillow \
      qt6-qtmultimedia qt6-qtsvg chromaprint-tools \
      || warn "some packages failed to install"
  elif command -v pacman >/dev/null; then
    sudo pacman -S --needed --noconfirm cdparanoia flac lame opus-tools vorbis-tools wavpack ffmpeg python-pip \
      xcb-util-cursor || warn "some packages failed to install"
  elif command -v zypper >/dev/null; then
    sudo zypper install -y cdparanoia flac lame opus-tools vorbis-tools wavpack ffmpeg python3-pip \
      || warn "some packages failed to install"
  else
    warn "Unknown package manager. Please install: cdparanoia flac lame opus-tools vorbis-tools wavpack ffmpeg"
  fi
fi

PY=python3
command -v $PY >/dev/null || { warn "python3 not found"; exit 1; }
$PY - <<'EOF' || { echo "Python 3.9 or newer is required"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
EOF

say "Creating Python environment in $PREFIX"
mkdir -p "$PREFIX/lib"
rm -rf "$PREFIX/lib/spindle"
cp -r "$SRC/spindle" "$PREFIX/lib/spindle"
find "$PREFIX/lib/spindle" -name __pycache__ -prune -exec rm -rf {} +
# --system-site-packages lets the env reuse distro packages (e.g. Fedora's python3-pyside6);
# pip only fetches what's still missing.
[[ -d "$PREFIX/venv" ]] || $PY -m venv --system-site-packages "$PREFIX/venv"
MISSING=$("$PREFIX/venv/bin/python" - <<'EOF'
import importlib
for mod, req in (("PySide6.QtWidgets", "PySide6>=6.4"), ("PySide6.QtMultimedia", "PySide6>=6.4"), ("mutagen", "mutagen>=1.45"),
                 ("numpy", "numpy>=1.21"), ("PIL", "pillow")):
    try:
        importlib.import_module(mod)
    except Exception:
        print(req)
EOF
)
if [[ -n "$MISSING" ]]; then
  say "Fetching from PyPI: $(echo $MISSING)"
  "$PREFIX/venv/bin/python" -m pip install --quiet $(echo $MISSING | tr ' ' '\n' | sort -u)
else
  say "All Python libraries provided by the system"
fi

say "Installing plugins"
mkdir -p "$PREFIX/plugins"
for p in "$SRC"/plugins/*.py; do
  [[ -e "$PREFIX/plugins/$(basename "$p")" ]] || cp "$p" "$PREFIX/plugins/"
done

say "Creating launchers in $BIN"
mkdir -p "$BIN"
# our lib goes first on sys.path, so a "spindle" folder in the current directory can't shadow it
cat > "$BIN/spindle" <<EOF
#!/bin/sh
exec "$PREFIX/venv/bin/python" -c 'import sys; sys.path.insert(0, "$PREFIX/lib"); from spindle.gui.app import main; sys.exit(main(sys.argv[1:]))' "\$@"
EOF
cat > "$BIN/spindle-cli" <<EOF
#!/bin/sh
exec "$PREFIX/venv/bin/python" -c 'import sys; sys.path.insert(0, "$PREFIX/lib"); from spindle.cli import main; sys.exit(main(sys.argv[1:]))' "\$@"
EOF
chmod +x "$BIN/spindle" "$BIN/spindle-cli"

say "Adding menu entry"
mkdir -p "$APPS" "$ICONS" "$PREFIX/icons"
# the menu entry points at an absolute icon path (always works); the themed copies help docks/launchers
cp "$SRC/spindle/gui/assets/spindle.svg" "$PREFIX/icons/spindle.svg"
cp "$SRC/spindle/gui/assets/spindle-256.png" "$PREFIX/icons/spindle.png"
cp "$SRC/spindle/gui/assets/spindle.svg" "$ICONS/spindle.svg"
for sz in 16 24 32 48 64 128 256 512; do
  mkdir -p "${ICONS%/scalable/apps}/${sz}x${sz}/apps"
  cp "$SRC/spindle/gui/assets/spindle-$sz.png" "${ICONS%/scalable/apps}/${sz}x${sz}/apps/spindle.png"
done
cat > "$APPS/spindle.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Spindle
GenericName=CD Ripper
Comment=Rip, tag and add cover art to audio CDs
Exec=$BIN/spindle %U
Icon=$PREFIX/icons/spindle.svg
Terminal=false
Categories=AudioVideo;Audio;DiscBurning;
Keywords=cd;rip;ripper;flac;mp3;musicbrainz;accuraterip;eac;
MimeType=x-content/audio-cdda;
StartupWMClass=Spindle
EOF
touch "${ICONS%/scalable/apps}" 2>/dev/null || true
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1 || true
command -v xdg-desktop-menu >/dev/null && xdg-desktop-menu forceupdate >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q -f -t "${ICONS%/scalable/apps}" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

# drive access: on Fedora (and most systemd desktops) logind gives the logged-in user access to
# the drive automatically, so only complain if the drive really isn't readable.
DEV=$(ls /dev/sr[0-9]* 2>/dev/null | head -n1 || true)
if [[ -n "$DEV" && ! -r "$DEV" ]]; then
  GRP=$(stat -c %G "$DEV" 2>/dev/null || echo cdrom)
  warn "You can't read $DEV yet. Fix with:  sudo usermod -aG $GRP \$USER   (then log out and back in)"
fi
case ":$PATH:" in *":$BIN:"*) ;; *) warn "$BIN is not on your PATH; launch from the app menu or add it." ;; esac

for t in cdparanoia flac lame opusenc oggenc wavpack ffmpeg; do
  command -v $t >/dev/null || warn "not found: $t (formats depending on it will fall back to ffmpeg or be unavailable)"
done
say "Done. Start Spindle from your application menu, or run: spindle   (try 'spindle --simulate' without a disc)"
