"""Windows drive logic, run on any system: TOC/descriptor parsing, offset-corrected reading, re-reads,
cache flushing, and a full rip through the pipeline with the Windows drive talking to a fake disc."""
import os, struct, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest import mock

from spindle import windrive
from spindle.disc import BYTES_PER_FRAME, TOC
from spindle.ripper import SimulatedDrive


def _msf(lba):
    f = lba + 150
    return f // 4500, (f // 75) % 60, f % 75


def make_toc_buf(starts, leadout, data=()):
    first, last = 1, len(starts)
    out = bytearray(struct.pack(">H", 2 + 8 * (len(starts) + 1)) + bytes([first, last]))
    for i, lba in enumerate(starts + [leadout]):
        num = 0xAA if i == len(starts) else i + 1
        ctrl = 0x04 if num in data else 0x00
        m, s, f = _msf(lba)
        out += bytes([0, (0x1 << 4) | ctrl, num, 0, 0, m, s, f])
    return bytes(out).ljust(804, b"\0")


def test_parse_toc():
    toc = windrive.parse_toc(make_toc_buf([0, 15213, 32164], 46442))
    assert [(t.number, t.start, t.end, t.audio) for t in toc.tracks] == \
        [(1, 0, 15212, True), (2, 15213, 32163, True), (3, 32164, 46441, True)]
    assert toc.leadout == 46442
    # Enhanced CD: data track 3, audio ends 11400 sectors before it
    enh = windrive.parse_toc(make_toc_buf([0, 10000, 50000], 70000, data={3}))
    assert [t.audio for t in enh.tracks] == [True, True, False]
    assert enh.audio_leadout == 50000 - 11400
    try:
        windrive.parse_toc(make_toc_buf([0], 1000, data={1}))
        raise AssertionError("data-only disc accepted")
    except windrive.DriveError:
        pass


def test_parse_descriptor():
    buf = bytearray(64)
    struct.pack_into("<II4BIII", buf, 0, 1, 64, 5, 0, 1, 0, 32, 41, 58)
    buf[32:41] = b"HL-DT-ST\0"
    buf[41:58] = b"DVDRAM GH24NSD1 \0"
    buf[58:63] = b"1.00\0"
    assert windrive.parse_descriptor(bytes(buf)) == ("HL-DT-ST", "DVDRAM GH24NSD1", "1.00")


class FakeDisc:
    """Raw sector reads from a simulated disc, with scripted failures and a log of what was read."""

    def __init__(self, toc, bad=None, flaky=None):
        sim = SimulatedDrive(toc)
        data = bytearray(toc.leadout * BYTES_PER_FRAME)       # data tracks/gaps stay silent
        for t in toc.audio_tracks:                            # the tone depends on the track
            data[t.start * BYTES_PER_FRAME:(t.end + 1) * BYTES_PER_FRAME] = \
                sim.open_stream(t.start, t.end).read(t.sectors * BYTES_PER_FRAME)
        self.data = bytes(data)
        self.bad = set(bad or ())            # never read
        self.flaky = dict(flaky or {})       # sector -> failures left
        self.reads = []

    def truth(self, first, last):
        return self.data[first * BYTES_PER_FRAME:(last + 1) * BYTES_PER_FRAME]

    def read(self, lba, n):
        self.reads.append((lba, n))
        for s in range(lba, lba + n):
            if s in self.bad:
                raise OSError(23, "CRC error")
            if self.flaky.get(s, 0) > 0:
                self.flaky[s] -= 1
                raise OSError(23, "CRC error")
        return self.truth(lba, lba + n - 1)


def _read(reader, first, last, offset=0, retries=5):
    out = bytearray()
    reader.read_range(first, last, offset, out.extend, retries)
    return bytes(out)


def test_offset_correction():
    toc = TOC.from_offsets([0, 300], 600)
    disc = FakeDisc(toc)
    r = windrive.RawReader(disc.read, toc.audio_leadout)
    truth = disc.truth(0, 599)
    assert _read(r, 300, 599) == truth[300 * 2352:]
    # +6 samples: audio from 24 bytes later; past the end of the disc is silence (no overreading)
    got = _read(r, 300, 599, offset=6)
    assert len(got) == 300 * 2352 and got[:-24] == truth[300 * 2352 + 24:] and got[-24:] == b"\0" * 24
    # -30 samples at the very start of the disc: leading silence
    got = _read(r, 0, 299, offset=-30)
    assert got[:120] == b"\0" * 120 and got[120:] == truth[:300 * 2352 - 120]
    assert all(lba >= 0 and lba + n <= 600 for lba, n in disc.reads), "read outside the disc"
    assert r.errors == 0


