"""File naming templates: %field% placeholders and [optional sections]."""
from __future__ import annotations

import re
import unicodedata

from .metadata.base import AlbumMeta, TrackMeta

FIELDS = ["albumartist", "artist", "album", "title", "tracknumber", "totaltracks", "year", "date",
          "genre", "disc", "totaldiscs", "label", "catalog", "composer", "country", "discsubtitle"]
_WIN_BAD = '<>:"\\|?*'
# other names people (and other rippers like EAC/foobar/Picard) commonly use for the same fields
ALIASES = {"artists": "artist", "trackartist": "artist", "performer": "artist",
           "album artist": "albumartist", "albumartists": "albumartist", "band": "albumartist",
           "track": "tracknumber", "tracknum": "tracknumber", "trackno": "tracknumber", "number": "tracknumber",
           "tracktitle": "title", "name": "title", "discnumber": "disc", "discno": "disc",
           "totaldisc": "totaldiscs", "disctotal": "totaldiscs", "tracktotal": "totaltracks",
           "albumtitle": "album", "releasedate": "date", "catalognumber": "catalog", "catno": "catalog",
           "composers": "composer", "releasecountry": "country"}


def unknown_fields(template: str) -> list[str]:
    """Placeholders in a template that Spindle doesn't recognise."""
    out = []
    for name in re.findall(r"%([\w ]+)%", template):
        key = name.lower()
        if key not in FIELDS and key not in ALIASES and name not in out:
            out.append(name)
    return out


def clean(value: str, safe: bool) -> str:
    value = unicodedata.normalize("NFC", value or "")
    value = value.replace("/", "∕" if not safe else "-").replace("\0", "")
    if safe:
        value = value.translate({ord(c): None for c in _WIN_BAD if c != '"'}).replace('"', "'")
        value = re.sub(r"[\x00-\x1f]", "", value)
    return value.strip()


def values(album: AlbumMeta, track: TrackMeta | None, total_tracks: int) -> dict[str, str]:
    v = {
        "albumartist": album.artist, "album": album.title, "year": album.year, "date": album.date,
        "genre": album.genre, "label": album.label, "catalog": album.catalog, "country": album.country,
        "totaltracks": str(total_tracks), "discsubtitle": album.disc_subtitle,
        "disc": str(album.disc_number) if album.disc_total > 1 else "",
        "totaldiscs": str(album.disc_total) if album.disc_total > 1 else "",
        "artist": album.artist, "title": "", "tracknumber": "", "composer": "",
    }
    if track:
        v.update(artist=track.artist or album.artist, title=track.title, composer=track.composer,
                 tracknumber=f"{track.number:02d}")
    return v


def render(template: str, vals: dict[str, str], safe: bool = True) -> str:
    def sub(text: str) -> tuple[str, bool]:
        missing = False

        def rep(m):
            nonlocal missing
            key = m.group(1).lower()
            val = vals.get(ALIASES.get(key, key))
            if not val:             # empty or unknown field: leave it out rather than writing "%x%"
                missing = True
                return ""
            return clean(val, safe)
        return re.sub(r"%([\w ]+)%", rep, text), missing

    # optional [sections] vanish when any field inside them is empty
    def opt(m):
        out, missing = sub(m.group(1))
        return "" if missing else out
    t = re.sub(r"\[([^\[\]]*)\]", opt, template)
    out, _ = sub(t)
    parts = [p.strip() for p in out.split("/")]
    parts = [re.sub(r"\s{2,}", " ", p) for p in parts if p != ""]
    # tidy separators left dangling by empty fields, e.g. "01 - Title - " or " - Title"
    parts = [re.sub(r"^[\s\-–_.,]+|[\s\-–_,]+$", "", p) or "_" for p in parts]
    if safe:
        # Windows/FAT can't store folder names ending in a dot or space ("R.E.M." -> "R.E.M._" is ugly,
        # so only the very end of each folder name is trimmed; the file name keeps its extension after it)
        parts = [p.rstrip(". ") or "_" for p in parts[:-1]] + parts[-1:]
    return "/".join(p[:200] for p in parts)


def values_from_tags(tags: dict[str, str]) -> dict[str, str]:
    """Template values from a file's existing (lower-case) tags, for renaming files on disk."""
    def num(v: str) -> str:
        m = re.match(r"\d+", v or "")
        return m.group() if m else ""
    tn = num(tags.get("tracknumber", ""))
    dn, dt = num(tags.get("discnumber", "")), num(tags.get("disctotal", "") or tags.get("totaldiscs", ""))
    date = tags.get("date", "") or tags.get("year", "") or tags.get("originaldate", "")
    artist = tags.get("artist", "") or tags.get("albumartist", "")
    return {
        "albumartist": tags.get("albumartist", "") or artist, "artist": artist,
        "album": tags.get("album", ""), "title": tags.get("title", ""),
        "tracknumber": f"{int(tn):02d}" if tn else "", "totaltracks": num(tags.get("tracktotal", "") or tags.get("totaltracks", "")),
        "year": date[:4], "date": date, "genre": tags.get("genre", ""),
        "disc": dn if (dn and (not dt or int(dt) > 1)) else "", "totaldiscs": dt if dt and int(dt) > 1 else "",
        "label": tags.get("label", "") or tags.get("organization", ""),
        "catalog": tags.get("catalognumber", ""), "composer": tags.get("composer", ""),
        "country": tags.get("releasecountry", ""), "discsubtitle": tags.get("discsubtitle", ""),
    }
