#!/usr/bin/env bash
# Removes Spindle (keeps your settings in ~/.config/spindle and your music).
set -eu
D="${XDG_DATA_HOME:-$HOME/.local/share}"
rm -rf "$D/spindle/venv" "$D/spindle/lib" "$D/spindle/icons"
rm -f "$HOME/.local/bin/spindle" "$HOME/.local/bin/spindle-cli" "$D/applications/spindle.desktop" \
      "$D/icons/hicolor/scalable/apps/spindle.svg" "$D"/icons/hicolor/*x*/apps/spindle.png
echo "Spindle removed. Plugins are still in $D/spindle/plugins, settings in ~/.config/spindle."
