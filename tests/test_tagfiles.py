"""File tagger tests with a fake MusicBrainz (needs mutagen + ffmpeg)."""
import os, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest import mock

from spindle import encoders, tagger, tagfiles
from spindle.config import Config
from spindle.disc import TOC
from spindle.ripper import SimulatedDrive

LENGTHS = [4.0, 5.3333, 4.0]   # sectors 300/400/300 -> seconds


def fake_release(rid="rel-1", titles=("Alpha", "Beta", "Gamma"), lengths=LENGTHS, title="Real Album"):
    return {"id": rid, "title": title, "date": "2001-02-03", "country": "US",
            "artist-credit": [{"name": "Real Artist", "artist": {"id": "art-1"}}],
            "release-group": {"id": "rg-1", "first-release-date": "2001"},
            "media": [{"position": 1, "format": "CD", "track-count": len(titles),
                       "tracks": [{"id": f"t{i}", "position": i + 1, "title": t, "length": int(l * 1000),
                                   "recording": {"id": f"r{i}"}} for i, (t, l) in enumerate(zip(titles, lengths))]}]}


def make_files():
    tmp = tempfile.mkdtemp()
    d = os.path.join(tmp, "Real Artist - Real Album (2001)")
    os.makedirs(d)
    drive = SimulatedDrive(TOC.from_offsets([0, 300, 700], 1000))
    paths = []
    for n, name in ((1, "01 - alpha.flac"), (2, "02 - beta.mp3"), (3, "03 gamma.ogg")):
        wav = os.path.join(tempfile.mkdtemp(), f"{n}.wav")
        drive.rip(n, n, wav, 0, "full", 1, lambda f: None, threading.Event())
        key = {"flac": "flac", "mp3": "mp3", "ogg": "vorbis"}[name.rsplit(".", 1)[1]]
        dst = os.path.join(d, name)
        encoders.encode(key, {}, wav, dst)
        paths.append(dst)
    # one file has partial old tags + ReplayGain that must survive
    tagger.write(paths[0], {"TITLE": "alpha (old)", "ALBUM": "Real Albm", "REPLAYGAIN_TRACK_GAIN": "-3.00 dB"})
    return tmp, d, paths


def test_identify_and_save():
    tmp, d, paths = make_files()
    cfg = Config(path=os.path.join(tmp, "c.json"))
    cfg["output"]["directory"] = os.path.join(tmp, "Library")
    files = tagfiles.scan([tmp])
    albums = tagfiles.group(files)
    assert len(albums) == 1 and len(albums[0].files) == 3, albums
    fa = albums[0]
    assert fa.guess_album == "Real Albm"
    wrong = fake_release("rel-2", ("X", "Y", "Z", "W"), [100, 200, 300, 400], "Other")
    with mock.patch("spindle.metadata.musicbrainz.search_releases", return_value=[{"id": "rel-2"}, {"id": "rel-1"}]), \
         mock.patch("spindle.metadata.musicbrainz.fetch_release",
                    side_effect=lambda mbid, full=True: fake_release() if mbid == "rel-1" else wrong):
        tagfiles.identify(cfg, fa)
    assert fa.status == "matched" and fa.edited.mb_release_id == "rel-1", (fa.status, fa.message, [c.label() for c in fa.candidates])
    assert [fa.assignment[i] for i in range(3)] == [1, 2, 3]
    print("best:", fa.candidates[0].label(), "| runner-up:", fa.candidates[1].label())
    fa.edited.genre = "Indie"                          # a user edit
    target = tagfiles.target_path(cfg, fa, 1, organize=True)
    assert target.endswith(os.path.join("Library", "Real Artist", "Real Album (2001)", "02 - Beta.mp3")), target
    tagfiles.save(cfg, fa, rename=True, organize=True)
    lib = os.path.join(tmp, "Library", "Real Artist", "Real Album (2001)")
    assert sorted(os.listdir(lib)) == ["01 - Alpha.flac", "02 - Beta.mp3", "03 - Gamma.ogg"], os.listdir(lib)
    for name, title in (("01 - Alpha.flac", "Alpha"), ("02 - Beta.mp3", "Beta"), ("03 - Gamma.ogg", "Gamma")):
        t, _ = tagger.read(os.path.join(lib, name))
        assert t["title"] == title and t["album"] == "Real Album" and t["genre"] == "Indie", (name, t)
        assert t.get("musicbrainz_albumid") == "rel-1", (name, t)
    from mutagen.flac import FLAC
    assert FLAC(os.path.join(lib, "01 - Alpha.flac"))["REPLAYGAIN_TRACK_GAIN"] == ["-3.00 dB"]
    print("ok tag + rename + replaygain preserved")


