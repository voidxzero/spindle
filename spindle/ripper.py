"""Secure audio extraction via cdparanoia / libcdio cd-paranoia, plus a simulated drive."""
from __future__ import annotations

import math
import os
import re
import shutil
import struct
import subprocess
import threading
import wave
import zlib
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from . import system
from .disc import BYTES_PER_FRAME, TOC, DriveError, read_toc

try:
    import fcntl
except ImportError:                  # Windows: no direct drive reads yet
    fcntl = None

CDROMREADAUDIO = 0x530E

ProgressCB = Callable[[float], None]

# cdparanoia -e status words that are routine; everything else is a correction or error
ROUTINE = {"read", "verify", "wrote", "overlap", "finished", "skip_check"}
SERIOUS = {"skip", "read_error", "readerr", "cache_error", "cacheerr", "dropped", "duped"}


class Cancelled(Exception):
    pass


@dataclass
class RipResult:
    path: str
    corrections: int = 0          # jitter/edge fixes paranoia performed
    errors: int = 0               # skips / read errors (likely audible damage)
    suspicious: list[int] = field(default_factory=list)   # sector numbers
    crc: int = 0
    peak: float = 0.0

    @property
    def ok(self) -> bool:
        return self.errors == 0


def find_paranoia() -> str | None:
    for name in ("cdparanoia", "cd-paranoia"):
        p = shutil.which(name)
        if p:
            return p
    return None


def wav_pcm(path: str) -> bytes:
    with wave.open(path, "rb") as w:
        return w.readframes(w.getnframes())


def analyse(path: str) -> tuple[int, float]:
    """CRC32 of the audio data (EAC-style copy CRC) and peak level (0..1)."""
    crc, peak = 0, 0
    with wave.open(path, "rb") as w:
        while True:
            data = w.readframes(588 * 1000)
            if not data:
                break
            crc = zlib.crc32(data, crc)
            a = np.frombuffer(data, dtype="<i2")
            if a.size:
                peak = max(peak, int(np.abs(a.astype(np.int32)).max()))
    return crc & 0xFFFFFFFF, peak / 32768.0


def write_wav_header(fh, n_bytes: int) -> None:
    fh.write(b"RIFF" + struct.pack("<I", 36 + n_bytes) + b"WAVE")
    fh.write(b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, 44100, 44100 * 4, 4, 16))
    fh.write(b"data" + struct.pack("<I", n_bytes))


class Drive:
    """Interface for anything that can rip audio from a disc."""
    device = ""
    name = ""

    def read_toc(self) -> TOC:
        raise NotImplementedError

    def rip(self, first_track: int, last_track: int, out: str, offset: int, paranoia: str,
            retries: int, progress: ProgressCB, cancel: threading.Event) -> RipResult:
        raise NotImplementedError

    def open_stream(self, start_lba: int, end_lba: int) -> "PCMStream":
        """Raw 16-bit stereo 44.1 kHz little-endian PCM for absolute sectors start_lba..end_lba
        (inclusive). Playback streams continuously across tracks, which makes it gapless."""
        raise NotImplementedError


class PCMStream:
    def read(self, n: int) -> bytes:
        raise NotImplementedError

    def close(self) -> None:
        pass


class _ProcStream(PCMStream):
    def __init__(self, cmd: list[str]):
        self.proc = system.popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)

    def read(self, n: int) -> bytes:
        return self.proc.stdout.read(n) if self.proc.stdout else b""

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.kill()
        try:
            self.proc.wait(3)
        except subprocess.TimeoutExpired:
            pass
        if self.proc.stdout:
            self.proc.stdout.close()


