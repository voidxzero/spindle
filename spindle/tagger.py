"""Write tags and embedded artwork into every supported format with mutagen."""
from __future__ import annotations

import base64
import os

from .coverart import image_size, mime_of
from .metadata.base import AlbumMeta, TrackMeta


def tag_values(album: AlbumMeta, track: TrackMeta | None, total: int, mb_ids: bool = True,
               extra: dict | None = None) -> dict[str, str]:
    """Format-neutral tag dictionary (Vorbis comment names)."""
    t: dict[str, str] = {
        "ALBUM": album.title, "ALBUMARTIST": album.artist, "DATE": album.date or album.original_date,
        "ORIGINALDATE": album.original_date, "GENRE": album.genre, "LABEL": album.label,
        "CATALOGNUMBER": album.catalog, "BARCODE": album.barcode, "RELEASECOUNTRY": album.country,
        "MEDIA": album.media, "DISCNUMBER": str(album.disc_number), "DISCTOTAL": str(album.disc_total),
        "DISCSUBTITLE": album.disc_subtitle, "TRACKTOTAL": str(total), "COMMENT": album.comment,
        "COMPILATION": "1" if album.various else "",
    }
    if track:
        t.update({"TITLE": track.title, "ARTIST": track.artist or album.artist,
                  "TRACKNUMBER": str(track.number), "COMPOSER": track.composer, "ISRC": track.isrc})
    if mb_ids:
        t.update({"MUSICBRAINZ_ALBUMID": album.mb_release_id,
                  "MUSICBRAINZ_RELEASEGROUPID": album.mb_releasegroup_id,
                  "MUSICBRAINZ_ALBUMARTISTID": album.mb_artist_id,
                  "MUSICBRAINZ_DISCID": album.mb_disc_id})
        if track:
            t.update({"MUSICBRAINZ_TRACKID": track.mb_recording_id,
                      "MUSICBRAINZ_RELEASETRACKID": track.mb_track_id,
                      "MUSICBRAINZ_ARTISTID": track.mb_artist_id})
    t.update(extra or {})
    return {k: v for k, v in t.items() if v not in ("", None)}


# MusicBrainz Picard-compatible names for ID3 TXXX / MP4 freeform / APE
_MB_NAMES = {
    "MUSICBRAINZ_ALBUMID": "MusicBrainz Album Id", "MUSICBRAINZ_RELEASEGROUPID": "MusicBrainz Release Group Id",
    "MUSICBRAINZ_ALBUMARTISTID": "MusicBrainz Album Artist Id", "MUSICBRAINZ_DISCID": "MusicBrainz Disc Id",
    "MUSICBRAINZ_RELEASETRACKID": "MusicBrainz Release Track Id", "MUSICBRAINZ_ARTISTID": "MusicBrainz Artist Id",
    "MUSICBRAINZ_TRACKID": "MusicBrainz Track Id", "RELEASECOUNTRY": "MusicBrainz Album Release Country",
    "CATALOGNUMBER": "CATALOGNUMBER", "BARCODE": "BARCODE", "MEDIA": "MEDIA", "ORIGINALDATE": "ORIGINALDATE",
    "REPLAYGAIN_TRACK_GAIN": "REPLAYGAIN_TRACK_GAIN", "REPLAYGAIN_TRACK_PEAK": "REPLAYGAIN_TRACK_PEAK",
    "REPLAYGAIN_ALBUM_GAIN": "REPLAYGAIN_ALBUM_GAIN", "REPLAYGAIN_ALBUM_PEAK": "REPLAYGAIN_ALBUM_PEAK",
}


def _picture(cover: bytes):
    from mutagen.flac import Picture
    pic = Picture()
    pic.type = 3
    pic.mime = mime_of(cover)
    pic.desc = "Front Cover"
    pic.width, pic.height = image_size(cover)
    pic.depth = 24
    pic.data = cover
    return pic


# every key Spindle writes; in preserve mode only these are replaced and everything else is kept
MANAGED = {"ALBUM", "ALBUMARTIST", "DATE", "ORIGINALDATE", "GENRE", "LABEL", "CATALOGNUMBER", "BARCODE",
           "RELEASECOUNTRY", "MEDIA", "DISCNUMBER", "DISCTOTAL", "DISCSUBTITLE", "TRACKTOTAL", "COMMENT",
           "COMPILATION", "TITLE", "ARTIST", "TRACKNUMBER", "COMPOSER", "ISRC", "MUSICBRAINZ_ALBUMID",
           "MUSICBRAINZ_RELEASEGROUPID", "MUSICBRAINZ_ALBUMARTISTID", "MUSICBRAINZ_DISCID",
           "MUSICBRAINZ_TRACKID", "MUSICBRAINZ_RELEASETRACKID", "MUSICBRAINZ_ARTISTID",
           "TOTALTRACKS", "TOTALDISCS"}


