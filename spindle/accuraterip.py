"""AccurateRip v1/v2 checksums, database lookup and drive offset detection."""
from __future__ import annotations

import html
import re
import struct
from dataclasses import dataclass

import numpy as np

from . import net
from .disc import TOC, DriveInfo

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
