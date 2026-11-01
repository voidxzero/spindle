"""Cover art discovery (Cover Art Archive, iTunes, Deezer, local files) and image helpers."""
from __future__ import annotations

import difflib
import io
import re
import struct
from pathlib import Path

from . import net
from .metadata.base import AlbumMeta, CoverCandidate, CoverProvider


def _sim(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[\W_]+", " ", s.lower()).strip()  # noqa: E731
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def match_score(album: AlbumMeta, artist: str, title: str) -> float:
    t = _sim(album.title, title)
    # tolerate "(Remastered)", "[Deluxe]" etc on the service side
    t = max(t, _sim(album.title, re.sub(r"[\(\[].*?[\)\]]", "", title)))
    a = 1.0 if album.various else _sim(album.artist, artist)
    return round(0.6 * t + 0.4 * a, 3)


class CoverArtArchive(CoverProvider):
    name = "Cover Art Archive"

    def find(self, album):
        out = []
        for kind, mbid in (("release", album.mb_release_id), ("release-group", album.mb_releasegroup_id)):
            if not mbid:
                continue
            try:
                data = net.get_json(f"https://coverartarchive.org/{kind}/{mbid}")
            except net.HttpError:
                continue
            for img in data.get("images", []):
                if not img.get("front") and "Front" not in (img.get("types") or []):
                    continue
                th = img.get("thumbnails") or {}
                url = th.get("1200") or img.get("image")
                out.append(CoverCandidate(
                    url=url, source=self.name, thumb_url=th.get("250") or th.get("small") or url,
                    description=f"{kind} front" + (" (1200px)" if th.get("1200") else " (original)"),
                    score=1.0 if kind == "release" else 0.95))
                if th.get("1200") and img.get("image"):
                    out.append(CoverCandidate(url=img["image"], source=self.name,
                                              thumb_url=th.get("250") or url,
                                              description=f"{kind} front (original size)",
                                              score=0.99 if kind == "release" else 0.94))
            if out:
                break
        return out


class ITunes(CoverProvider):
    name = "iTunes"
    settings = {"country": ("Store country (2-letter)", "US"), "size": ("Image size (px)", 1400)}

    def find(self, album):
        term = f"{'' if album.various else album.artist} {album.title}".strip()
        if not term:
            return []
        data = net.get_json("https://itunes.apple.com/search",
                            {"term": term, "entity": "album", "limit": 12,
                             "country": self.config.get("country") or "US"})
        size = int(self.config.get("size") or 1400)
        out = []
        for r in data.get("results", []):
            art = r.get("artworkUrl100")
            if not art:
                continue
            score = match_score(album, r.get("artistName", ""), r.get("collectionName", ""))
            if score < 0.55:
                continue
            out.append(CoverCandidate(
                url=re.sub(r"\d+x\d+bb", f"{size}x{size}bb", art), source=self.name,
                thumb_url=art.replace("100x100bb", "250x250bb"), width=size, height=size,
                description=f"{r.get('artistName', '')} – {r.get('collectionName', '')}", score=score))
        return sorted(out, key=lambda c: -c.score)[:6]


class Deezer(CoverProvider):
    name = "Deezer"

    def find(self, album):
        q = f'album:"{album.title}"' + ("" if album.various else f' artist:"{album.artist}"')
        data = net.get_json("https://api.deezer.com/search/album", {"q": q, "limit": 10})
        out = []
        for r in data.get("data", []):
            if not r.get("cover_xl"):
                continue
            score = match_score(album, (r.get("artist") or {}).get("name", ""), r.get("title", ""))
            if score < 0.55:
                continue
            out.append(CoverCandidate(url=r["cover_xl"], source=self.name,
                                      thumb_url=r.get("cover_medium") or r["cover_xl"],
                                      width=1000, height=1000, score=score,
                                      description=f"{(r.get('artist') or {}).get('name', '')} – {r.get('title', '')}"))
        return sorted(out, key=lambda c: -c.score)[:6]


BUILTIN_COVER: list[type[CoverProvider]] = [CoverArtArchive, ITunes, Deezer]


def find_all(cfg, album: AlbumMeta, log=lambda s: None) -> list[CoverCandidate]:
    from .metadata import cover_providers
    cands: list[CoverCandidate] = []
    for p in cover_providers(cfg):
        try:
            found = p.find(album)
            log(f"{p.name}: {len(found)} cover candidate(s)")
            cands.extend(found)
        except Exception as e:  # noqa: BLE001
            log(f"{p.name}: cover search failed ({e})")
    return cands


def download(c: CoverCandidate) -> bytes:
    if c.data is None:
        c.data = net.get(c.url, timeout=30)
        c.width, c.height = image_size(c.data)
    return c.data


def auto_pick(cfg, album: AlbumMeta, log=lambda s: None) -> CoverCandidate | None:
    """Take the first acceptable candidate in provider order (they're already ranked)."""
    min_px = int(cfg["coverart"].get("min_px", 0) or 0)
    fallback = None
    for c in find_all(cfg, album, log):
        if c.score < 0.75:
            continue
        try:
            download(c)
        except net.HttpError as e:
            log(f"{c.source}: download failed ({e})")
            continue
        if not c.width:
            continue
        if min(c.width, c.height) >= min_px:
            log(f"Cover art: {c.source}, {c.width}×{c.height}")
            return c
        fallback = fallback or c
    if fallback:
        log(f"Cover art: {fallback.source}, {fallback.width}×{fallback.height} (below preferred size)")
    return fallback


def from_file(path: str) -> CoverCandidate:
    data = Path(path).read_bytes()
    w, h = image_size(data)
    if not w:
        raise ValueError("Not a JPEG or PNG image")
    return CoverCandidate(url=Path(path).as_uri(), source="Local file", data=data, width=w, height=h,
                          description=Path(path).name)


def image_size(data: bytes) -> tuple[int, int]:
    """Width/height of a JPEG or PNG without needing Pillow."""
    if data[:8] == b"\x89PNG\r\n\x1a\n" and len(data) >= 24:
        return struct.unpack(">II", data[16:24])
    if data[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            seg = struct.unpack(">H", data[i + 2:i + 4])[0]
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                h, w = struct.unpack(">HH", data[i + 5:i + 9])
                return w, h
            i += 2 + seg
    return 0, 0


def mime_of(data: bytes) -> str:
    return "image/png" if data[:4] == b"\x89PNG" else "image/jpeg"


def for_embedding(data: bytes, max_px: int) -> bytes:
    """Optionally shrink artwork before embedding (needs Pillow; otherwise returns original)."""
    if not max_px:
        return data
    w, h = image_size(data)
    if max(w, h) <= max_px:
        return data
    try:
        from PIL import Image
    except ImportError:
        return data
    im = Image.open(io.BytesIO(data))
    im = im.convert("RGB")
    im.thumbnail((max_px, max_px), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90, optimize=True)
    return buf.getvalue()