def test_preserve_cover():
    tmp, d, paths = make_files()
    png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (10).to_bytes(4, "big") * 2 + b"\x08\x02\0\0\0" + b"\0" * 20
    for p in paths:
        tagger.write(p, {"TITLE": "x"}, png)
        tagger.write(p, {"TITLE": "y"}, None, preserve=True)      # no cover given -> keep existing
        assert tagger.read_cover(p) == png, p
        assert tagger.read(p)[0]["title"] == "y"
    print("ok cover kept")




def test_batch_rename():
    tmp, d, paths = make_files()
    files = tagfiles.scan([d])
    # give them the "broken" names from the user's screenshot
    for i, f in enumerate(files):
        tagger.write(f.path, {"TITLE": ["Alpha", "Beta", "Gamma"][i], "ARTIST": "R.E.M.", "TRACKNUMBER": str(i + 1)})
        bad = os.path.join(d, f"{i + 1:02d} - {['Alpha', 'Beta', 'Gamma'][i]} - %artists%{' (2)' if i == 0 else ''}{os.path.splitext(f.path)[1]}")
        os.rename(f.path, bad)
    files = tagfiles.scan([d])
    plans = tagfiles.plan_renames(files, "pattern", "%tracknumber% - %title%")
    assert [os.path.basename(p.new_path) for p in plans] == ["01 - Alpha.flac", "02 - Beta.mp3", "03 - Gamma.ogg"], [p.new_path for p in plans]
    plans2 = tagfiles.plan_renames(files, "replace", find=" - %artists%", replace="")
    assert os.path.basename(plans2[0].new_path) == "01 - Alpha (2).flac"
    plans3 = tagfiles.plan_renames(files, "replace", find=r"^(\d+) - (.+?) - .*$", replace=r"\2 (\1)", regex=True)
    assert os.path.basename(plans3[1].new_path) == "Beta (02).mp3", plans3[1].new_path
    clash = tagfiles.plan_renames(files, "pattern", "%artist%")
    assert all(p.problem == "" for p in clash)   # different extensions -> no clash
    same = tagfiles.plan_renames([files[0], files[0]], "pattern", "%artist%")
    assert same[0].problem == "Two files would get this name"
    assert tagfiles.apply_renames(plans) == 3
    assert sorted(os.listdir(d)) == ["01 - Alpha.flac", "02 - Beta.mp3", "03 - Gamma.ogg"], os.listdir(d)
    # swap two names safely
    a, b = files[0], files[1]
    swap = [tagfiles.RenamePlan(a, os.path.join(d, "x.flac")), tagfiles.RenamePlan(b, os.path.join(d, "y.mp3"))]
    tagfiles.apply_renames(swap)
    print("ok batch rename")


def test_cover_only():
    tmp, d, paths = make_files()
    cfg = Config(path=os.path.join(tmp, "c.json"))
    cfg["coverart"]["embed_max_px"] = 0
    for i, p in enumerate(paths):
        tagger.write(p, {"TITLE": f"Keep {i}", "ARTIST": "Keep Artist", "REPLAYGAIN_TRACK_GAIN": "-2.00 dB"})
    png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + (600).to_bytes(4, "big") * 2 + b"\x08\x02\0\0\0" + b"\0" * 40
    fa = tagfiles.group(tagfiles.scan([d]))[0]
    n = tagfiles.embed_cover_only(cfg, fa, png)
    assert n == 3
    for i, p in enumerate(sorted(f.path for f in fa.files)):
        t, _ = tagger.read(p)
        assert t["title"].startswith("Keep") and t["artist"] == "Keep Artist", t
        assert tagger.read_cover(p) == png, p
    assert os.path.exists(os.path.join(d, "cover.png"))
    assert tagfiles.folder_image(fa) == png
    from mutagen.flac import FLAC
    assert FLAC([p for p in paths if p.endswith(".flac")][0])["REPLAYGAIN_TRACK_GAIN"] == ["-2.00 dB"]
    print("ok cover only (tags untouched)")


if __name__ == "__main__":
    test_identify_and_save()
    test_preserve_cover()
    test_batch_rename()
    test_cover_only()