def _vorbis(f, tags, cover, is_flac, preserve=False):
    if f.tags is None:
        f.add_tags()
    if preserve:
        for k in list(f.tags.keys()):
            if k.upper() in MANAGED:
                del f.tags[k]
    else:
        f.tags.clear()
    for k, v in tags.items():
        f[k] = v
    if "TRACKTOTAL" in tags:
        f["TOTALTRACKS"] = tags["TRACKTOTAL"]
    if "DISCTOTAL" in tags:
        f["TOTALDISCS"] = tags["DISCTOTAL"]
    if cover:
        if is_flac:
            f.clear_pictures()
            f.add_picture(_picture(cover))
        else:
            if preserve:
                for k in list(f.tags.keys()):
                    if k.upper() in ("METADATA_BLOCK_PICTURE", "COVERART", "COVERARTMIME"):
                        del f.tags[k]
            f["METADATA_BLOCK_PICTURE"] = base64.b64encode(_picture(cover).write()).decode("ascii")


_ID3_MANAGED = ("TIT2", "TPE1", "TALB", "TPE2", "TCON", "TCOM", "TPUB", "TSRC", "TSST", "TYER", "TDRC",
                "TDOR", "TORY", "TRCK", "TPOS", "TCMP", "UFID:http://musicbrainz.org")


def _id3(tags, cover, v23, existing=None, preserve=False):
    from mutagen import id3
    t = existing if existing is not None else id3.ID3()   # saving a fresh tag replaces the old one
    if preserve:
        for fid in _ID3_MANAGED:
            t.delall(fid)
        managed_desc = set(_MB_NAMES.values()) | set(_MB_NAMES)
        for key in list(t.keys()):
            frame = t[key]
            if key.startswith("TXXX:") and frame.desc in managed_desc and not frame.desc.startswith("REPLAYGAIN"):
                del t[key]
            elif key.startswith("COMM") and "COMMENT" in tags and frame.desc == "":
                del t[key]
            elif key.startswith("APIC") and cover:
                del t[key]
    else:
        t.clear()
    simple = {"TITLE": id3.TIT2, "ARTIST": id3.TPE1, "ALBUM": id3.TALB, "ALBUMARTIST": id3.TPE2,
              "GENRE": id3.TCON, "COMPOSER": id3.TCOM, "LABEL": id3.TPUB, "ISRC": id3.TSRC,
              "DISCSUBTITLE": id3.TSST}
    for k, cls in simple.items():
        if k in tags:
            t.add(cls(encoding=3, text=tags[k]))
    if "DATE" in tags:
        t.add((id3.TYER if v23 else id3.TDRC)(encoding=3, text=tags["DATE"][:4] if v23 else tags["DATE"]))
    if "ORIGINALDATE" in tags and not v23:
        t.add(id3.TDOR(encoding=3, text=tags["ORIGINALDATE"]))
    if "TRACKNUMBER" in tags:
        t.add(id3.TRCK(encoding=3, text=f"{tags['TRACKNUMBER']}/{tags.get('TRACKTOTAL', '')}".rstrip("/")))
    if "DISCNUMBER" in tags:
        t.add(id3.TPOS(encoding=3, text=f"{tags['DISCNUMBER']}/{tags.get('DISCTOTAL', '')}".rstrip("/")))
    if tags.get("COMPILATION") == "1":
        t.add(id3.TCMP(encoding=3, text="1"))
    if "COMMENT" in tags:
        t.add(id3.COMM(encoding=3, lang="eng", desc="", text=tags["COMMENT"]))
    for k, name in _MB_NAMES.items():
        if k in tags and k != "MUSICBRAINZ_TRACKID":
            t.add(id3.TXXX(encoding=3, desc=name, text=tags[k]))
    if "MUSICBRAINZ_TRACKID" in tags:
        t.add(id3.UFID(owner="http://musicbrainz.org", data=tags["MUSICBRAINZ_TRACKID"].encode()))
    if cover:
        t.add(id3.APIC(encoding=3, mime=mime_of(cover), type=3, desc="Front Cover", data=cover))
    return t


