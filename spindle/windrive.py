"""CD drives on Windows, through Windows' own CD-ROM interface (DeviceIoControl). Needs no administrator
rights and nothing installed.

Reading is "burst" style with careful re-reads: audio is read in blocks; a block that fails is re-read
sector by sector, each sector up to `retries` times, and only a sector that never reads cleanly is
filled with silence (and counted as an error). Before reading a stretch of the disc a second time
(test & copy, re-reads) other sectors are read first, so the drive's cache can't hand back the first
read. What makes the rip secure is the same as on Linux: AccurateRip checks every track, and
test & copy compares two independent reads.

The parts that don't touch Windows (TOC/descriptor parsing and RawReader) are tested on any system."""
from __future__ import annotations

import struct
import threading
from typing import Callable

from .disc import (BYTES_PER_FRAME, CDS_DISC_OK, CDS_NO_DISC, CDS_NO_INFO, PREGAP, TOC, DriveError,
                   DriveInfo)

# --- DeviceIoControl codes (winioctl.h / ntddcdrm.h) -------------------------------------------------
IOCTL_CDROM_READ_TOC = 0x00024000
IOCTL_CDROM_RAW_READ = 0x0002403E
IOCTL_STORAGE_CHECK_VERIFY2 = 0x002D0800
IOCTL_STORAGE_MEDIA_REMOVAL = 0x002D4804
IOCTL_STORAGE_EJECT_MEDIA = 0x002D4808
IOCTL_STORAGE_LOAD_MEDIA = 0x002D480C
IOCTL_STORAGE_QUERY_PROPERTY = 0x002D1400
CDDA = 2                          # TRACK_MODE_TYPE for RAW_READ_INFO
ERROR_NOT_READY = 21
DRIVE_CDROM = 5

BLOCK = 20                        # sectors per read (47 KB; every drive/driver accepts this)
FLUSH_SECTORS = 3000              # ~7 MB read elsewhere to push a stretch out of the drive's cache


# --- parsing (pure, testable anywhere) ----------------------------------------------------------------
def msf_lba(m: int, s: int, f: int) -> int:
    return (m * 60 + s) * 75 + f - PREGAP


def parse_toc(buf: bytes) -> TOC:
    """CDROM_TOC (MSF addresses) -> TOC. Each TRACK_DATA is 8 bytes:
    Reserved, Control(low 4 bits)/Adr(high 4 bits), TrackNumber, Reserved1, Address[0, M, S, F]."""
    if len(buf) < 12:
        raise DriveError("No readable disc in the drive.")
    first, last = buf[2], buf[3]
    if not (1 <= first <= last <= 99):
        raise DriveError("No readable disc in the drive.")
    starts, data, leadout = [], set(), None
    for i in range(last - first + 2):
        o = 4 + 8 * i
        if o + 8 > len(buf):
            break
        ctrl, num = buf[o + 1] & 0x0F, buf[o + 2]
        lba = msf_lba(buf[o + 5], buf[o + 6], buf[o + 7])
        if num == 0xAA:
            leadout = lba
        else:
            starts.append(lba)
            if ctrl & 0x04:
                data.add(num)
    if leadout is None or len(starts) != last - first + 1:
        raise DriveError("Could not read the disc's table of contents.")
    toc = TOC.from_offsets(starts, leadout, first, data)
    if not toc.audio_tracks:
        raise DriveError("This disc has no audio tracks.")
    return toc


def parse_descriptor(buf: bytes) -> tuple[str, str, str]:
    """STORAGE_DEVICE_DESCRIPTOR -> (vendor, model, revision)."""
    if len(buf) < 24:
        return "", "", ""
    vend_off, prod_off, rev_off = struct.unpack_from("<III", buf, 12)

    def s(off: int) -> str:
        if not off or off >= len(buf):
            return ""
        end = buf.find(b"\0", off)
        return " ".join(buf[off:end if end >= 0 else len(buf)].decode("ascii", "replace").split())
    return s(vend_off), s(prod_off), s(rev_off)


# --- reading audio (pure logic; the device is just a read function) --------------------------------------
ReadFn = Callable[[int, int], bytes]          # (lba, sectors) -> raw CDDA bytes; raises OSError on failure


