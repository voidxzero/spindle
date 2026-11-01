"""MusicBrainz (ws/2) disc ID lookup, search, and full release details."""
from __future__ import annotations

from ..disc import TOC
from .. import net
from .base import AlbumMeta, MetadataProvider, TrackMeta

WS = "https://musicbrainz.org/ws/2"
DISC_INC = "artist-credits+labels+recordings+release-groups+isrcs"
FULL_INC = ("artist-credits+labels+recordings+release-groups+isrcs+genres+"
            "recording-level-rels+work-rels+work-level-rels+artist-rels")


def credit(ac: list | None) -> tuple[str, str]:
    """Artist-credit list -> ('Name feat. Other', first artist id)."""
    if not ac:
        return "", ""
    name = "".join(c.get("name", c.get("artist", {}).get("name", "")) + c.get("joinphrase", "")
                   for c in ac)
    return name, ac[0].get("artist", {}).get("id", "")


def _pick_medium(rel: dict, disc_id: str, ntracks: int) -> tuple[dict | None, int]:
    media = rel.get("media", [])
    for m in media:
        if any(d.get("id") == disc_id for d in m.get("discs", []) or []):
            return m, len(media)
    for m in media:
        if m.get("track-count") == ntracks and (m.get("format") or "CD").find("CD") >= 0:
            return m, len(media)
    for m in media:
        if m.get("track-count") == ntracks:
            return m, len(media)
    return None, len(media)


def _genre(rel: dict) -> str:
    pool = list(rel.get("genres") or []) + list((rel.get("release-group") or {}).get("genres") or [])
    if not pool:
        return ""
    best = max(pool, key=lambda g: g.get("count", 0))
    return best.get("name", "").title()


def album_from_medium(rel: dict, medium: dict, disc_total: int, disc_id: str = "",
                      numbers: list[int] | None = None) -> AlbumMeta:
    """Build an AlbumMeta for one medium of a release. ``numbers`` renumbers tracks (CD TOC order)."""
    artist, artist_id = credit(rel.get("artist-credit"))
    rg = rel.get("release-group") or {}
    li = (rel.get("label-info") or [{}])
    label = next(((x.get("label") or {}).get("name", "") for x in li if x.get("label")), "")
    catno = next((x.get("catalog-number") or "" for x in li if x.get("catalog-number")), "")
    album = AlbumMeta(
        title=rel.get("title", ""), artist=artist, date=rel.get("date", ""),
        original_date=rg.get("first-release-date", ""), genre=_genre(rel), label=label,
        catalog=catno, barcode=rel.get("barcode") or "", country=rel.get("country") or "",
        media=medium.get("format") or "CD", disc_number=medium.get("position", 1),
        disc_total=disc_total, disc_subtitle=medium.get("title") or "",
        compilation=artist_id == "89ad4ac3-39f7-470e-963a-56509c546377" or
        "Compilation" in (rg.get("secondary-types") or []),
        source="MusicBrainz", mb_release_id=rel.get("id", ""),
        mb_releasegroup_id=rg.get("id", ""), mb_artist_id=artist_id, mb_disc_id=disc_id,
        extra={"status": rel.get("status", ""), "disambiguation": rel.get("disambiguation", ""),
               "type": rg.get("primary-type", "")},
    )
    tracks = medium.get("tracks") or []
    for i, tr in enumerate(tracks):
        if numbers is not None and i >= len(numbers):
            break
        rec = tr.get("recording") or {}
        name, aid = credit(tr.get("artist-credit") or rec.get("artist-credit"))
        composers = []
        for rr in rec.get("relations") or []:
            if rr.get("type") == "performance" and rr.get("work"):
                for wr in rr["work"].get("relations") or []:
                    if wr.get("type") in ("composer", "writer") and wr.get("artist"):
                        nm = wr["artist"].get("name", "")
                        if nm and nm not in composers:
                            composers.append(nm)
        try:
            pos = int(tr.get("position") or i + 1)
        except ValueError:
            pos = i + 1
        album.tracks.append(TrackMeta(
            number=numbers[i] if numbers is not None else pos,
            title=tr.get("title") or rec.get("title", ""),
            artist=name or artist, composer="; ".join(composers),
            isrc=(rec.get("isrcs") or [""])[0], mb_recording_id=rec.get("id", ""),
            mb_track_id=tr.get("id", ""), mb_artist_id=aid,
            length_ms=int(tr.get("length") or rec.get("length") or 0)))
    return album


def parse_release(rel: dict, toc: TOC, disc_id: str = "") -> AlbumMeta | None:
    medium, disc_total = _pick_medium(rel, disc_id, len(toc.audio_tracks))
    if medium is None:
        return None
    return album_from_medium(rel, medium, disc_total, disc_id, [t.number for t in toc.audio_tracks])


def release_albums(rel: dict) -> list[AlbumMeta]:
    """One AlbumMeta per medium of a full release (used by the file tagger)."""
    media = rel.get("media") or []
    return [album_from_medium(rel, m, len(media)) for m in media if m.get("tracks")]


def fetch_release(mbid: str, full: bool = True) -> dict:
    return net.get_json(f"{WS}/release/{mbid}", {"inc": FULL_INC if full else DISC_INC, "fmt": "json"})


def search_releases(artist: str, album: str, limit: int = 8) -> list[dict]:
    q = []
    if album:
        q.append(f'release:"{album}"')
    if artist:
        q.append(f'artist:"{artist}"')
    if not q:
        return []
    data = net.get_json(f"{WS}/release/", {"query": " AND ".join(q), "limit": limit, "fmt": "json"})
    return data.get("releases", [])


class MusicBrainz(MetadataProvider):
    name = "MusicBrainz"

    def lookup_disc(self, toc: TOC) -> list[AlbumMeta]:
        disc_id = toc.musicbrainz_id()
        try:
            data = net.get_json(f"{WS}/discid/{disc_id}",
                                {"toc": toc.musicbrainz_toc(), "cdstubs": "no", "inc": DISC_INC, "fmt": "json"})
        except net.HttpError as e:
            if e.status in (404, 400):
                return []
            raise
        releases = data.get("releases") or ([data] if data.get("media") else [])
        out = []
        for rel in releases:
            a = parse_release(rel, toc, disc_id)
            if a:
                out.append(a)
        return out

    def search(self, artist: str, album: str, toc: TOC) -> list[AlbumMeta]:
        q = []
        if album:
            q.append(f'release:"{album}"')
        if artist:
            q.append(f'artist:"{artist}"')
        q.append(f"tracks:{len(toc.audio_tracks)}")
        data = net.get_json(f"{WS}/release/", {"query": " AND ".join(q), "limit": 15, "fmt": "json"})
        out = []
        for rel in data.get("releases", [])[:8]:
            try:
                full = net.get_json(f"{WS}/release/{rel['id']}", {"inc": DISC_INC, "fmt": "json"})
            except net.HttpError:
                continue
            a = parse_release(full, toc)
            if a:
                out.append(a)
        return out

    def complete(self, album: AlbumMeta, toc: TOC) -> AlbumMeta:
        if not album.mb_release_id:
            return album
        try:
            rel = net.get_json(f"{WS}/release/{album.mb_release_id}", {"inc": FULL_INC, "fmt": "json"})
        except net.HttpError:
            return album
        full = parse_release(rel, toc, album.mb_disc_id)
        if not full:
            return album
        full.cover_hint = album.cover_hint
        return full
