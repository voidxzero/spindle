"""Convert music you already have to another format, keeping tags and cover art. Toolkit-independent;
the Convert tab drives it.

Each file is decoded to a temporary WAV on local disk (ffmpeg, or flac for FLAC sources), encoded with the
same encoders and settings as ripping, then tagged from the original."""
from __future__ import annotations

import concurrent.futures as cf
import os
import shutil
import subprocess
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from . import encoders, tagger
from .tagfiles import AUDIO_EXT

# source extension -> format key (".m4a" is resolved by codec: ALAC or AAC)
_EXT_FORMAT = {".flac": "flac", ".wv": "wavpack", ".wav": "wav", ".mp3": "mp3", ".opus": "opus",
               ".ogg": "vorbis", ".oga": "vorbis", ".m4a": "aac", ".mp4": "aac", ".m4b": "aac"}
# tags read under one name and written under another (tagger.read gives lower-case "easy" names)
_TAG_ALIASES = {"organization": "LABEL", "totaltracks": "TRACKTOTAL", "totaldiscs": "DISCTOTAL",
                "year": "DATE"}
_TAG_SKIP = {"length", "metadata_block_picture", "coverart", "coverartmime", "encoder", "encoded_by",
             "encoder_options", "encoding"}


class ConvertError(Exception):
    pass


@dataclass
class Item:
    src: str
    base: str = ""            # the folder it was added from ("" = added as a single file)
    fmt: str | None = None    # source format key
    length: float = 0.0
    bits: int = 16
    dst: str = ""
    state: str = ""           # "", "working", "good", "bad", "skip"
    message: str = ""


def list_audio(paths: list[str]) -> list[tuple[str, str]]:
    """(file, base folder) for every audio file under the given files/folders, in a stable order."""
    out = []
    for p in paths:
        p = os.path.abspath(p)
        if os.path.isdir(p):
            for root, dirs, names in os.walk(p):
                dirs.sort()
                for n in sorted(names):
                    if os.path.splitext(n)[1].lower() in AUDIO_EXT and not n.startswith("."):
                        out.append((os.path.join(root, n), p))
        elif os.path.splitext(p)[1].lower() in AUDIO_EXT:
            out.append((p, ""))
    return out


def probe(path: str) -> tuple[str | None, float, int]:
    """(format key, length in seconds, bits per sample) of an audio file."""
    ext = os.path.splitext(path)[1].lower()
    fmt = _EXT_FORMAT.get(ext)
    length, bits = 0.0, 16
    try:
        import mutagen
        f = mutagen.File(path)
        info = getattr(f, "info", None)
        if info is not None:
            length = float(getattr(info, "length", 0) or 0)
            bits = int(getattr(info, "bits_per_sample", 0) or 16)
            if ext in (".m4a", ".mp4", ".m4b"):
                codec = str(getattr(info, "codec", "") or "").lower()
                fmt = "alac" if codec.startswith("alac") else "aac"
    except Exception:  # noqa: BLE001 - unreadable files still get listed; converting reports the error
        pass
    return fmt, length, bits


def label(fmt: str | None) -> str:
    if not fmt:
        return "?"
    return {"aac": "AAC", "alac": "ALAC", "vorbis": "Vorbis", "wavpack": "WavPack"}.get(
        fmt, encoders.FORMATS[fmt].label.split()[0] if fmt in encoders.FORMATS else fmt)


def destination(item: Item, fmt: str, folder: str | None) -> str:
    """Where the converted file goes: next to the original, or in ``folder`` keeping the layout of the
    folder it was added from (Music/Album/01.flac -> folder/Album/01.mp3)."""
    stem = os.path.splitext(os.path.basename(item.src))[0] + "." + encoders.FORMATS[fmt].ext
    if not folder:
        return os.path.join(os.path.dirname(item.src), stem)
    if item.base:
        rel = os.path.relpath(os.path.dirname(item.src), os.path.dirname(item.base))
        return os.path.normpath(os.path.join(folder, rel, stem))
    return os.path.join(folder, stem)


def work_root() -> str:
    """Temporary WAVs live on local disk, as when ripping (the music may be on a network share)."""
    root = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "spindle" / "work"
    try:
        root.mkdir(parents=True, exist_ok=True)
        return str(root)
    except OSError:
        return tempfile.gettempdir()


def _decode(src: str, wav: str, bits: int) -> None:
    ff = shutil.which("ffmpeg")
    if ff:
        codec = {24: "pcm_s24le", 32: "pcm_s32le"}.get(bits, "pcm_s16le")
        p = subprocess.run([ff, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
                            "-map", "0:a:0", "-vn", "-c:a", codec, "-f", "wav", wav],
                           capture_output=True, text=True, errors="replace")
        if p.returncode != 0 or not os.path.exists(wav):
            raise ConvertError("couldn't read the file: " + (p.stderr.strip()[-200:] or f"ffmpeg exit {p.returncode}"))
        return
    if src.lower().endswith(".flac") and shutil.which("flac"):
        p = subprocess.run(["flac", "-s", "-d", "-f", "-o", wav, src], capture_output=True, text=True, errors="replace")
        if p.returncode != 0:
            raise ConvertError("flac couldn't decode the file: " + p.stderr.strip()[-200:])
        return
    raise ConvertError("ffmpeg is needed to read this kind of file - install it with your package manager")


