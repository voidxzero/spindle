"""Example Spindle plugin: Discogs metadata search and cover art.

Copy this file to ~/.local/share/spindle/plugins/ (the installer does it for you),
then add a personal access token in Settings > Plugins
(generate one at https://www.discogs.com/settings/developers).

A plugin is just a Python file defining MetadataProvider and/or CoverProvider
subclasses. Spindle discovers them automatically.
"""
from __future__ import annotations

import re

from spindle import net
from spindle.coverart import match_score
from spindle.metadata.base import AlbumMeta, CoverCandidate, CoverProvider, MetadataProvider, TrackMeta

API = "https://api.discogs.com"


def _clean(name: str) -> str:
    return re.sub(r"\s\(\d+\)$", "", name or "").rstrip("*")   # "Artist (2)" -> "Artist"


def _artists(lst) -> str:
    out = ""
    for a in lst or []:
        out += _clean(a.get("anv") or a.get("name", ""))
        j = (a.get("join") or "").strip()
        out += f" {j} " if j and j != "," else (", " if j == "," else "")
    return out.strip(" ,")


class _Discogs:
    settings = {"token": ("Personal access token", "")}

    def _get(self, path, params=None):
        token = (self.config.get("token") or "").strip()
        if not token:
            raise RuntimeError("no Discogs token set (Settings > Plugins)")
        return net.get_json(API + path, params, headers={"Authorization": f"Discogs token={token}"})


class DiscogsMetadata(_Discogs, MetadataProvider):
    name = "Discogs"

    def lookup_disc(self, toc):
        return []        # Discogs has no TOC lookup; search() is used when you type artist/album

    def search(self, artist, album, toc):
        if not self.config.get("token"):
            return []
        res = self._get("/database/search", {"type": "release", "format": "CD", "per_page": 10,
                                             "release_title": album, "artist": artist})
        out = []
        n = len(toc.audio_tracks)
        for hit in res.get("results", [])[:6]:
            rel = self._get(f"/releases/{hit['id']}")
            tracks = [t for t in rel.get("tracklist", []) if t.get("type_", "track") == "track"]
            if len(tracks) < n:
                continue
            labels = rel.get("labels") or [{}]
            a = AlbumMeta(title=rel.get("title", ""), artist=_artists(rel.get("artists")),
                          date=rel.get("released") or str(rel.get("year") or ""),
                          genre=(rel.get("styles") or rel.get("genres") or [""])[0],
                          label=_clean(labels[0].get("name", "")), catalog=labels[0].get("catno", ""),
                          country=rel.get("country", ""), source=self.name,
                          cover_hint=next((i.get("uri") for i in rel.get("images", []) if i.get("type") == "primary"), ""),
                          extra={"discogs_id": rel.get("id")})
            for i, (t, tr) in enumerate(zip(toc.audio_tracks, tracks)):
                a.tracks.append(TrackMeta(t.number, tr.get("title", ""), _artists(tr.get("artists")) or a.artist))
            out.append(a)
        return out


class DiscogsCovers(_Discogs, CoverProvider):
    name = "Discogs"

    def find(self, album):
        if not self.config.get("token"):
            return []
        if album.cover_hint and album.source == "Discogs":
            return [CoverCandidate(album.cover_hint, self.name, description="Discogs primary image")]
        res = self._get("/database/search", {"type": "release", "per_page": 8,
                                             "release_title": album.title, "artist": album.artist})
        out = []
        for hit in res.get("results", []):
            if not hit.get("cover_image") or "spacer.gif" in hit["cover_image"]:
                continue
            artist, _, title = hit.get("title", "").partition(" - ")
            score = match_score(album, artist, title)
            if score >= 0.6:
                out.append(CoverCandidate(hit["cover_image"], self.name, thumb_url=hit.get("thumb", ""),
                                          description=hit.get("title", ""), score=score * 0.95))
        return out[:4]
