"""Provider registry: built-in sources plus user plugins.

Plugins are plain .py files dropped into ~/.local/share/spindle/plugins/ (or
~/.config/spindle/plugins/). Any MetadataProvider or CoverProvider subclass they
define is picked up automatically. See plugins/discogs.py for an example.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import traceback
from pathlib import Path

from ..config import CONFIG_DIR, DATA_DIR
from .base import AlbumMeta, CoverCandidate, CoverProvider, MetadataProvider, TrackMeta
from .gnudb import GnuDB
from .musicbrainz import MusicBrainz

PLUGIN_DIRS = [DATA_DIR / "plugins", CONFIG_DIR / "plugins",
               Path(__file__).resolve().parent.parent.parent / "plugins"]

BUILTIN_META: list[type[MetadataProvider]] = [MusicBrainz, GnuDB]
_plugin_meta: list[type[MetadataProvider]] = []
_plugin_cover: list[type[CoverProvider]] = []
plugin_errors: list[str] = []
_loaded = False


def load_plugins(force: bool = False) -> None:
    global _loaded
    if _loaded and not force:
        return
    _plugin_meta.clear()
    _plugin_cover.clear()
    plugin_errors.clear()
    seen: set[str] = set()
    for d in PLUGIN_DIRS:
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.py")):
            if f.name.startswith("_") or f.name in seen:
                continue
            seen.add(f.name)
            modname = f"spindle_plugin_{f.stem}"
            try:
                spec = importlib.util.spec_from_file_location(modname, f)
                mod = importlib.util.module_from_spec(spec)
                sys.modules[modname] = mod
                spec.loader.exec_module(mod)
            except Exception:
                plugin_errors.append(f"{f.name}: {traceback.format_exc(limit=2).strip().splitlines()[-1]}")
                continue
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if obj.__module__ != modname:
                    continue
                if issubclass(obj, MetadataProvider) and obj is not MetadataProvider:
                    _plugin_meta.append(obj)
                elif issubclass(obj, CoverProvider) and obj is not CoverProvider:
                    _plugin_cover.append(obj)
    _loaded = True


def all_metadata_classes() -> list[type[MetadataProvider]]:
    load_plugins()
    return BUILTIN_META + _plugin_meta


def all_cover_classes() -> list[type[CoverProvider]]:
    from ..coverart import BUILTIN_COVER
    load_plugins()
    return BUILTIN_COVER + _plugin_cover


def _ordered(classes, order: list[str], disabled: list[str]):
    by_name = {c.name: c for c in classes}
    names = [n for n in order if n in by_name] + [n for n in by_name if n not in order]
    return [by_name[n] for n in names if n not in disabled]


def _cfg_for(cls, cfg) -> dict:
    c = dict(cfg.plugin(cls.name)) if hasattr(cfg, "plugin") else {}
    if cls is GnuDB:
        c.setdefault("user", cfg["metadata"].get("gnudb_user", "anonymous"))
    for key, (_label, default) in getattr(cls, "settings", {}).items():
        c.setdefault(key, default)
    return c


def metadata_providers(cfg) -> list[MetadataProvider]:
    m = cfg["metadata"]
    return [c(_cfg_for(c, cfg)) for c in _ordered(all_metadata_classes(), m["providers"], m["disabled"])]


def cover_providers(cfg) -> list[CoverProvider]:
    m = cfg["coverart"]
    return [c(_cfg_for(c, cfg)) for c in _ordered(all_cover_classes(), m["providers"], m["disabled"])]


def lookup_all(cfg, toc, log=lambda s: None) -> list[AlbumMeta]:
    """Query every enabled provider for this disc; MusicBrainz results come first."""
    results: list[AlbumMeta] = []
    for p in metadata_providers(cfg):
        try:
            found = p.lookup_disc(toc)
            log(f"{p.name}: {len(found)} match{'es' if len(found) != 1 else ''}")
            results.extend(found)
        except Exception as e:  # noqa: BLE001 - providers are untrusted plugins
            log(f"{p.name}: lookup failed ({e})")
    pref = cfg["metadata"].get("prefer_country", "")
    if pref:
        results.sort(key=lambda a: (a.source != "MusicBrainz", a.country != pref))
    return results


def search_all(cfg, artist, album, toc, log=lambda s: None) -> list[AlbumMeta]:
    results: list[AlbumMeta] = []
    for p in metadata_providers(cfg):
        try:
            found = p.search(artist, album, toc)
            if found:
                log(f"{p.name}: {len(found)} search result(s)")
            results.extend(found)
        except Exception as e:  # noqa: BLE001
            log(f"{p.name}: search failed ({e})")
    return results


def complete(cfg, album: AlbumMeta, toc) -> AlbumMeta:
    for p in metadata_providers(cfg):
        if p.name == album.source:
            try:
                return p.complete(album, toc)
            except Exception:  # noqa: BLE001
                return album
    return album


__all__ = ["AlbumMeta", "TrackMeta", "CoverCandidate", "MetadataProvider", "CoverProvider",
           "load_plugins", "lookup_all", "search_all", "complete", "metadata_providers",
           "cover_providers", "all_metadata_classes", "all_cover_classes", "plugin_errors"]