class RawReader:
    """Reads a stretch of audio with offset correction, re-reads and cache flushing."""

    def __init__(self, read: ReadFn, audio_end: int, block: int = BLOCK, done: list | None = None):
        self.read = read
        self.audio_end = audio_end            # first LBA that isn't readable audio (lead-out / data gap)
        self.block = block
        # stretches already read (kept by the drive across rips): reading one again needs a cache flush
        self._done: list[tuple[int, int]] = done if done is not None else []
        self.corrections = 0                  # sectors that needed re-reading but then read fine
        self.errors = 0                       # sectors that never read cleanly (filled with silence)
        self.suspicious: list[int] = []

    def _sector(self, lba: int, retries: int) -> bytes:
        for attempt in range(max(1, retries)):
            try:
                data = self.read(lba, 1)
                if len(data) == BYTES_PER_FRAME:
                    if attempt:
                        self.corrections += 1
                    return data
            except OSError:
                pass
        self.errors += 1
        self.suspicious.append(lba)
        return b"\0" * BYTES_PER_FRAME

    def _block(self, lba: int, n: int, retries: int) -> bytes:
        """n sectors from lba; anything outside the readable audio is silence (no overreading)."""
        out = bytearray()
        pos = lba
        while pos < lba + n:
            if pos < 0 or pos >= self.audio_end:
                out += b"\0" * BYTES_PER_FRAME
                pos += 1
                continue
            run = min(lba + n, self.audio_end) - pos
            try:
                data = self.read(pos, run)
                if len(data) != run * BYTES_PER_FRAME:
                    raise OSError("short read")
                out += data
            except OSError:                     # scratched spot: go sector by sector
                for k in range(run):
                    out += self._sector(pos + k, retries)
            pos += run
        return bytes(out)

    def _flush_cache(self, first: int, last: int, gap: int = 1000) -> None:
        """Read up to FLUSH_SECTORS somewhere at least `gap` sectors away from first..last, on whichever
        side has more room. (On a very short disc there may be no such place; then there's nothing to do.)"""
        after = (last + 1 + gap, self.audio_end)              # [start, end)
        before = (0, first - gap)
        lo, hi = max(after, before, key=lambda r: r[1] - r[0])
        if hi - lo < self.block:
            return
        if (lo, hi) == before:
            lo = max(lo, hi - FLUSH_SECTORS)                  # the stretch nearest the start of the disc
        hi = min(hi, lo + FLUSH_SECTORS)
        for lba in range(lo, hi, self.block):
            try:
                self.read(lba, min(self.block, hi - lba))
            except OSError:
                pass

    def read_range(self, first: int, last: int, offset: int, write: Callable[[bytes], None], retries: int = 20,
                   progress=lambda f: None, cancel: threading.Event | None = None) -> None:
        """Write the audio of sectors first..last (inclusive), shifted by `offset` samples (read offset
        correction: positive = take audio from later on the disc), to `write`."""
        from .ripper import Cancelled
        start_b = first * BYTES_PER_FRAME + offset * 4
        end_b = (last + 1) * BYTES_PER_FRAME + offset * 4
        s0 = start_b // BYTES_PER_FRAME
        s1 = -(-end_b // BYTES_PER_FRAME) - 1
        if any(a <= s1 and s0 <= b for a, b in self._done):
            self._flush_cache(s0, s1)
        total = s1 - s0 + 1
        pos = s0
        while pos <= s1:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            n = min(self.block, s1 - pos + 1)
            data = self._block(pos, n, retries)
            lo = max(start_b, pos * BYTES_PER_FRAME) - pos * BYTES_PER_FRAME
            hi = min(end_b, (pos + n) * BYTES_PER_FRAME) - pos * BYTES_PER_FRAME
            write(data[lo:hi])
            pos += n
            progress((pos - s0) / total)
        self._done.append((s0, s1))


# --- Windows calls -------------------------------------------------------------------------------------
class Device:
    """An open CD drive (\\\\.\\D:)."""

    def __init__(self, device: str):
        import ctypes
        from ctypes import wintypes
        self._ct, self._wt = ctypes, wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                  wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        k.CreateFileW.restype = wintypes.HANDLE
        k.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                      wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                      wintypes.LPVOID]
        k.DeviceIoControl.restype = wintypes.BOOL
        k.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k = k
        path = "\\\\.\\" + device.rstrip("\\").rstrip(":") + ":"
        GENERIC_READ, SHARE_RW, OPEN_EXISTING = 0x80000000, 0x3, 3
        h = k.CreateFileW(path, GENERIC_READ, SHARE_RW, None, OPEN_EXISTING, 0, None)
        if h in (None, wintypes.HANDLE(-1).value):
            err = ctypes.get_last_error()
            raise DriveError(f"Cannot open drive {device}: {ctypes.FormatError(err).strip()}")
        self.h = h
        self.buf = ctypes.create_string_buffer(BLOCK * BYTES_PER_FRAME)

    def ioctl(self, code: int, inp: bytes | None = None, out_size: int = 0, out=None) -> bytes:
        ct = self._ct
        ib = ct.create_string_buffer(inp, len(inp)) if inp else None
        ob = out if out is not None else (ct.create_string_buffer(out_size) if out_size else None)
        got = self._wt.DWORD(0)
        ok = self._k.DeviceIoControl(self.h, code, ib, len(inp) if inp else 0, ob,
                                     out_size if ob is not None else 0, ct.byref(got), None)
        if not ok:
            err = ct.get_last_error()
            e = OSError(err, ct.FormatError(err).strip())
            e.winerror_code = err
            raise e
        return ob.raw[:got.value] if ob is not None else b""

    def read(self, lba: int, n: int) -> bytes:
        """n raw audio sectors (2352 bytes each) from lba. Raises OSError on a read error."""
        if n > BLOCK:
            return b"".join(self.read(lba + i, min(BLOCK, n - i)) for i in range(0, n, BLOCK))
        info = struct.pack("<qII", lba * 2048, n, CDDA)        # RAW_READ_INFO
        return self.ioctl(IOCTL_CDROM_RAW_READ, info, n * BYTES_PER_FRAME, self.buf)

    def close(self) -> None:
        if getattr(self, "h", None):
            self._k.CloseHandle(self.h)
            self.h = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()


