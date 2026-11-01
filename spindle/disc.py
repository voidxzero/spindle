"""Disc table-of-contents reading and disc identifier calculation.

Reads the TOC straight from the kernel (Linux cdrom ioctls), so no libdiscid is
needed, and computes the MusicBrainz disc ID, FreeDB/GnuDB ID and AccurateRip IDs.
"""
from __future__ import annotations

import base64
import fcntl
import glob
import hashlib
import os
import struct
from dataclasses import dataclass

# --- linux/cdrom.h ---------------------------------------------------------
CDROMEJECT = 0x5309
CDROMCLOSETRAY = 0x5319
CDROMREADTOCHDR = 0x5305
CDROMREADTOCENTRY = 0x5306
CDROM_DRIVE_STATUS = 0x5326
CDROM_GET_MCN = 0x5311
CDROM_LOCKDOOR = 0x5329
CDROM_LBA = 0x01
CDROM_LEADOUT = 0xAA
CDS_NO_INFO, CDS_NO_DISC, CDS_TRAY_OPEN, CDS_DRIVE_NOT_READY, CDS_DISC_OK = range(5)

PREGAP = 150              # 2 second lead-in, in frames (sectors)
FRAMES_PER_SECOND = 75
SAMPLES_PER_FRAME = 588   # stereo samples per sector
BYTES_PER_FRAME = 2352
DATA_TRACK_GAP = 11400    # gap between the audio session and data session on Enhanced CDs


@dataclass
class Track:
    number: int
    start: int            # LBA (no 150 pregap added)
    end: int              # LBA of last sector, inclusive
    audio: bool = True

    @property
    def sectors(self) -> int:
        return self.end - self.start + 1

    @property
    def seconds(self) -> float:
        return self.sectors / FRAMES_PER_SECOND

    @property
    def length_str(self) -> str:
        return msf_str(self.sectors, with_frames=False)


