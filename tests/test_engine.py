"""Engine tests that run without a disc, network, Qt or mutagen."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest import mock

from spindle import accuraterip, encoders, naming
from spindle.config import Config
from spindle.disc import TOC
from spindle.metadata.base import AlbumMeta, TrackMeta
from spindle.metadata import gnudb, musicbrainz
from spindle.coverart import image_size, match_score
from spindle.ripper import SimulatedDrive
from spindle.pipeline import RipJob, Reporter


def test_ids():
    t = TOC.from_musicbrainz_toc("1 6 95462 150 15363 32314 46592 63414 80489")
    assert t.musicbrainz_id() == "49HHV7Eb8UKF3aQiNmu1GR8vKTY-"


def test_enhanced_cd():
    # 3 audio + data track: audio lead-out must be data start - 11400
    t = TOC.from_offsets([0, 10000, 20000, 50000], 70000, data_tracks={4})
    assert t.audio_leadout == 50000 - 11400
    assert len(t.audio_tracks) == 3 and t.track(3).end == 50000 - 11400 - 1
    assert t.musicbrainz_toc().split()[:3] == ["1", "3", str(50000 - 11400 + 150)]


def test_ar_parse_verify():
    import struct
    blob = struct.pack("<BIII", 2, 1, 2, 3) + struct.pack("<BII", 7, 0xAAAA, 0) + struct.pack("<BII", 5, 0xBBBB, 0)
    blob += struct.pack("<BIII", 2, 1, 2, 3) + struct.pack("<BII", 3, 0xCCCC, 0) + struct.pack("<BII", 2, 0xBBBB, 0)
    r = accuraterip.parse_response(blob)
    assert len(r) == 2
    v = accuraterip.verify(1, 0x1111, 0xBBBB, r)
    assert v.status == "accurate" and v.confidence == 5 and v.version == 2
    assert accuraterip.verify(0, 1, 2, r).status == "mismatch"
    assert accuraterip.verify(0, 1, 2, []).status == "absent"


def test_offset_table():
    page = "<table><tr><td>PIONEER  - DVD-RW  DVR-111D</td><td>+48</td><td>Yes</td></tr>" \
           "<tr><td>HL-DT-ST - DVDRAM GH24NSD1</td><td>+6</td></tr></table>"
    tbl = accuraterip.parse_offset_table(page)
    from spindle.disc import DriveInfo
    assert accuraterip.find_offset(DriveInfo("/dev/sr0", "HL-DT-ST", "DVDRAM GH24NSD1"), tbl) == 6
    assert accuraterip.find_offset(DriveInfo("/dev/sr0", "PIONEER", "DVD-RW DVR-111D"), tbl) == 48


def test_naming():
    a = AlbumMeta(title="OK Computer", artist="Radiohead", date="1997-05-21")
    tr = TrackMeta(2, "Paranoid Android", "Radiohead")
    cfg = Config(path="/nonexistent")
    v = naming.values(a, tr, 12)
    assert naming.render(cfg["output"]["template"], v) == "Radiohead/OK Computer (1997)/02 - Paranoid Android"
    a.disc_total, a.disc_number = 2, 1
    assert naming.render(cfg["output"]["template"], naming.values(a, tr, 12)).endswith("/1-02 - Paranoid Android")
    assert naming.render("%artist%/%title%", {"artist": "AC/DC", "title": 'What? "Yes"'}) == "AC-DC/What 'Yes'"


def test_musicbrainz_parse():
    toc = TOC.from_offsets([0, 10000], 20000)
    rel = {"id": "rel", "title": "Album", "date": "2001", "country": "GB",
           "artist-credit": [{"name": "A", "joinphrase": " & ", "artist": {"id": "a1"}}, {"name": "B", "artist": {"id": "b"}}],
           "label-info": [{"catalog-number": "CAT1", "label": {"name": "Lbl"}}],
           "release-group": {"id": "rg", "first-release-date": "2000", "genres": [{"name": "rock", "count": 3}]},
           "media": [{"position": 1, "format": "CD", "track-count": 3, "tracks": []},
                     {"position": 2, "format": "CD", "track-count": 2, "discs": [{"id": "DISC"}],
                      "tracks": [{"id": "t1", "title": "One", "recording": {"id": "r1", "isrcs": ["X"],
                                   "relations": [{"type": "performance", "work": {"relations": [
                                       {"type": "composer", "artist": {"name": "Comp"}}]}}]}},
                                 {"id": "t2", "title": "Two", "recording": {"id": "r2"}}]}]}
    a = musicbrainz.parse_release(rel, toc, "DISC")
    assert a.artist == "A & B" and a.disc_number == 2 and a.disc_total == 2 and a.genre == "Rock"
    assert a.label == "Lbl" and a.catalog == "CAT1" and a.original_date == "2000"
    assert a.tracks[0].composer == "Comp" and a.tracks[0].isrc == "X" and a.tracks[1].artist == "A & B"


def test_gnudb_parse():
    toc = TOC.from_offsets([0, 10000], 20000)
    a = gnudb.parse_xmcd("DTITLE=Various / Hits\nDYEAR=1999\nTTITLE0=Foo / Bar\nTTITLE1=Baz / Qu\nTTITLE1=ux\n", toc)
    assert a.compilation and a.tracks[0].artist == "Foo" and a.tracks[1].title == "Quux"


def test_image_size():
    png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (1200).to_bytes(4, "big") + (800).to_bytes(4, "big")
    assert image_size(png) == (1200, 800)
    jpg = b"\xff\xd8\xff\xe0\x00\x04ab\xff\xc0\x00\x11\x08" + (600).to_bytes(2, "big") + (500).to_bytes(2, "big") + b"\0" * 20
    assert image_size(jpg) == (500, 600)
    assert match_score(AlbumMeta(title="Abbey Road", artist="The Beatles"), "The Beatles", "Abbey Road (Remastered)") > 0.9


def test_full_pipeline_simulated(tmp_path=None):
    tmp = tempfile.mkdtemp()
    cfg = Config(path=os.path.join(tmp, "c.json"))
    cfg["output"]["directory"] = tmp
    cfg["output"]["formats"] = [f for f in ("flac", "mp3", "opus") if encoders.backend(f)]
    cfg["coverart"]["auto_fetch"] = False
    drive = SimulatedDrive(TOC.from_offsets([0, 300, 700], 1000), flaky_track=2)
    toc = drive.read_toc()
    album = AlbumMeta(title="Test Album", artist="Test Artist", date="2024",
                      tracks=[TrackMeta(i, f"Song {i}", "Test Artist") for i in (1, 2, 3)])
    seen = []
    with mock.patch("spindle.accuraterip.lookup", return_value=[]), \
         mock.patch("spindle.tagger.write", lambda *a, **k: seen.append(a[0])), \
         mock.patch("spindle.tagger.add", lambda *a, **k: None):
        job = RipJob(cfg, drive, toc, album, [1, 2, 3], None, Reporter())
        ok = job.run()
    assert ok, [r.verdict for r in job.results.values()]
    for n, r in job.results.items():
        assert r.test_crc == r.copy_crc and len(r.files) == len(cfg["output"]["formats"]), r
        for f in r.files:
            assert os.path.getsize(f) > 1000
    assert os.path.exists(job.log_path)
    log = open(job.log_path).read()
    assert "Copy OK" in log and "Test CRC" in log
    assert any(f.endswith(".cue") for f in os.listdir(job.output_dir))
    print(job.output_dir, sorted(os.listdir(job.output_dir)))
    return job


def test_image_mode():
    tmp = tempfile.mkdtemp()
    cfg = Config(path=os.path.join(tmp, "c.json"))
    cfg["output"].update(directory=tmp, formats=["flac"], mode="image")
    cfg["coverart"]["auto_fetch"] = False
    drive = SimulatedDrive(TOC.from_offsets([0, 300, 700], 1000))
    album = AlbumMeta(title="Img", artist="Art", tracks=[TrackMeta(i, f"S{i}") for i in (1, 2, 3)])
    with mock.patch("spindle.accuraterip.lookup", return_value=[]), \
         mock.patch("spindle.tagger.write", lambda *a, **k: None), mock.patch("spindle.tagger.add", lambda *a, **k: None):
        job = RipJob(cfg, drive, drive.read_toc(), album, [1, 2, 3], None, Reporter())
        assert job.run()
    cue = [f for f in os.listdir(job.output_dir) if f.endswith(".cue")][0]
    text = open(os.path.join(job.output_dir, cue)).read()
    assert "INDEX 01 00:04:00" in text and "INDEX 01 00:09:25" in text, text




def test_keydisc_offset():
    """A simulated drive that reads N samples early/late; the database holds the true frame-450 CRCs."""
    import numpy as np
    from spindle.ripper import SimulatedDrive
    toc = TOC.from_offsets([0, 2000, 4500, 7000], 9000)
    truth = SimulatedDrive(toc)                      # true_offset 0 -> perfect audio
    entries = []
    for t in toc.audio_tracks:
        s = truth.open_stream(t.start + 450, t.start + 450)
        frame = np.frombuffer(s.read(2352), "<u4").astype(np.uint64)
        crc = int((frame * np.arange(1, 589, dtype=np.uint64)).sum() & np.uint64(0xFFFFFFFF))
        entries.append((5, 0x1234, crc))
    db = [accuraterip.ARResponse(4, 0, 0, 0, entries)]
    for true in (6, -1164, 667, 0):
        drive = SimulatedDrive(toc)
        drive.true_offset = true
        res = accuraterip.detect_offset_keydisc(drive, toc, db, max_offset=3000)
        assert res and res[0].offset == true and res[0].tracks_matched == 4 == res[0].tracks_tested, (true, res[:3])
    # a second pressing shifted by 30 samples shows up as a second, weaker candidate
    shifted = SimulatedDrive(toc); shifted.true_offset = -30
    e2 = []
    for t in toc.audio_tracks[:2]:
        s = shifted.open_stream(t.start + 450, t.start + 450)
        fr = np.frombuffer(s.read(2352), "<u4").astype(np.uint64)
        e2.append((2, 0, int((fr * np.arange(1, 589, dtype=np.uint64)).sum() & np.uint64(0xFFFFFFFF))))
    db2 = db + [accuraterip.ARResponse(2, 0, 0, 0, e2 + [(0, 0, 0), (0, 0, 0)])]
    drive = SimulatedDrive(toc); drive.true_offset = 6
    res = accuraterip.detect_offset_keydisc(drive, toc, db2)
    assert res[0].offset == 6 and res[0].tracks_matched == 4 and res[1].offset == 36, res[:3]


def test_default_theme_migration():
    """v3: the default skin per platform; installs still on System switch once, chosen skins are kept."""
    import json
    from spindle.config import DEFAULT_THEME
    tmp = tempfile.mkdtemp()
    assert DEFAULT_THEME in ("Amp Classic", "Modern Night")
    assert Config(path=os.path.join(tmp, "fresh.json"))["ui"]["theme"] == DEFAULT_THEME
    for old, want in (("System", DEFAULT_THEME), ("Luna Midnight", "Luna Midnight")):
        p = os.path.join(tmp, f"{old}.json")
        json.dump({"config_version": 2, "ui": {"theme": old}}, open(p, "w"))
        c = Config(path=p)
        assert c["ui"]["theme"] == want and c["config_version"] == 3, (old, c["ui"]["theme"])
        c["ui"]["theme"] = "System"           # choosing System again afterwards sticks
        c.save()
        assert Config(path=p)["ui"]["theme"] == "System"


def _flaky_rip_setup():
    tmp = tempfile.mkdtemp()
    cache = tempfile.mkdtemp()
    cfg = Config(path=os.path.join(tmp, "c.json"))
    cfg["output"].update(directory=os.path.join(tmp, "out"), formats=["flac"])
    cfg["coverart"]["auto_fetch"] = False
    cfg["drive"]["retry_on_mismatch"] = 0          # copy + one test read: the flaky track fails
    drive = SimulatedDrive(TOC.from_offsets([0, 300, 700], 1000), flaky_track=2)
    album = AlbumMeta(title="Retry Album", artist="Retry Artist",
                      tracks=[TrackMeta(i, f"Song {i}", "Retry Artist") for i in (1, 2, 3)])
    return tmp, cache, cfg, drive, album


def test_work_dir_is_local():
    """Temporary WAVs go to ~/.cache/spindle/work, never into the (possibly network) output folder."""
    import copy
    tmp, cache, cfg, drive, album = _flaky_rip_setup()
    seen = []
    real = tempfile.mkdtemp
    with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}), \
         mock.patch("spindle.accuraterip.lookup", return_value=[]), \
         mock.patch("spindle.tagger.write", lambda *a, **k: None), mock.patch("spindle.tagger.add", lambda *a, **k: None), \
         mock.patch("spindle.pipeline.tempfile.mkdtemp", lambda **k: seen.append(k["dir"]) or real(**k)):
        RipJob(copy.deepcopy(cfg), drive, drive.read_toc(), album, [1], None, Reporter()).run()
    assert seen == [os.path.join(cache, "spindle", "work")], seen
    assert os.listdir(seen[0]) == [], os.listdir(seen[0])          # cleaned up afterwards
    assert not [f for f in os.listdir(cfg["output"]["directory"]) if f.startswith(".spindle-")]


def test_retry_failed_track():
    """Retrying a failed track replaces its file and rebuilds the album's log, cue and playlist."""
    tmp, cache, cfg, drive, album = _flaky_rip_setup()
    import copy
    with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": cache}), \
         mock.patch("spindle.accuraterip.lookup", return_value=[]), \
         mock.patch("spindle.tagger.write", lambda *a, **k: None), mock.patch("spindle.tagger.add", lambda *a, **k: None):
        job = RipJob(copy.deepcopy(cfg), drive, drive.read_toc(), album, [1, 2, 3], None, Reporter())
        assert not job.run()
        assert [n for n in job.track_order if not job.results[n].good] == [2]
        old_file = job.results[2].files[0]
        drive.flaky_track = 0                           # the disc reads cleanly this time
        retry = RipJob(copy.deepcopy(cfg), drive, drive.read_toc(), album, [2], None, Reporter(), retry_of=job)
        assert retry.run()
    assert retry.track_order == [1, 2, 3] and all(retry.results[n].good for n in (1, 2, 3))
    assert retry.results[2].files == [old_file]         # replaced in place, no "(2)" copy
    out = retry.output_dir
    names = sorted(os.listdir(out))
    assert not [n for n in names if "(2)" in n], names
    assert len([n for n in names if n.endswith(".flac")]) == 3, names
    log = open(retry.log_path).read()
    assert "No errors occurred" in log and log.count("Track ") >= 3, log
    m3u = open(os.path.join(out, [n for n in names if n.endswith(".m3u8")][0])).read()
    assert m3u.count(".flac") == 3, m3u


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
