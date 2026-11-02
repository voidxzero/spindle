"""Command-line front-end: `spindle-cli` rips the inserted disc with your saved settings."""
from __future__ import annotations

import argparse
import sys

from . import __version__, encoders
from .config import Config
from .disc import list_drives
from .metadata import AlbumMeta, complete, lookup_all
from .pipeline import Reporter, RipJob
from .ripper import SimulatedDrive, make_drive


class ConsoleReporter(Reporter):
    def __init__(self):
        self._last = -1

    def log(self, msg):
        print(f"\r{msg:<79}")

    def overall(self, frac, text=""):
        pct = int(frac * 100)
        if pct != self._last or text:
            self._last = pct
            bar = "#" * (pct // 4)
            print(f"\r[{bar:<25}] {pct:3d}% {text:<40}", end="", flush=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="spindle-cli", description="Rip, tag and add artwork to an audio CD.")
    ap.add_argument("-d", "--device", help="drive, e.g. /dev/sr0")
    ap.add_argument("-f", "--format", action="append", choices=list(encoders.FORMATS),
                    help="output format (repeatable); default from settings")
    ap.add_argument("-o", "--output", help="output directory")
    ap.add_argument("--offset", type=int, help="read offset correction in samples")
    ap.add_argument("--image", action="store_true", help="rip to a single file + cue sheet")
    ap.add_argument("--release", type=int, default=0, help="which metadata match to use (index)")
    ap.add_argument("--no-cover", action="store_true")
    ap.add_argument("--list", action="store_true", help="only show the disc and metadata matches")
    ap.add_argument("--simulate", action="store_true", help="use a virtual drive (testing)")
    ap.add_argument("--version", action="version", version=f"Spindle {__version__}")
    a = ap.parse_args(argv)

    cfg = Config()
    if a.format:
        cfg["output"]["formats"] = a.format
    if a.output:
        cfg["output"]["directory"] = a.output
    if a.offset is not None:
        cfg["drive"]["read_offset"] = a.offset
    if a.image:
        cfg["output"]["mode"] = "image"
    if a.no_cover:
        cfg["coverart"]["auto_fetch"] = False

    if a.simulate:
        drive = SimulatedDrive()
    else:
        dev = a.device or cfg["drive"]["device"] or (list_drives()[0].device if list_drives() else "")
        if not dev:
            print("No optical drive found.", file=sys.stderr)
            return 2
        info = next((d for d in list_drives() if d.device == dev), None)
        drive = make_drive(dev, info.name if info else dev)
    toc = drive.read_toc()
    print(f"{drive.name}: {len(toc.audio_tracks)} audio tracks, {toc.total_seconds / 60:.1f} min")
    print(f"MusicBrainz disc ID {toc.musicbrainz_id()}  FreeDB {toc.freedb_id():08x}")
    matches = lookup_all(cfg, toc, lambda m: print("  " + m))
    for i, m in enumerate(matches):
        print(f"  [{i}] {m.artist} - {m.title}  ({m.summary()})  via {m.source}")
    if a.list:
        return 0
    album = complete(cfg, matches[a.release], toc) if matches and a.release < len(matches) else AlbumMeta.blank(toc)
    album.mb_disc_id = album.mb_disc_id or toc.musicbrainz_id()
    print(f"Using: {album.artist} - {album.title}")
    job = RipJob(cfg, drive, toc, album, [t.number for t in toc.audio_tracks], None, ConsoleReporter())
    ok = job.run()
    print()
    if job.log_path:
        print(f"Log: {job.log_path}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