class _DirectStream(PCMStream):
    """Reads CD audio straight from the drive with the kernel's CDROMREADAUDIO ioctl.
    No helper process, so playback starts and seeks immediately."""
    FRAMES = 25                                     # 1/3 s per read: low latency, few syscalls

    def __init__(self, device: str, start_lba: int, end_lba: int):
        import ctypes
        self.fd = os.open(device, os.O_RDONLY | os.O_NONBLOCK)
        self.pos, self.end = start_lba, end_lba
        self.buf = ctypes.create_string_buffer(self.FRAMES * BYTES_PER_FRAME)
        self.addr = ctypes.addressof(self.buf)
        self.pending = b""
        # read the first block now, so a drive that doesn't support this fails here (-> fallback)
        self.pending = self._read_frames(min(self.FRAMES, self.end - self.pos + 1))

    def _read_frames(self, n: int) -> bytes:
        if n <= 0:
            return b""
        req = struct.pack("iBiP", self.pos, 1, n, self.addr)    # cdrom_read_audio{addr, CDROM_LBA, nframes, buf}
        try:
            fcntl.ioctl(self.fd, CDROMREADAUDIO, req)
        except OSError:
            if n > 1:                                           # scratched spot: retry frame by frame
                return b"".join(self._read_frames_single() for _ in range(n))
            raise
        self.pos += n
        return self.buf.raw[: n * BYTES_PER_FRAME]

    def _read_frames_single(self) -> bytes:
        try:
            return self._read_frames(1)
        except OSError:
            self.pos += 1
            return b"\0" * BYTES_PER_FRAME                     # unreadable sector: play silence

    def read(self, n: int) -> bytes:
        if self.pending:
            out, self.pending = self.pending, b""
            return out
        return self._read_frames(min(self.FRAMES, self.end - self.pos + 1))

    def close(self) -> None:
        try:
            os.close(self.fd)
        except OSError:
            pass


class _SimStream(PCMStream):
    def __init__(self, drive: "SimulatedDrive", start_lba: int, end_lba: int):
        self.toc = drive.read_toc()
        self.pos = start_lba * 588        # absolute sample
        self.end = (end_lba + 1) * 588
        self.true_offset = getattr(drive, "true_offset", 0)   # raw[k] = true audio[k - offset]

    def read(self, n: int) -> bytes:
        count = min(n // 4, self.end - self.pos)
        if count <= 0:
            return b""
        lba = self.pos // 588
        track = next((t.number for t in self.toc.tracks if t.start <= lba <= t.end), 1)
        tt = (np.arange(self.pos, self.pos + count, dtype=np.float64) - self.true_offset) / 44100.0
        self.pos += count
        return _tone(tt, track).tobytes()


def _tone(t, track: int):
    freq = 220.0 * (1 + (track % 7) / 7.0)
    left = 0.4 * np.sin(2 * math.pi * freq * t) * (0.6 + 0.4 * np.sin(t * 0.7))
    right = 0.4 * np.sin(2 * math.pi * freq * 1.5 * t)
    pcm = np.empty(len(t) * 2, dtype="<i2")
    pcm[0::2] = (left * 32767).astype(np.int16)
    pcm[1::2] = (right * 32767).astype(np.int16)
    return pcm


def _span_time(sectors: int) -> str:
    """cdparanoia span offset: [mm:ss.ff] where ff is sectors (1/75 s)."""
    m, rem = divmod(sectors, 75 * 60)
    sec, f = divmod(rem, 75)
    return f"{m}:{sec:02d}.{f:02d}"


class ParanoiaDrive(Drive):
    def __init__(self, device: str, name: str = ""):
        self.device = device
        self.name = name or device
        self.binary = find_paranoia()

    def read_toc(self) -> TOC:
        return read_toc(self.device)

    def open_stream(self, start_lba, end_lba):
        try:
            return _DirectStream(self.device, start_lba, end_lba)
        except OSError:
            pass                                   # drive/kernel won't do raw audio reads: use cdparanoia
        if not self.binary:
            raise DriveError("This drive can't stream audio directly and cdparanoia isn't installed.")
        toc = self.read_toc()
        first = next(t for t in toc.audio_tracks if t.start <= start_lba <= t.end)
        last = next(t for t in toc.audio_tracks if t.start <= end_lba <= t.end)
        off = start_lba - first.start
        span = f"{first.number}[{_span_time(off)}]-{last.number}" if off else f"{first.number}-{last.number}"
        # -Z: no paranoia - fine for listening and keeps up in real time; -r: raw little-endian
        return _ProcStream([self.binary, "-q", "-Z", "-r", "-d", self.device, "--", span, "-"])

    def rip(self, first_track, last_track, out, offset, paranoia, retries, progress, cancel):
        if not self.binary:
            raise DriveError("cdparanoia is not installed (package 'cdparanoia' or 'libcdio-utils').")
        toc = self.read_toc()
        start = toc.track(first_track).start
        end = toc.track(last_track).end
        span = f"{first_track}-{last_track}" if first_track != last_track else str(first_track)
        cmd = [self.binary, "-d", self.device, "-e", "-O", str(offset), "-z=%d" % retries]
        if paranoia == "no-extra":
            cmd.append("-Y")
        elif paranoia == "off":
            cmd.append("-Z")
        cmd += ["--", span, out]
        proc = system.popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                text=True, errors="replace", bufsize=1)
        res = RipResult(out)
        pat = re.compile(r"##:\s*-?\d+\s*\[(\w+)\]\s*@\s*(\d+)")
        total = max(1, end - start + 1)
        tail: list[str] = []
        best = 0.0
        try:
            for line in proc.stderr:
                if cancel.is_set():
                    proc.terminate()
                    proc.wait(5)
                    raise Cancelled()
                m = pat.search(line)
                if not m:
                    tail = (tail + [line.strip()])[-8:]
                    continue
                word, pos = m.group(1).lower(), int(m.group(2))
                sector = pos // (BYTES_PER_FRAME // 2)     # positions are in 16-bit words
                if word in ROUTINE:
                    if word in ("read", "wrote"):
                        # paranoia re-reads behind itself while verifying; never let the bar move back
                        frac = min(1.0, max(0.0, (sector - start) / total))
                        if frac > best:
                            best = frac
                            progress(frac)
                    continue
                if word in SERIOUS:
                    res.errors += 1
                    if sector not in res.suspicious:
                        res.suspicious.append(sector)
                else:
                    res.corrections += 1
            code = proc.wait()
        finally:
            if proc.poll() is None:
                proc.kill()
        if code != 0 or not os.path.exists(out):
            raise DriveError("cdparanoia failed: " + (" ".join(tail[-3:]) or f"exit {code}"))
        progress(1.0)
        res.crc, res.peak = analyse(out)
        return res