@dataclass
class TOC:
    first: int
    last: int
    tracks: list[Track]
    leadout: int          # LBA of the lead-out
    mcn: str = ""

    # ---- derived ----------------------------------------------------------
    @property
    def audio_tracks(self) -> list[Track]:
        return [t for t in self.tracks if t.audio]

    @property
    def audio_leadout(self) -> int:
        """Lead-out of the audio session (differs from the disc lead-out on Enhanced CDs)."""
        tracks = self.tracks
        if tracks and not tracks[-1].audio and len(tracks) > 1 and tracks[-2].audio:
            return tracks[-1].start - DATA_TRACK_GAP
        return self.leadout

    @property
    def last_audio(self) -> int:
        return self.audio_tracks[-1].number if self.audio_tracks else self.last

    @property
    def total_seconds(self) -> float:
        return (self.audio_leadout - self.tracks[0].start) / FRAMES_PER_SECOND

    def track(self, number: int) -> Track:
        for t in self.tracks:
            if t.number == number:
                return t
        raise KeyError(number)

    # ---- identifiers -------------------------------------------------------
    def musicbrainz_id(self) -> str:
        first, last = self.first, self.last_audio
        offsets = {t.number: t.start + PREGAP for t in self.audio_tracks}
        s = "%02X%02X%08X" % (first, last, self.audio_leadout + PREGAP)
        for i in range(1, 100):
            s += "%08X" % offsets.get(i, 0)
        digest = hashlib.sha1(s.encode("ascii")).digest()
        return base64.b64encode(digest).decode().replace("+", ".").replace("/", "_").replace("=", "-")

    def musicbrainz_toc(self) -> str:
        parts = [self.first, self.last_audio, self.audio_leadout + PREGAP]
        parts += [t.start + PREGAP for t in self.audio_tracks]
        return " ".join(str(p) for p in parts)

    def freedb_id(self) -> int:
        def digit_sum(n: int) -> int:
            return sum(int(c) for c in str(n))
        n = sum(digit_sum((t.start + PREGAP) // FRAMES_PER_SECOND) for t in self.tracks)
        t = (self.leadout + PREGAP) // FRAMES_PER_SECOND - (self.tracks[0].start + PREGAP) // FRAMES_PER_SECOND
        return ((n % 0xFF) << 24) | (t << 8) | len(self.tracks)

    def freedb_query(self) -> str:
        offs = " ".join(str(t.start + PREGAP) for t in self.tracks)
        secs = (self.leadout + PREGAP) // FRAMES_PER_SECOND
        return f"{self.freedb_id():08x} {len(self.tracks)} {offs} {secs}"

    def accuraterip_ids(self) -> tuple[int, int, int]:
        audio = self.audio_tracks
        leadout = self.audio_leadout
        id1 = sum(t.start for t in audio) + leadout
        id2 = sum(max(t.start, 1) * (i + 1) for i, t in enumerate(audio)) + leadout * (len(audio) + 1)
        return id1 & 0xFFFFFFFF, id2 & 0xFFFFFFFF, self.freedb_id()

    def accuraterip_url(self) -> str:
        id1, id2, cddb = self.accuraterip_ids()
        n = len(self.audio_tracks)
        return ("http://www.accuraterip.com/accuraterip/%x/%x/%x/dBAR-%03d-%08x-%08x-%08x.bin"
                % (id1 & 0xF, (id1 >> 4) & 0xF, (id1 >> 8) & 0xF, n, id1, id2, cddb))

    def to_dict(self) -> dict:
        return {"first": self.first, "last": self.last, "leadout": self.leadout, "mcn": self.mcn,
                "tracks": [[t.number, t.start, t.end, t.audio] for t in self.tracks]}

    @classmethod
    def from_offsets(cls, offsets: list[int], leadout: int, first: int = 1,
                     data_tracks: set[int] | None = None) -> "TOC":
        """Build a TOC from LBA track offsets (no pregap) and an LBA lead-out."""
        data_tracks = data_tracks or set()
        tracks = []
        for i, start in enumerate(offsets):
            num = first + i
            nxt = offsets[i + 1] if i + 1 < len(offsets) else leadout
            end = nxt - 1
            # On Enhanced CDs the last audio track ends before the session gap
            if (num + 1) in data_tracks and num not in data_tracks:
                end = nxt - DATA_TRACK_GAP - 1
            tracks.append(Track(num, start, end, num not in data_tracks))
        return cls(first, first + len(offsets) - 1, tracks, leadout)

    @classmethod
    def from_musicbrainz_toc(cls, toc: str) -> "TOC":
        p = [int(x) for x in toc.split()]
        first, _last, leadout = p[0], p[1], p[2]
        return cls.from_offsets([x - PREGAP for x in p[3:]], leadout - PREGAP, first)


def msf_str(frames: int, with_frames: bool = True) -> str:
    m, rem = divmod(frames, 60 * FRAMES_PER_SECOND)
    s, f = divmod(rem, FRAMES_PER_SECOND)
    return f"{m:02d}:{s:02d}.{f:02d}" if with_frames else f"{m}:{s:02d}"


# ---------------------------------------------------------------------------
# Physical drives
# ---------------------------------------------------------------------------
@dataclass
class DriveInfo:
    device: str
    vendor: str = ""
    model: str = ""
    revision: str = ""

    @property
    def name(self) -> str:
        n = f"{self.vendor} {self.model}".strip()
        return n or self.device

    @property
    def accuraterip_name(self) -> str:
        """The 'VENDOR - MODEL' form used by the AccurateRip drive offset list."""
        return f"{self.vendor} - {self.model}".upper()


def list_drives() -> list[DriveInfo]:
    drives = []
    for dev in sorted(glob.glob("/dev/sr[0-9]*")):
        name = os.path.basename(dev)
        info = DriveInfo(dev)
        base = f"/sys/block/{name}/device"
        for attr in ("vendor", "model", "rev"):
            try:
                with open(f"{base}/{attr}") as fh:
                    val = " ".join(fh.read().split())
            except OSError:
                val = ""
            setattr(info, "revision" if attr == "rev" else attr, val)
        drives.append(info)
    if not drives and os.path.exists("/dev/cdrom"):
        drives.append(DriveInfo(os.path.realpath("/dev/cdrom")))
    return drives


class DriveError(Exception):
    pass


def _open(device: str) -> int:
    try:
        return os.open(device, os.O_RDONLY | os.O_NONBLOCK)
    except PermissionError as e:
        raise DriveError(f"Permission denied opening {device}. Add yourself to the 'cdrom' "
                         f"(or 'optical') group and log in again.") from e
    except OSError as e:
        raise DriveError(f"Cannot open {device}: {e.strerror}") from e


def drive_status(device: str) -> int:
    try:
        fd = _open(device)
    except DriveError:
        return CDS_NO_INFO
    try:
        return fcntl.ioctl(fd, CDROM_DRIVE_STATUS, 0)
    except OSError:
        return CDS_NO_INFO
    finally:
        os.close(fd)


def read_toc(device: str) -> TOC:
    fd = _open(device)
    try:
        try:
            hdr = fcntl.ioctl(fd, CDROMREADTOCHDR, b"\0\0")
        except OSError as e:
            raise DriveError("No readable disc in the drive." if e.errno in (5, 123) else
                             f"Could not read the disc's table of contents: {e.strerror}") from e
        first, last = hdr[0], hdr[1]

        def entry(track: int) -> tuple[int, bool]:
            buf = struct.pack("BBBxiB3x", track, 0, CDROM_LBA, 0, 0)
            res = fcntl.ioctl(fd, CDROMREADTOCENTRY, buf)
            _trk, adr_ctrl, _fmt, lba, _mode = struct.unpack("BBBxiB3x", res)
            ctrl = adr_ctrl >> 4
            return lba, not (ctrl & 0x04)

        starts, data = [], set()
        for n in range(first, last + 1):
            lba, audio = entry(n)
            starts.append(lba)
            if not audio:
                data.add(n)
        leadout, _ = entry(CDROM_LEADOUT)
        toc = TOC.from_offsets(starts, leadout, first, data)
        try:
            mcn = fcntl.ioctl(fd, CDROM_GET_MCN, b"\0" * 14)
            mcn = mcn.split(b"\0")[0].decode("ascii", "ignore").strip()
            if mcn.strip("0"):
                toc.mcn = mcn
        except OSError:
            pass
        if not toc.audio_tracks:
            raise DriveError("This disc has no audio tracks.")
        return toc
    finally:
        os.close(fd)


def eject(device: str, close: bool = False) -> None:
    fd = _open(device)
    try:
        try:
            fcntl.ioctl(fd, CDROM_LOCKDOOR, 0)
        except OSError:
            pass
        fcntl.ioctl(fd, CDROMCLOSETRAY if close else CDROMEJECT, 0)
    except OSError as e:
        raise DriveError(f"Eject failed: {e.strerror}") from e
    finally:
        os.close(fd)
