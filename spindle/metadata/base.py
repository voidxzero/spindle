"""Metadata model and the provider interfaces that plugins implement."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from ..disc import TOC


@dataclass
class TrackMeta:
    number: int
    title: str = ""
    artist: str = ""
    composer: str = ""
    isrc: str = ""
    mb_recording_id: str = ""
    mb_track_id: str = ""
    mb_artist_id: str = ""
    length_ms: int = 0               # length according to the database (0 = unknown)


@dataclass
class AlbumMeta:
    title: str = ""
    artist: str = ""                 # album artist
    date: str = ""                   # YYYY or YYYY-MM-DD
    original_date: str = ""
    genre: str = ""
    label: str = ""
    catalog: str = ""
    barcode: str = ""
    country: str = ""
    media: str = "CD"
    disc_number: int = 1
    disc_total: int = 1
    disc_subtitle: str = ""
    compilation: bool = False
    comment: str = ""
    source: str = ""                 # provider that produced this
    mb_release_id: str = ""
    mb_releasegroup_id: str = ""
    mb_artist_id: str = ""
    mb_disc_id: str = ""
    cover_hint: str = ""             # provider specific URL for artwork, if known
    extra: dict = field(default_factory=dict)
    tracks: list[TrackMeta] = field(default_factory=list)

    @property
    def year(self) -> str:
        return (self.date or self.original_date)[:4]

    @property
    def various(self) -> bool:
        if self.compilation:
            return True
        artists = {t.artist for t in self.tracks if t.artist}
        return len(artists) > 1 and self.artist.lower() in ("various artists", "various", "va", "")

    def track(self, n: int) -> TrackMeta:
        for t in self.tracks:
            if t.number == n:
                return t
        t = TrackMeta(n)
        self.tracks.append(t)
        return t

    def summary(self) -> str:
        bits = [self.date, self.country, self.label, self.catalog, self.media]
        if self.disc_total > 1:
            bits.append(f"disc {self.disc_number}/{self.disc_total}")
        return " · ".join(b for b in bits if b)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AlbumMeta":
        d = dict(d)
        tracks = [TrackMeta(**t) for t in d.pop("tracks", [])]
        known = set(cls.__dataclass_fields__)
        a = cls(**{k: v for k, v in d.items() if k in known})
        a.tracks = tracks
        return a

    @classmethod
    def blank(cls, toc: TOC) -> "AlbumMeta":
        return cls(title="Unknown Album", artist="Unknown Artist",
                   tracks=[TrackMeta(t.number, f"Track {t.number:02d}") for t in toc.audio_tracks])


class MetadataProvider:
    """Subclass this in a plugin to add a metadata source.

    ``settings`` declares options shown in Settings > Plugins:
        {"token": ("Personal access token", "")}
    Values arrive in ``self.config`` (a dict) before lookups run.
    """
    name = "Unnamed"
    settings: dict[str, tuple[str, object]] = {}

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def lookup_disc(self, toc: TOC) -> list[AlbumMeta]:
        """Return candidate releases for this exact disc. Raise on network errors."""
        return []

    def search(self, artist: str, album: str, toc: TOC) -> list[AlbumMeta]:
        """Free-text fallback when the disc isn't found. Optional."""
        return []

    def complete(self, album: AlbumMeta, toc: TOC) -> AlbumMeta:
        """Fill in details for a chosen candidate (e.g. a second, richer request). Optional."""
        return album


@dataclass
class CoverCandidate:
    url: str
    source: str
    thumb_url: str = ""
    width: int = 0
    height: int = 0
    description: str = ""
    score: float = 1.0               # 0..1 match confidence
    data: bytes | None = None        # filled once downloaded (or for local files)


class CoverProvider:
    """Subclass this in a plugin to add a cover art source."""
    name = "Unnamed"
    settings: dict[str, tuple[str, object]] = {}

    def __init__(self, config: dict | None = None):
        self.config = config or {}

    def find(self, album: AlbumMeta) -> list[CoverCandidate]:
        return []