def tags_for_copy(src: str) -> dict[str, str]:
    """The original's tags, under the names tagger.write expects."""
    tags, _ = tagger.read(src)
    out: dict[str, str] = {}
    for k, v in tags.items():
        if not v or k in _TAG_SKIP:
            continue
        key = _TAG_ALIASES.get(k, k.upper())
        out.setdefault(key, v)
    for k in ("TRACKNUMBER", "DISCNUMBER", "TRACKTOTAL", "DISCTOTAL"):   # MP4 needs plain numbers
        if k in out:
            num = out[k].split("/")[0].strip()
            if num.isdigit():
                out[k] = str(int(num))
            else:
                del out[k]
    return out


def convert_file(item: Item, fmt: str, opts: dict, *, copy_tags: bool = True, id3v23: bool = False,
                 overwrite: bool = False) -> str:
    """Convert one file to ``item.dst``. Returns a short result message; raises ConvertError."""
    src, dst = item.src, item.dst
    if os.path.abspath(src) == os.path.abspath(dst):
        raise ConvertError("it would replace the original - choose another folder")
    if os.path.exists(dst) and not overwrite:
        return "skip"
    lossless_target = encoders.FORMATS[fmt].lossless
    bits = item.bits if lossless_target and item.bits in (24, 32) else 16
    work = tempfile.mkdtemp(prefix=".spindle-convert-", dir=work_root())
    try:
        if src.lower().endswith(".wav") and item.bits == bits:
            wav = src                      # already PCM at the depth we want
        else:
            wav = os.path.join(work, "in.wav")
            _decode(src, wav, bits)
        try:
            encoders.encode(fmt, opts, wav, dst)
        except encoders.EncodeError as e:
            raise ConvertError(str(e)) from e
        if copy_tags:
            try:
                tags = tags_for_copy(src)
                cover = tagger.read_cover(src)
                if tags or cover:
                    tagger.write(dst, tags, cover, id3v23=id3v23)
            except Exception as e:  # noqa: BLE001 - the audio is fine; say what went wrong
                return f"converted, but the tags couldn't be copied: {e}"
        return "ok"
    finally:
        shutil.rmtree(work, ignore_errors=True)


class ConvertJob:
    """Convert a list of items in parallel. ``on_status(index, state, text)`` is called from worker
    threads; state is "working", "good", "bad" or "skip"."""

    def __init__(self, items: list[Item], fmt: str, opts: dict, *, folder: str | None, copy_tags: bool = True,
                 overwrite: bool = False, id3v23: bool = False, workers: int = 2, on_status=None):
        self.items, self.fmt, self.opts = items, fmt, opts
        self.folder, self.copy_tags, self.overwrite, self.id3v23 = folder, copy_tags, overwrite, id3v23
        self.workers = max(1, int(workers))
        self.on_status = on_status or (lambda i, s, t: None)
        self.cancel = threading.Event()
        for it in items:
            it.dst = destination(it, fmt, folder)

    def _one(self, i: int) -> None:
        it = self.items[i]
        if self.cancel.is_set():
            return
        if os.path.abspath(it.src) == os.path.abspath(it.dst):        # e.g. FLAC -> FLAC next to itself
            it.state, it.message = "skip", f"already {label(it.fmt)}"
            self.on_status(i, "skip", f"Skipped - already {label(it.fmt)}")
            return
        it.state = "working"
        self.on_status(i, "working", "Converting…")
        try:
            msg = convert_file(it, self.fmt, self.opts, copy_tags=self.copy_tags, id3v23=self.id3v23,
                               overwrite=self.overwrite)
        except Exception as e:  # noqa: BLE001
            it.state, it.message = "bad", str(e)
            self.on_status(i, "bad", f"Failed: {e}")
            return
        if msg == "skip":
            it.state, it.message = "skip", "already exists"
            self.on_status(i, "skip", "Skipped - already exists")
        elif msg == "ok":
            it.state, it.message = "good", ""
            self.on_status(i, "good", "Done")
        else:
            it.state, it.message = "good", msg
            self.on_status(i, "warn", "Done - " + msg)

    def run(self) -> dict[str, int]:
        """Blocks until every file is done (or the job is cancelled). Returns counts per state."""
        with cf.ThreadPoolExecutor(max_workers=self.workers) as pool:
            futs = [pool.submit(self._one, i) for i in range(len(self.items))]
            for f in futs:
                f.result()
        counts = {"good": 0, "bad": 0, "skip": 0, "cancelled": 0}
        for it in self.items:
            counts[it.state if it.state in counts else "cancelled"] += 1
        return counts