def list_drives() -> list[DriveInfo]:
    import ctypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    mask = k.GetLogicalDrives()
    drives = []
    for i in range(26):
        if not mask & (1 << i):
            continue
        letter = f"{chr(65 + i)}:"
        if k.GetDriveTypeW(letter + "\\") != DRIVE_CDROM:
            continue
        info = DriveInfo(letter)
        try:
            with Device(letter) as d:
                info.vendor, info.model, info.revision = parse_descriptor(
                    d.ioctl(IOCTL_STORAGE_QUERY_PROPERTY, struct.pack("<II4x", 0, 0), 1024))
        except (DriveError, OSError):
            pass
        drives.append(info)
    return drives


def drive_status(device: str) -> int:
    """CDS_DISC_OK or CDS_NO_DISC. (Windows doesn't say whether the tray is open.)"""
    try:
        with Device(device) as d:
            d.ioctl(IOCTL_STORAGE_CHECK_VERIFY2)
            return CDS_DISC_OK
    except OSError as e:
        return CDS_NO_DISC if getattr(e, "winerror_code", 0) == ERROR_NOT_READY else CDS_NO_INFO
    except DriveError:
        return CDS_NO_INFO


def read_toc(device: str) -> TOC:
    with Device(device) as d:
        try:
            buf = d.ioctl(IOCTL_CDROM_READ_TOC, None, 804)
        except OSError as e:
            if getattr(e, "winerror_code", 0) == ERROR_NOT_READY:
                raise DriveError("No readable disc in the drive.") from e
            raise DriveError(f"Could not read the disc's table of contents: {e.strerror}") from e
    return parse_toc(buf)


def eject(device: str, close: bool = False) -> None:
    with Device(device) as d:
        try:
            if not close:
                try:
                    d.ioctl(IOCTL_STORAGE_MEDIA_REMOVAL, b"\0")       # make sure the tray isn't locked
                except OSError:
                    pass
            d.ioctl(IOCTL_STORAGE_LOAD_MEDIA if close else IOCTL_STORAGE_EJECT_MEDIA)
        except OSError as e:
            raise DriveError(f"{'Closing the tray' if close else 'Eject'} failed: {e.strerror}") from e


# --- the Drive Spindle rips and plays with --------------------------------------------------------------
def _drive_class():
    from .ripper import Drive, PCMStream, RipResult, analyse, write_wav_header

    class _Stream(PCMStream):
        def __init__(self, device: str, start: int, end: int, audio_end: int):
            self.dev = Device(device)
            self.reader = RawReader(self.dev.read, audio_end)
            self.pos, self.end = start, end

        def read(self, n: int) -> bytes:
            if self.pos > self.end:
                return b""
            k = min(BLOCK, self.end - self.pos + 1)
            data = self.reader._block(self.pos, k, retries=2)    # playback: don't stall on bad spots
            self.pos += k
            return data

        def close(self) -> None:
            self.dev.close()

    class WinDrive(Drive):
        def __init__(self, device: str, name: str = ""):
            self.device, self.name = device, name or device
            self.binary = "Windows CD-ROM interface"         # shown in the rip log
            self.read_mode = "Burst with re-reads (verified by AccurateRip / test & copy)"
            self._toc: TOC | None = None
            self._done: list[tuple[int, int]] = []           # what's been read from this disc so far

        def read_toc(self) -> TOC:
            self._toc = read_toc(self.device)
            self._done = []
            return self._toc

        def rip(self, first_track, last_track, out, offset, paranoia, retries, progress, cancel):
            toc = self._toc or self.read_toc()
            start, end = toc.track(first_track).start, toc.track(last_track).end
            res = RipResult(out)
            n_bytes = (end - start + 1) * BYTES_PER_FRAME
            with Device(self.device) as dev:                 # closed after every pass: eject stays free
                reader = RawReader(dev.read, toc.audio_leadout, done=self._done)
                try:
                    with open(out, "wb") as fh:
                        write_wav_header(fh, n_bytes)
                        reader.read_range(start, end, int(offset), fh.write, max(1, int(retries)),
                                          progress, cancel)
                except OSError as e:
                    raise DriveError(f"Reading the disc failed: {e}") from e
            res.corrections, res.errors, res.suspicious = reader.corrections, reader.errors, reader.suspicious
            progress(1.0)
            res.crc, res.peak = analyse(out)
            return res

        def open_stream(self, start_lba, end_lba):
            toc = self._toc or self.read_toc()
            return _Stream(self.device, start_lba, end_lba, toc.audio_leadout)

    return WinDrive


_cls = None


def WinDrive(device: str, name: str = ""):
    """The Windows drive (built lazily: ripper imports disc, which imports this module)."""
    global _cls
    if _cls is None:
        _cls = _drive_class()
    return _cls(device, name)