def _mp4(f, tags, cover, preserve=False):
    from mutagen.mp4 import MP4Cover, MP4FreeForm
    if f.tags is None:
        f.add_tags()
    if preserve:
        managed = {"\xa9nam", "\xa9ART", "\xa9alb", "aART", "\xa9day", "\xa9gen", "\xa9wrt", "trkn", "disk", "cpil"}
        names = set(_MB_NAMES.values()) | {"LABEL", "ISRC", "MusicBrainz Track Id"}
        for k in list(f.tags.keys()):
            if k in managed or (k.startswith("----:com.apple.iTunes:") and k.split(":", 2)[2] in names
                                and not k.split(":", 2)[2].startswith("REPLAYGAIN")):
                del f.tags[k]
            elif k == "\xa9cmt" and "COMMENT" in tags:
                del f.tags[k]
    else:
        f.tags.clear()
    simple = {"TITLE": "\xa9nam", "ARTIST": "\xa9ART", "ALBUM": "\xa9alb", "ALBUMARTIST": "aART",
              "DATE": "\xa9day", "GENRE": "\xa9gen", "COMPOSER": "\xa9wrt", "COMMENT": "\xa9cmt"}
    for k, atom in simple.items():
        if k in tags:
            f.tags[atom] = [tags[k]]
    if "TRACKNUMBER" in tags:
        f.tags["trkn"] = [(int(tags["TRACKNUMBER"]), int(tags.get("TRACKTOTAL", 0) or 0))]
    if "DISCNUMBER" in tags:
        f.tags["disk"] = [(int(tags["DISCNUMBER"]), int(tags.get("DISCTOTAL", 0) or 0))]
    if tags.get("COMPILATION") == "1":
        f.tags["cpil"] = True
    for k in list(_MB_NAMES) + ["LABEL", "ISRC"]:
        if k in tags:
            name = _MB_NAMES.get(k, k)
            if k == "MUSICBRAINZ_TRACKID":
                name = "MusicBrainz Track Id"
            f.tags[f"----:com.apple.iTunes:{name}"] = [MP4FreeForm(tags[k].encode("utf-8"))]
    if cover:
        fmt = MP4Cover.FORMAT_PNG if mime_of(cover) == "image/png" else MP4Cover.FORMAT_JPEG
        f.tags["covr"] = [MP4Cover(cover, imageformat=fmt)]


def _ape(path, tags, cover, preserve=False):
    from mutagen.apev2 import APEv2, APENoHeaderError, APEValue, BINARY
    names = {"TITLE": "Title", "ARTIST": "Artist", "ALBUM": "Album", "ALBUMARTIST": "Album Artist",
             "DATE": "Year", "GENRE": "Genre", "COMPOSER": "Composer", "COMMENT": "Comment",
             "LABEL": "Label", "ISRC": "ISRC", "DISCSUBTITLE": "DiscSubtitle"}
    try:
        t = APEv2(path)
        if preserve:
            managed = {v.lower() for v in names.values()} | {v.lower() for v in _MB_NAMES.values()
                                                             if not v.startswith("REPLAYGAIN")}
            managed |= {k.lower() for k in MANAGED} | {"track", "disc"}
            for k in list(t.keys()):
                if k.lower() in managed:
                    del t[k]
        else:
            t.clear()
    except APENoHeaderError:
        t = APEv2()
    for k, v in tags.items():
        if k in ("TRACKNUMBER", "DISCNUMBER", "TRACKTOTAL", "DISCTOTAL"):
            continue
        t[names.get(k, _MB_NAMES.get(k, k))] = v
    if "TRACKNUMBER" in tags:
        t["Track"] = f"{tags['TRACKNUMBER']}/{tags.get('TRACKTOTAL', '')}".rstrip("/")
    if "DISCNUMBER" in tags:
        t["Disc"] = f"{tags['DISCNUMBER']}/{tags.get('DISCTOTAL', '')}".rstrip("/")
    if cover:
        ext = "png" if mime_of(cover) == "image/png" else "jpg"
        t["Cover Art (Front)"] = APEValue(f"cover.{ext}".encode() + b"\0" + cover, BINARY)
    t.save(path)


