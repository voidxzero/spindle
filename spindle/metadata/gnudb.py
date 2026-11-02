"""GnuDB (the freedb successor) via the CDDB-over-HTTP protocol."""
from __future__ import annotations


from .. import __version__, net
from ..disc import TOC
from .base import AlbumMeta, MetadataProvider, TrackMeta

URL = "https://gnudb.gnudb.org/~cddb/cddb.cgi"


def parse_xmcd(text: str, toc: TOC) -> AlbumMeta:
    fields: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        fields[k] = fields.get(k, "") + v          # long values continue on repeated keys
    dtitle = fields.get("DTITLE", "")
    artist, _, title = dtitle.partition(" / ")
    if not title:
        artist, title = "", dtitle
    album = AlbumMeta(title=title.strip(), artist=artist.strip(), date=fields.get("DYEAR", "").strip(),
                      genre=fields.get("DGENRE", "").strip(), source="GnuDB",
                      comment=fields.get("EXTD", "").replace("\\n", " ").strip())
    various = album.artist.lower() in ("various", "various artists")
    for i, t in enumerate(toc.tracks):
        raw = fields.get(f"TTITLE{i}", "").strip()
        if not t.audio:
            continue
        tartist, sep, ttitle = raw.partition(" / ")
        if not sep:
            tartist, ttitle = album.artist, raw
        elif not various:
            # "A / B" in a non-VA disc is most often part of the title itself
            tartist, ttitle = album.artist, raw
        album.tracks.append(TrackMeta(t.number, ttitle.strip(), tartist.strip()))
    album.compilation = various
    return album


class GnuDB(MetadataProvider):
    name = "GnuDB"

    def _cmd(self, cmd: str) -> list[str]:
        user = (self.config.get("user") or "anonymous").replace(" ", "_")
        host = "localhost"          # the protocol wants a host name; any will do, so don't share the real one
        body = net.get(URL, {"cmd": cmd, "hello": f"{user} {host} Spindle {__version__}", "proto": "6"})
        return body.decode("utf-8", "replace").splitlines()

    def lookup_disc(self, toc: TOC) -> list[AlbumMeta]:
        lines = self._cmd("cddb query " + toc.freedb_query())
        if not lines:
            return []
        code = lines[0][:3]
        matches: list[tuple[str, str]] = []
        if code == "200":
            parts = lines[0].split(" ", 3)
            matches.append((parts[1], parts[2]))
        elif code in ("210", "211"):
            for ln in lines[1:]:
                if ln.strip() == ".":
                    break
                parts = ln.split(" ", 2)
                if len(parts) >= 2:
                    matches.append((parts[0], parts[1]))
        else:
            return []
        out = []
        for categ, discid in matches[:6]:
            rl = self._cmd(f"cddb read {categ} {discid}")
            if rl and rl[0].startswith("210"):
                text = "\n".join(ln for ln in rl[1:] if ln.strip() != ".")
                a = parse_xmcd(text, toc)
                if not a.genre:
                    a.genre = categ.title()
                a.extra["gnudb"] = f"{categ}/{discid}"
                out.append(a)
        return out
