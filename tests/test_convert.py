"""Convert tab engine: format round-trips, tags + cover carried over, 24-bit kept, folder layout, safety.
Needs mutagen and the encoders (or ffmpeg); formats that can't be produced here are skipped."""
import os, sys, tempfile, wave
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from unittest import mock

import numpy as np

from spindle import convert, encoders, tagger


def _wav(path, bits=16, seconds=1.0):
    n = int(44100 * seconds)
    t = np.arange(n) / 44100.0
    sig = (np.sin(2 * np.pi * 440 * t) * 0.3)
    stereo = np.repeat(sig[:, None], 2, axis=1)
    if bits == 24:
        ints = (stereo * (2 ** 23 - 1)).astype("<i4")
        raw = b"".join(int(v).to_bytes(4, "little", signed=True)[:3] for v in ints.ravel())
    else:
        raw = (stereo * 32767).astype("<i2").tobytes()
    with wave.open(path, "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(bits // 8)
        w.setframerate(44100)
        w.writeframes(raw)


def _jpeg():
    # smallest useful JPEG: tagger only cares that it's image data with a known type
    return bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d01020300041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbd3ffd9")


TAGS = {"TITLE": "Song ☂", "ARTIST": "Artist", "ALBUM": "Album", "ALBUMARTIST": "Album Artist",
        "TRACKNUMBER": "3", "TRACKTOTAL": "12", "DISCNUMBER": "1", "DATE": "1999",
        "GENRE": "Rock", "MUSICBRAINZ_TRACKID": "rec-id", "REPLAYGAIN_TRACK_GAIN": "-6.50 dB"}


def _source(tmp, fmt="flac", bits=16, name="03 - Song"):
    wav = os.path.join(tmp, "raw.wav")
    _wav(wav, bits)
    dst = os.path.join(tmp, "Album", name + "." + encoders.FORMATS[fmt].ext)
    encoders.encode(fmt, {}, wav, dst)
    tagger.write(dst, TAGS, _jpeg())
    os.remove(wav)
    return dst


def _item(path, base=""):
    fmt, length, bits = convert.probe(path)
    return convert.Item(path, base, fmt, length, bits)


def test_roundtrip_every_format():
    tmp = tempfile.mkdtemp()
    src = _source(tmp)
    with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
        for fmt in encoders.FORMATS:
            if not encoders.backend(fmt) or fmt == "flac":
                continue
            it = _item(src)
            it.dst = os.path.join(tmp, "out", fmt, "x." + encoders.FORMATS[fmt].ext)
            assert convert.convert_file(it, fmt, {}) == "ok", fmt
            tags, length = tagger.read(it.dst)
            assert abs(length - 1.0) < 0.1, (fmt, length)
            if fmt == "wav":
                continue
            assert tags.get("title") == "Song ☂" and tags.get("artist") == "Artist", (fmt, tags)
            assert tags.get("tracknumber") == "3" and tags.get("album") == "Album", (fmt, tags)
            assert tagger.read_cover(it.dst), f"{fmt}: cover missing"
            print("ok", fmt)
    # the converted MP3 can be converted again (lossy -> lossless works too)
    mp3 = os.path.join(tmp, "out", "mp3", "x.mp3")
    if os.path.exists(mp3):
        it = _item(mp3)
        assert it.fmt == "mp3"
        it.dst = os.path.join(tmp, "back.flac")
        with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            assert convert.convert_file(it, "flac", {}) == "ok"
        assert tagger.read(it.dst)[0].get("title") == "Song ☂"


def test_24bit_kept_for_lossless():
    from mutagen import File
    tmp = tempfile.mkdtemp()
    src = _source(tmp, bits=24)
    it = _item(src)
    assert it.bits == 24
    with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
        it.dst = os.path.join(tmp, "o.wv")
        assert convert.convert_file(it, "wavpack", {}) == "ok"
        assert File(it.dst).info.bits_per_sample == 24
        it.dst = os.path.join(tmp, "o.flac")
        assert convert.convert_file(it, "flac", {}) == "ok"
        assert File(it.dst).info.bits_per_sample == 24


def test_destination_and_safety():
    tmp = tempfile.mkdtemp()
    music = os.path.join(tmp, "Music")
    it = convert.Item(os.path.join(music, "Album", "CD1", "01.flac"), base=os.path.join(music, "Album"))
    assert convert.destination(it, "mp3", None) == os.path.join(music, "Album", "CD1", "01.mp3")
    out = os.path.join(tmp, "out")
    assert convert.destination(it, "mp3", out) == os.path.join(out, "Album", "CD1", "01.mp3")
    single = convert.Item(os.path.join(music, "x.flac"))
    assert convert.destination(single, "opus", out) == os.path.join(out, "x.opus")
    # ALAC -> AAC next to the original would be the same .m4a: refuse rather than overwrite it
    src = os.path.join(tmp, "a.m4a")
    open(src, "wb").write(b"original")
    same = convert.Item(src, fmt="alac")
    same.dst = convert.destination(same, "aac", None)
    try:
        convert.convert_file(same, "aac", {})
        raise AssertionError("should refuse")
    except convert.ConvertError:
        pass
    assert open(src, "rb").read() == b"original"


def test_job_skips_existing_and_reports():
    tmp = tempfile.mkdtemp()
    a = _source(tmp, name="01 - A")
    b = _source(tmp, name="02 - B")
    broken = os.path.join(tmp, "Album", "03 - Broken.flac")
    open(broken, "wb").write(b"not audio")
    items = [_item(p, os.path.join(tmp, "Album")) for p, _ in convert.list_audio([os.path.join(tmp, "Album")])]
    assert [os.path.basename(i.src) for i in items] == ["01 - A.flac", "02 - B.flac", "03 - Broken.flac"]
    out = os.path.join(tmp, "out")
    seen = []
    with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
        job = convert.ConvertJob(items, "mp3", {}, folder=out, on_status=lambda i, s, t: seen.append((i, s)))
        counts = job.run()
        assert counts == {"good": 2, "bad": 1, "skip": 0, "cancelled": 0}, counts
        assert os.path.exists(os.path.join(out, "Album", "01 - A.mp3"))
        assert (2, "bad") in seen
        for it in items:
            it.state = ""
        again = convert.ConvertJob(items[:2], "mp3", {}, folder=out).run()       # already there: skipped
        assert again["skip"] == 2, again
        replaced = convert.ConvertJob(items[:2], "mp3", {}, folder=out, overwrite=True).run()
        assert replaced["good"] == 2, replaced
    assert os.path.exists(a) and os.path.exists(b)                                 # originals untouched
    # FLAC -> FLAC next to the originals: nothing to do, reported as skipped (not failed)
    with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
        for it in items:
            it.state = ""
        same = convert.ConvertJob(items[:2], "flac", {}, folder=None).run()
    assert same["skip"] == 2 and same["bad"] == 0, same


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok", name)
