"""Persistent settings stored as JSON in ~/.config/spindle/config.json."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

from . import system

CONFIG_DIR = system.config_dir()
DATA_DIR = system.data_dir()
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_THEME = "Amp Classic" if system.WINDOWS else "Modern Night"   # the owner's choice per platform

DEFAULTS: dict = {
    "drive": {
        "device": "",                 # empty = first drive found
        "read_offset": 0,             # samples; look yours up with "Detect offset"
        "offsets_by_model": {},       # remembered per drive model
        "paranoia": "full",           # full | no-extra | off
        "max_retries": 20,
        "test_and_copy": True,
        "retry_on_mismatch": 2,       # extra copy passes when test/copy CRCs differ
        "auto_read": True,            # read + look up as soon as a disc is inserted
        "auto_rip": False,            # ...and then rip it without asking (after a short countdown)
        "eject_when_done": True,
    },
    "output": {
        "directory": str(Path.home() / "Music"),
        "template": "%albumartist%/%album% [(%year%)]/[%disc%-]%tracknumber% - %title%",
        "va_template": "Various Artists/%album% [(%year%)]/[%disc%-]%tracknumber% - %artist% - %title%",
        "image_template": "%albumartist%/%album% [(%year%)]/%albumartist% - %album%",
        "safe_filenames": True,       # strip characters Windows/FAT can't store
        "formats": ["flac"],
        "mode": "tracks",             # tracks | image
        "overwrite": False,
        "parallel_encoders": 2,
    },
    "encoders": {
        "flac":    {"level": 8, "verify": True},
        "mp3":     {"mode": "vbr", "vbr_quality": 0, "bitrate": 320, "id3v23": False},
        "opus":    {"bitrate": 160},
        "vorbis":  {"quality": 6},
        "aac":     {"bitrate": 256},
        "alac":    {},
        "wavpack": {"mode": "high"},
        "wav":     {},
    },
    "metadata": {
        "providers": ["MusicBrainz", "GnuDB"],
        "disabled": [],
        "gnudb_user": "anonymous",
        "contact": "",                # added to the User-Agent (MusicBrainz asks for one)
        "auto_pick_single": True,
        "prefer_country": "",         # e.g. US, GB, JP - tie-breaker between releases
        "genre_from_mb": True,
        "write_mb_ids": True,
        "acoustid_key": "",           # optional: identify untagged files by audio fingerprint
    },
    "coverart": {
        "providers": ["Cover Art Archive", "iTunes", "Deezer"],
        "disabled": [],
        "auto_fetch": True,
        "embed": True,
        "embed_max_px": 1000,         # resize embedded copy (0 = embed original)
        "save_file": True,
        "filename": "cover.jpg",
        "min_px": 500,
    },
    "extras": {
        "write_log": True,
        "write_cue": True,
        "write_m3u": True,
        "replaygain": True,
    },
    "ui": {
        "theme": DEFAULT_THEME,       # Amp Classic on Windows, Modern Night on Linux; "System" follows the OS
        "geometry": "",
        "show_log": True,
    },
    "sound": {
        "on_finish": True,            # chime when a rip finishes (a different tone if it had problems)
        "file": "",                   # your own sound for a good rip; empty = built-in chime
        "volume": 80,
    },
    "update": {
        "check_on_start": True,       # look for a newer version on GitHub when Spindle starts
        "repo": "",                   # owner/name; empty = the repository the installer recorded
        "skipped": "",                # a version you chose to skip (not offered again on startup)
    },
    "plugins": {},                    # per-plugin settings, keyed by provider name
    "tagger": {
        "rename": False,              # rename files with the naming template when saving
        "organize": False,            # ...and move them into the music folder
    },
    "convert": {
        "format": "mp3",              # what the Convert tab makes
        "folder": "",                 # empty = next to the originals
        "copy_tags": True,            # tags and cover art from the original
        "overwrite": False,           # replace files that already exist (otherwise skip them)
    },
    "config_version": 3,
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k not in ("offsets_by_model", "plugins"):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config(dict):
    def __init__(self, path: Path = CONFIG_FILE):
        self.path = Path(path)
        data = {}
        if self.path.exists():
            try:
                data = json.loads(self.path.read_text())
            except (OSError, ValueError):
                data = {}
        if data and data.get("config_version", 1) < 2:
            data.setdefault("ui", {})["theme"] = "System"   # v2: follow the desktop theme by default
            data["config_version"] = 2
        if data and data.get("config_version", 1) < 3:
            # v3: a skin is the default (Amp Classic / Modern Night). Installs still on System switch once;
            # a skin someone picked is kept, and System stays available in Options ▸ Skin.
            ui = data.setdefault("ui", {})
            if ui.get("theme", "System") == "System":
                ui["theme"] = DEFAULT_THEME
            data["config_version"] = 3
        super().__init__(_merge(DEFAULTS, data))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def plugin(self, name: str) -> dict:
        return self.setdefault("plugins", {}).setdefault(name, {})
