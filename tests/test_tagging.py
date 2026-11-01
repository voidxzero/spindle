"""Round-trip tags + artwork through every output format (needs mutagen and ffmpeg)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from spindle import encoders, tagger
from spindle.metadata.base import AlbumMeta, TrackMeta
from spindle.ripper import SimulatedDrive
from spindle.disc import TOC
import threading


def make_png(w=600, h=600):
    import struct, zlib
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    rows = b"".join(b"\0" + bytes([(x * 7) % 256, (y * 3) % 256, 90]) * 1 for y in range(h) for x in range(w))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b""))


def test_roundtrip():
    from mutagen import File
    tmp = tempfile.mkdtemp()
    wav = os.path.join(tmp, "src.wav")
    SimulatedDrive(TOC.from_offsets([0], 150)).rip(1, 1, wav, 0, "full", 1, lambda f: None, threading.Event())
    album = AlbumMeta(title="Album Ünicode", artist="Artist", date="1999-04-01", genre="Rock", label="Lbl",
                      catalog="CAT-1", mb_release_id="rel-id", mb_disc_id="disc-id", disc_total=2,
                      tracks=[TrackMeta(1, "Title ☂", "Track Artist", composer="Comp", isrc="USRC17607839",
                                        mb_recording_id="rec-id")])
    cover = make_png()
    for key in encoders.FORMATS:
        if encoders.backend(key) is None:
            print("skip", key)
            continue
        dst = os.path.join(tmp, f"t.{key}.{encoders.FORMATS[key].ext}")
        encoders.encode(key, {}, wav, dst)
        tags = tagger.tag_values(album, album.tracks[0], 10)
        tagger.write(dst, tags, cover)
        tagger.add(dst, {"REPLAYGAIN_TRACK_GAIN": "-6.50 dB"})
        f = File(dst)
        dump = repr(f.tags)
        assert "Title ☂" in dump and "Album Ünicode" in dump and "rel-id" in dump, (key, dump[:400])
        assert "-6.50 dB" in dump, (key, "replaygain", dump[:600])
        has_art = (getattr(f, "pictures", None) or "covr" in (f.tags or {}) or "APIC" in dump
                   or "METADATA_BLOCK_PICTURE" in dump or "metadata_block_picture" in dump
                   or "Cover Art (Front)" in dump)
        assert has_art, (key, "no art")
        print("ok", key, os.path.getsize(dst), "bytes")
    # ID3v2.3 variant
    dst = os.path.join(tmp, "v23.mp3")
    encoders.encode("mp3", {}, wav, dst)
    tagger.write(dst, tagger.tag_values(album, album.tracks[0], 10), cover, id3v23=True)
    from mutagen.id3 import ID3
    t = ID3(dst, translate=False)
    assert t.version[1] == 3 and str(t["TYER"]) == "1999", t.version
    print("ok mp3 id3v2.3")


if __name__ == "__main__":
    test_roundtrip()