def write(path: str, tags: dict[str, str], cover: bytes | None = None, id3v23: bool = False,
          preserve: bool = False) -> None:
    """Write tags. preserve=True replaces only the fields Spindle manages and keeps everything else
    (ReplayGain, lyrics, ratings, other apps' tags) and keeps the existing cover unless one is given."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".flac":
        from mutagen.flac import FLAC
        f = FLAC(path)
        _vorbis(f, tags, cover, True, preserve)
        f.save()
    elif ext in (".ogg", ".opus", ".oga"):
        from mutagen import File
        f = File(path)
        _vorbis(f, tags, cover, False, preserve)
        f.save()
    elif ext == ".mp3":
        existing = None
        if preserve:
            from mutagen import id3
            try:
                existing = id3.ID3(path)
            except id3.ID3NoHeaderError:
                existing = None
        t = _id3(tags, cover, id3v23, existing, preserve)
        if id3v23:
            t.update_to_v23()
        t.save(path, v2_version=3 if id3v23 else 4)
    elif ext in (".m4a", ".mp4", ".m4b"):
        from mutagen.mp4 import MP4
        f = MP4(path)
        _mp4(f, tags, cover, preserve)
        f.save()
    elif ext == ".wv":
        _ape(path, tags, cover, preserve)
    elif ext == ".wav":
        from mutagen.wave import WAVE
        f = WAVE(path)
        if f.tags is None:
            f.add_tags()
        _id3(tags, cover, False, existing=f.tags, preserve=preserve)
        f.save()
    else:
        raise ValueError(f"Don't know how to tag {ext} files")


def add(path: str, extra: dict[str, str]) -> None:
    """Add/replace a few tags in an already tagged file (used for ReplayGain)."""
    ext = os.path.splitext(path)[1].lower()
    if ext in (".flac", ".ogg", ".opus", ".oga"):
        from mutagen import File
        f = File(path)
        for k, v in extra.items():
            f[k] = v
        f.save()
    elif ext in (".mp3", ".wav"):
        from mutagen import id3
        if ext == ".mp3":
            t = id3.ID3(path)
        else:
            from mutagen.wave import WAVE
            w = WAVE(path)
            t = w.tags
        for k, v in extra.items():
            t.delall(f"TXXX:{_MB_NAMES.get(k, k)}")
            t.add(id3.TXXX(encoding=3, desc=_MB_NAMES.get(k, k), text=v))
        if ext == ".mp3":
            t.save(path, v2_version=t.version[1] if t.version[1] in (3, 4) else 4)
        else:
            w.save()
    elif ext == ".m4a":
        from mutagen.mp4 import MP4, MP4FreeForm
        f = MP4(path)
        for k, v in extra.items():
            f.tags[f"----:com.apple.iTunes:{_MB_NAMES.get(k, k)}"] = [MP4FreeForm(v.encode())]
        f.save()
    elif ext == ".wv":
        from mutagen.apev2 import APEv2
        t = APEv2(path)
        for k, v in extra.items():
            t[k] = v
        t.save(path)


# ---------------------------------------------------------------------------
# Reading (used by the file tagger)
# ---------------------------------------------------------------------------
_APE_READ = {"album artist": "albumartist", "track": "tracknumber", "disc": "discnumber", "year": "date",
             "musicbrainz album id": "musicbrainz_albumid", "musicbrainz track id": "musicbrainz_trackid"}


def read(path: str) -> tuple[dict[str, str], float]:
    """Return (lower-case tag dict, duration seconds). Keys: title, artist, album, albumartist,
    tracknumber, discnumber, date, genre, musicbrainz_albumid, musicbrainz_trackid, ..."""
    import mutagen
    f = mutagen.File(path, easy=True)
    if f is None:
        raise ValueError("Unsupported or damaged file")
    out: dict[str, str] = {}
    if f.tags is not None:
        for k in list(f.tags.keys()):
            try:
                v = f.tags[k]
            except (KeyError, ValueError):
                continue
            if isinstance(v, list):
                v = v[0] if v else ""
            if not isinstance(v, str):
                v = str(v)
            key = k.lower()
            key = _APE_READ.get(key, key)
            if key.startswith("----:com.apple.itunes:"):
                continue
            out.setdefault(key, v.strip())
    for k in ("tracknumber", "discnumber"):
        if "/" in out.get(k, ""):
            n, _, t = out[k].partition("/")
            out[k] = n.strip()
            out.setdefault("tracktotal" if k == "tracknumber" else "disctotal", t.strip())
    length = float(getattr(getattr(f, "info", None), "length", 0) or 0)
    return out, length


def read_cover(path: str) -> bytes | None:
    """First embedded front cover (or any picture), or None."""
    import mutagen
    ext = os.path.splitext(path)[1].lower()
    try:
        f = mutagen.File(path)
    except Exception:  # noqa: BLE001
        return None
    if f is None:
        return None
    if ext == ".flac":
        pics = sorted(f.pictures, key=lambda p: p.type != 3)
        return pics[0].data if pics else None
    tags = f.tags
    if tags is None:
        return None
    if ext in (".ogg", ".opus", ".oga"):
        from mutagen.flac import Picture
        for v in tags.get("METADATA_BLOCK_PICTURE", []):
            try:
                return Picture(base64.b64decode(v)).data
            except Exception:  # noqa: BLE001
                continue
        return None
    if ext in (".mp3", ".wav"):
        apics = [tags[k] for k in tags.keys() if k.startswith("APIC")]
        apics.sort(key=lambda a: a.type != 3)
        return apics[0].data if apics else None
    if ext in (".m4a", ".mp4", ".m4b"):
        c = tags.get("covr")
        return bytes(c[0]) if c else None
    if ext == ".wv":
        for k in tags.keys():
            if k.lower().startswith("cover art"):
                val = tags[k].value
                return val.split(b"\0", 1)[1] if b"\0" in val else val
    return None