def test_rereads_and_errors():
    toc = TOC.from_offsets([0], 400)
    disc = FakeDisc(toc, bad={123}, flaky={77: 2})
    r = windrive.RawReader(disc.read, toc.audio_leadout)
    got = _read(r, 0, 399, retries=5)
    truth = disc.truth(0, 399)
    assert r.corrections == 1 and r.errors == 1 and r.suspicious == [123], (r.corrections, r.errors, r.suspicious)
    assert got[77 * 2352:78 * 2352] == truth[77 * 2352:78 * 2352]        # recovered on a re-read
    assert got[123 * 2352:124 * 2352] == b"\0" * 2352                   # unreadable: silence
    assert got[:123 * 2352] == truth[:123 * 2352] and got[124 * 2352:] == truth[124 * 2352:]


def test_cache_flush_and_data_track():
    toc = TOC.from_offsets([0, 2000, 30000], 40000, data_tracks={3})
    disc = FakeDisc(toc)
    r = windrive.RawReader(disc.read, toc.audio_leadout)
    _read(r, 2000, 2999)
    disc.reads.clear()
    _read(r, 2000, 2999)                              # test pass over the same stretch
    first_reads = disc.reads[:5]
    # before re-reading 2000-2999, read at least 1000 sectors away from it (pushes it out of the cache)
    assert all(lba >= 2999 + 1000 or lba + n <= 2000 - 1000 for lba, n in first_reads), first_reads
    assert sum(n for lba, n in disc.reads if not 2000 <= lba <= 2999) >= windrive.FLUSH_SECTORS
    assert all(lba + n <= toc.audio_leadout for lba, n in disc.reads), "read into the data session"


def test_full_rip_with_windows_drive():
    """The real pipeline (rip, AccurateRip, test & copy, encode, log/cue) with WinDrive on a fake disc."""
    from spindle import encoders
    from spindle.config import Config
    from spindle.metadata.base import AlbumMeta, TrackMeta
    from spindle.pipeline import RipJob, Reporter
    toc = TOC.from_offsets([0, 300, 700], 1000)
    disc = FakeDisc(toc, flaky={350: 2})      # fails as part of a block and once on its own

    class FakeDevice:
        def __init__(self, device):
            self.read = disc.read

        def close(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

    tmp = tempfile.mkdtemp()
    cfg = Config(path=os.path.join(tmp, "c.json"))
    cfg["output"].update(directory=os.path.join(tmp, "out"), formats=["flac"] if encoders.backend("flac") else ["wav"])
    cfg["coverart"]["auto_fetch"] = False
    album = AlbumMeta(title="Win", artist="Dows", tracks=[TrackMeta(i, f"T{i}") for i in (1, 2, 3)])
    with mock.patch.object(windrive, "Device", FakeDevice), \
         mock.patch.object(windrive, "read_toc", lambda dev: toc), \
         mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}), \
         mock.patch("spindle.accuraterip.lookup", return_value=[]), \
         mock.patch("spindle.tagger.write", lambda *a, **k: None), mock.patch("spindle.tagger.add", lambda *a, **k: None):
        drive = windrive.WinDrive("D:", "TEST DRIVE")
        assert drive.read_toc() is toc
        job = RipJob(cfg, drive, toc, album, [1, 2, 3], None, Reporter())
        assert job.run(), [r.verdict for r in job.results.values()]
    for n, r in job.results.items():
        assert r.test_crc == r.copy_crc, (n, r)            # test & copy agree
    log = open(job.log_path, encoding="utf-8").read()
    assert "Windows CD-ROM interface" in log and "Copy OK" in log and "Burst with re-reads" in log
    # the correction on track 2 was counted
    assert job.results[2].corrections >= 1
    with mock.patch.object(windrive, "Device", FakeDevice):
        stream = drive.open_stream(0, 9)
        assert stream.read(1) == disc.truth(0, 9)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
