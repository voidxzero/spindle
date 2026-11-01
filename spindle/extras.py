"""ReplayGain, cue sheets, playlists and the EAC-style extraction log."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime

from . import __version__
from .disc import TOC, msf_str


# --- ReplayGain 2.0 (EBU R128 loudness, -18 LUFS reference) ------------------
def _ebur128(paths: list[str]) -> float | None:
    ff = shutil.which("ffmpeg")
    if not ff:
        return None
    lst = None
    if len(paths) == 1:
        inp = ["-i", paths[0]]
    else:
        lst = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False)
        for path in paths:
            lst.write("file '%s'\n" % path.replace("'", "'\\''"))
        lst.close()
        inp = ["-f", "concat", "-safe", "0", "-i", lst.name]
    try:
        p = subprocess.run([ff, "-nostdin", "-hide_banner", *inp, "-af", "ebur128", "-f", "null", "-"],
                           capture_output=True, text=True, errors="replace")
    finally:
        if lst:
            os.unlink(lst.name)
    m = re.findall(r"I:\s+(-?[\d.]+) LUFS", p.stderr)
    return float(m[-1]) if m else None


def replaygain(wavs: list[str], peaks: list[float]) -> tuple[list[dict], str] | None:
    """Returns per-track tag dicts (incl. album values) or None if ffmpeg is missing."""
    album_i = _ebur128(wavs)
    if album_i is None:
        return None
    album_peak = max(peaks) if peaks else 1.0
    out = []
    for w, pk in zip(wavs, peaks):
        ti = _ebur128([w])
        if ti is None:
            continue
        out.append({"REPLAYGAIN_TRACK_GAIN": f"{-18.0 - ti:+.2f} dB",
                    "REPLAYGAIN_TRACK_PEAK": f"{pk:.6f}",
                    "REPLAYGAIN_ALBUM_GAIN": f"{-18.0 - album_i:+.2f} dB",
                    "REPLAYGAIN_ALBUM_PEAK": f"{album_peak:.6f}"})
    return out, f"{-18.0 - album_i:+.2f} dB"


# --- cue sheet ---------------------------------------------------------------
def _q(s: str) -> str:
    return '"' + (s or "").replace('"', "'") + '"'


def cue_sheet(toc: TOC, album, files: dict[int, str] | None, image_file: str | None,
              tags: dict | None = None) -> str:
    """Single-file (image) sheet if image_file is given, otherwise a multi-file sheet."""
    lines = []
    if album.genre:
        lines.append(f"REM GENRE {_q(album.genre)}")
    if album.year:
        lines.append(f"REM DATE {album.year}")
    if album.mb_disc_id:
        lines.append(f"REM DISCID {toc.freedb_id():08X}")
        lines.append(f"REM MUSICBRAINZ_DISCID {album.mb_disc_id}")
    lines.append(f'REM COMMENT "Spindle {__version__}"')
    if toc.mcn:
        lines.append(f"CATALOG {toc.mcn}")
    lines += [f"PERFORMER {_q(album.artist)}", f"TITLE {_q(album.title)}"]
    base = toc.audio_tracks[0].start
    if image_file:
        lines.append(f"FILE {_q(os.path.basename(image_file))} WAVE")
    for t in toc.audio_tracks:
        tm = album.track(t.number)
        if not image_file:
            if not files or t.number not in files:
                continue
            lines.append(f"FILE {_q(os.path.basename(files[t.number]))} WAVE")
        lines.append(f"  TRACK {t.number:02d} AUDIO")
        lines.append(f"    TITLE {_q(tm.title)}")
        lines.append(f"    PERFORMER {_q(tm.artist or album.artist)}")
        if tm.isrc:
            lines.append(f"    ISRC {tm.isrc}")
        pos = t.start - base if image_file else 0
        lines.append(f"    INDEX 01 {msf_str(pos).replace('.', ':')}")
    return "\n".join(lines) + "\n"


def m3u(files: list[str], album) -> str:
    out = ["#EXTM3U"]
    for f in files:
        out.append(os.path.basename(f))
    return "\n".join(out) + "\n"


# --- extraction log -----------------------------------------------------------
def build_log(job) -> str:
    """job is a pipeline.RipJob after completion."""
    a, toc, cfg = job.album, job.toc, job.cfg
    d = cfg["drive"]
    w = []
    w.append(f"Spindle {__version__} extraction logfile from {datetime.now():%d. %B %Y, %H:%M}")
    w.append("")
    w.append(f"{a.artist} / {a.title}")
    w.append("")
    w.append(f"Used drive  : {job.drive.name}   Device: {job.drive.device}")
    w.append("")
    w.append(f"Ripper                      : {job.backend_name}")
    w.append(f"Read mode                   : Secure (paranoia: {d['paranoia']})")
    w.append(f"Read offset correction      : {job.offset:+d}")
    w.append(f"Max retries per sector      : {d['max_retries']}")
    w.append(f"Test & copy                 : {'Yes' if d['test_and_copy'] else 'No'}")
    w.append(f"Output                      : {', '.join(job.format_descriptions)}")
    w.append(f"MusicBrainz disc ID         : {toc.musicbrainz_id()}")
    w.append(f"FreeDB disc ID              : {toc.freedb_id():08x}")
    if a.source:
        w.append(f"Metadata source             : {a.source}" + (f" (release {a.mb_release_id})" if a.mb_release_id else ""))
    if job.cover:
        w.append(f"Cover art                   : {job.cover.source} {job.cover.width}x{job.cover.height}")
    w.append("")
    w.append("TOC of the extracted CD")
    w.append("")
    w.append("     Track |   Start  |  Length  | Start sector | End sector ")
    w.append("    ---------------------------------------------------------")
    for t in toc.tracks:
        w.append(f"      {t.number:>3}  | {msf_str(t.start):>8} | {msf_str(t.sectors):>8} |"
                 f"   {t.start:>8}   |  {t.end:>8}  " + ("" if t.audio else " (data)"))
    w.append("")
    if job.ar_note:
        w.append(job.ar_note)
        w.append("")
    for n in job.track_order:
        r = job.results.get(n)
        if not r:
            continue
        w.append(f"Track {n:2d}")
        w.append("")
        for f in r.files:
            w.append(f"     Filename {f}")
        w.append("")
        w.append(f"     Peak level {r.peak * 100:.1f} %")
        if r.test_crc is not None:
            w.append(f"     Test CRC {r.test_crc:08X}")
        w.append(f"     Copy CRC {r.copy_crc:08X}")
        if r.ar is not None:
            w.append(f"     AccurateRip v1 {r.ar_v1:08X}  v2 {r.ar_v2:08X}   {r.ar.text()}")
        if r.corrections:
            w.append(f"     Paranoia corrections {r.corrections}")
        if r.suspicious:
            w.append("     Suspicious positions " + ", ".join(msf_str(s - toc.track(n).start) for s in r.suspicious[:20]))
        if r.rg_gain:
            w.append(f"     ReplayGain {r.rg_gain}")
        w.append("     " + r.verdict)
        w.append("")
    ok = [n for n in job.track_order if n in job.results and job.results[n].good]
    acc = [n for n in job.track_order if n in job.results and job.results[n].ar is not None
           and job.results[n].ar.status == "accurate"]
    w.append("")
    if job.album_gain:
        w.append(f"Album ReplayGain {job.album_gain}")
    if acc and len(acc) == len(job.track_order):
        w.append("All tracks accurately ripped")
    elif acc:
        w.append(f"{len(acc)} of {len(job.track_order)} tracks accurately ripped")
    if len(ok) == len(job.track_order):
        w.append("No errors occurred")
    else:
        w.append(f"There were errors in {len(job.track_order) - len(ok)} track(s)")
    w.append("")
    w.append("End of status report")
    return "\n".join(w) + "\n"
