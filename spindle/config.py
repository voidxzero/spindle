"""Persistent settings stored as JSON in ~/.config/spindle/config.json."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "spindle"
DATA_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share")) / "spindle"
CONFIG_FILE = CONFIG_DIR / "config.json"

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
        "theme": "System",            # follow the desktop (KDE/GNOME); skins are optional
        "geometry": "",
        "show_log": True,
    },
    "plugins": {},                    # per-plugin settings, keyed by provider name
    "tagger": {
        "rename": False,              # rename files with the naming template when saving
        "organize": False,            # ...and move them into the music folder
    },
    "config_version": 2,
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
        super().__init__(_merge(DEFAULTS, data))

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, indent=2, ensure_ascii=False))
        tmp.replace(self.path)

    def plugin(self, name: str) -> dict:
        return self.setdefault("plugins", {}).setdefault(name, {})
