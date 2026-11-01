"""AccurateRip v1/v2 checksums, database lookup and drive offset detection."""
from __future__ import annotations

import html
import re
import struct
from dataclasses import dataclass

import numpy as np

from . import net
from .disc import BYTES_PER_FRAME, TOC, DriveInfo

SKIP = 5 * 588  # samples ignored at the very start of track 1 and very end of the last track


def checksums(pcm: bytes, first: bool, last: bool) -> tuple[int, int]:
    """Return (ARv1, ARv2) for 16-bit stereo little-endian PCM of one track."""
    allsamples = np.frombuffer(pcm, dtype="<u4", count=len(pcm) // 4)
    n = len(allsamples)
    if n == 0:
        return 0, 0
    lo = SKIP if first else 1                  # 1-based multipliers, inclusive range
    hi = n - SKIP if last else n
    v1 = v2 = np.uint64(0)
    mask32 = np.uint64(0xFFFFFFFF)
    chunk = 1 << 22
    with np.errstate(over="ignore"):           # wrap-around is intended: only the low 32 bits matter
        for start in range(max(lo, 1), hi + 1, chunk):
            stop = min(start + chunk - 1, hi)
            m = np.arange(start, stop + 1, dtype=np.uint64)
            s = allsamples[start - 1:stop].astype(np.uint64)
            prod = s * m                       # < 2^32 * 2^28: no overflow per product
            v1 += prod.sum(dtype=np.uint64)
            v2 += (prod & mask32).sum(dtype=np.uint64) + (prod >> np.uint64(32)).sum(dtype=np.uint64)
    return int(v1 & mask32), int(v2 & mask32)


@dataclass
class ARResponse:
    track_count: int
    id1: int
    id2: int
    cddb: int
    entries: list[tuple[int, int, int]]   # (confidence, crc, crc450) per track


def parse_response(data: bytes) -> list[ARResponse]:
    out, pos = [], 0
    while pos + 13 <= len(data):
        count, id1, id2, cddb = struct.unpack_from("<BIII", data, pos)
        pos += 13
        entries = []
        for _ in range(count):
            if pos + 9 > len(data):
                break
            conf, crc, crc450 = struct.unpack_from("<BII", data, pos)
            entries.append((conf, crc, crc450))
            pos += 9
        out.append(ARResponse(count, id1, id2, cddb, entries))
    return out


def lookup(toc: TOC) -> list[ARResponse]:
    """Fetch AccurateRip results for a disc. Returns [] when the disc isn't in the database."""
    try:
        return parse_response(net.get(toc.accuraterip_url(), timeout=15))
    except net.HttpError as e:
        if e.status == 404:
            return []
        raise


@dataclass
class TrackVerdict:
    status: str            # "accurate" | "mismatch" | "absent" | "unavailable"
    confidence: int = 0
    version: int = 0
    total: int = 0         # total submissions for this track across pressings

    def text(self) -> str:
        if self.status == "accurate":
            return f"Accurate (confidence {self.confidence}) [v{self.version}]"
        if self.status == "mismatch":
            return f"Not accurate ({self.total} submissions)"
        if self.status == "unavailable":
            return "AccurateRip unreachable"
        return "Not in database"


def verify(track_index: int, v1: int, v2: int, responses: list[ARResponse]) -> TrackVerdict:
    total, best = 0, None
    for r in responses:
        if track_index >= len(r.entries):
            continue
        conf, crc, _ = r.entries[track_index]
        total += conf
        for ver, ours in ((2, v2), (1, v1)):
            if ours and crc == ours and (best is None or conf > best.confidence):
                best = TrackVerdict("accurate", conf, ver)
    if best:
        best.total = total
        return best
    return TrackVerdict("mismatch" if total else "absent", total=total)


# --- drive offsets ----------------------------------------------------------
OFFSETS_URL = "http://www.accuraterip.com/driveoffsets.htm"


def _norm(s: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def parse_offset_table(page: str) -> dict[str, int]:
    table = {}
    for row in re.findall(r"<tr.*?</tr>", page, re.S | re.I):
        cells = [html.unescape(re.sub(r"<[^>]+>", "", c)).strip()
                 for c in re.findall(r"<td.*?</td>", row, re.S | re.I)]
        if len(cells) >= 2 and re.fullmatch(r"[+-]?\d+", cells[1].replace(" ", "")):
            table[_norm(cells[0])] = int(cells[1].replace(" ", ""))
    return table


def find_offset(drive: DriveInfo, table: dict[str, int]) -> int | None:
    key = _norm(drive.accuraterip_name)
    if key in table:
        return table[key]
    model = _norm(drive.model)
    if model:
        hits = [v for k, v in table.items() if k.endswith(model) or model in k]
        if len(set(hits)) == 1:
            return hits[0]
    return None


def detect_offset(drive: DriveInfo) -> int | None:
    page = net.get(OFFSETS_URL, timeout=20).decode("latin-1")
    return find_offset(drive, parse_offset_table(page))


# --- key-disc offset detection ---------------------------------------------------------------------
# The AccurateRip database stores, for every track, a checksum of frame 450 (588 samples, 6 s into the
# track): sum of sample * position (1..588), mod 2^32. Reading that frame from the drive with no offset
# correction and testing every candidate shift shows which correction makes the drive's audio line up
# with everyone else's - i.e. the drive's read offset. (Same method as EAC / CUETools.)
FRAME = 588
F450 = 450


def frame450_crcs(raw_samples: np.ndarray, first_sample: int, track_start_sample: int,
                  max_offset: int) -> dict[int, int]:
    """CRC of frame 450 for every correction in [-max_offset, +max_offset].

    raw_samples: uint32 stereo samples read with no offset correction, the first being absolute sample
    ``first_sample``. Correction c means the true audio is raw[k + c]."""
    w = np.arange(1, FRAME + 1, dtype=np.uint64)
    base = track_start_sample + F450 * FRAME - first_sample          # index of the frame at c = 0
    lo, hi = base - max_offset, base + max_offset + FRAME
    if lo < 0 or hi > len(raw_samples):
        raise ValueError("not enough audio read around frame 450")
    seg = raw_samples[lo:hi].astype(np.uint64)
    windows = np.lib.stride_tricks.sliding_window_view(seg, FRAME)    # (2*max_offset+1, 588)
    with np.errstate(over="ignore"):
        crcs = (windows * w).sum(axis=1, dtype=np.uint64) & np.uint64(0xFFFFFFFF)
    return {c: int(v) for c, v in zip(range(-max_offset, max_offset + 1), crcs)}


@dataclass
class OffsetResult:
    offset: int
    tracks_matched: int
    tracks_tested: int


def detect_offset_keydisc(drive, toc: TOC, responses: list[ARResponse], max_offset: int = 3000,
                          progress=lambda frac: None, cancel=None) -> list[OffsetResult]:
    """Find the drive's read offset from a disc that's in the AccurateRip database.
    Returns candidates, best first. Each track votes once for every offset whose frame-450 CRC matches
    one of the database's values for that track (several pressings may give several offsets)."""
    audio = toc.audio_tracks
    margin = max_offset // FRAME + 2
    tested, votes = 0, {}
    usable = [(i, t) for i, t in enumerate(audio) if t.sectors > F450 + 1 + margin]
    for n, (idx, t) in enumerate(usable):
        if cancel is not None and cancel.is_set():
            break
        wanted = {r.entries[idx][2] for r in responses if idx < len(r.entries) and r.entries[idx][2]}
        if not wanted:
            continue
        start_lba = t.start + F450 - margin
        end_lba = t.start + F450 + 1 + margin
        stream = drive.open_stream(start_lba, end_lba)
        try:
            need = (end_lba - start_lba + 1) * BYTES_PER_FRAME
            buf = b""
            while len(buf) < need:
                chunk = stream.read(need - len(buf))
                if not chunk:
                    break
                buf += chunk
        finally:
            stream.close()
        samples = np.frombuffer(buf[: len(buf) // 4 * 4], dtype="<u4")
        try:
            crcs = frame450_crcs(samples, start_lba * FRAME, t.start * FRAME, max_offset)
        except ValueError:
            continue
        tested += 1
        for c, v in crcs.items():
            if v in wanted:
                votes[c] = votes.get(c, 0) + 1
        progress((n + 1) / len(usable))
    ranked = sorted(votes.items(), key=lambda kv: (-kv[1], abs(kv[0])))
    return [OffsetResult(c, v, tested) for c, v in ranked]