class SimulatedDrive(Drive):
    """A fake drive for trying Spindle without a disc (spindle --simulate)."""

    def __init__(self, toc: TOC | None = None, flaky_track: int = 0):
        self.device = "simulated"
        self.name = "SPINDLE Virtual CD-ROM"
        self.flaky_track = flaky_track
        self._pass = 0
        # Pink Floyd - "The Dark Side of the Moon" style 10 track layout
        self._toc = toc or TOC.from_offsets(
            [0, 4380, 21155, 38850, 54440, 72450, 85800, 103050, 120680, 137560], 155400)

    def read_toc(self) -> TOC:
        return self._toc

    def open_stream(self, start_lba, end_lba):
        return _SimStream(self, start_lba, end_lba)

    def rip(self, first_track, last_track, out, offset, paranoia, retries, progress, cancel):
        toc = self._toc
        start, end = toc.track(first_track).start, toc.track(last_track).end
        sectors = end - start + 1
        self._pass += 1
        res = RipResult(out)
        with open(out, "wb") as fh:
            write_wav_header(fh, sectors * BYTES_PER_FRAME)
            chunk = 75 * 4
            for s in range(0, sectors, chunk):
                if cancel.is_set():
                    raise Cancelled()
                n = min(chunk, sectors - s)
                t0 = (start + s) * 588
                t = np.arange(t0, t0 + n * 588, dtype=np.float64) / 44100.0
                pcm = _tone(t, first_track)
                if first_track == self.flaky_track and self._pass % 2 and s == chunk:
                    pcm[1000:1010] = 0
                    res.corrections += 3
                fh.write(pcm.tobytes())
                progress((s + n) / sectors)
                if os.environ.get("SPINDLE_SIM_SLOW"):
                    threading.Event().wait(0.01)
        res.crc, res.peak = analyse(out)
        return res


def make_drive(device: str, name: str = "") -> Drive:
    """The right drive for this system: cdparanoia on Linux, Windows' CD-ROM interface on Windows."""
    if system.WINDOWS:
        from .windrive import WinDrive
        return WinDrive(device, name)
    return ParanoiaDrive(device, name)


def can_rip() -> bool:
    """Whether real discs can be ripped here (Windows needs nothing; Linux needs cdparanoia)."""
    return system.WINDOWS or find_paranoia() is not None
