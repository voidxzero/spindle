"""Encoders. Uses the reference command-line encoder when installed, ffmpeg otherwise."""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass

from . import system


@dataclass(frozen=True)
class Format:
    key: str
    label: str
    ext: str
    lossless: bool
    native: str          # preferred CLI encoder
    ffmpeg_codec: str


FORMATS: dict[str, Format] = {f.key: f for f in [
    Format("flac", "FLAC", "flac", True, "flac", "flac"),
    Format("alac", "Apple Lossless (ALAC)", "m4a", True, "", "alac"),
    Format("wavpack", "WavPack", "wv", True, "wavpack", "wavpack"),
    Format("wav", "WAV (uncompressed)", "wav", True, "", ""),
    Format("mp3", "MP3 (LAME)", "mp3", False, "lame", "libmp3lame"),
    Format("opus", "Opus", "opus", False, "opusenc", "libopus"),
    Format("vorbis", "Ogg Vorbis", "ogg", False, "oggenc", "libvorbis"),
    Format("aac", "AAC (M4A)", "m4a", False, "fdkaac", "aac"),
]}


class EncodeError(Exception):
    pass


def _ffmpeg_has(codec: str) -> bool:
    ff = shutil.which("ffmpeg")
    if not ff or not codec:
        return False
    if not hasattr(_ffmpeg_has, "cache"):
        try:
            out = system.run([ff, "-hide_banner", "-encoders"], capture_output=True, text=True,
                                 timeout=10).stdout
        except (OSError, subprocess.TimeoutExpired):
            out = ""
        _ffmpeg_has.cache = out
    return f" {codec} " in _ffmpeg_has.cache


def backend(key: str) -> str | None:
    """'native', 'ffmpeg', 'copy' or None if the format can't be produced on this system."""
    f = FORMATS[key]
    if key == "wav":
        return "copy"
    if f.native and shutil.which(f.native):
        return "native"
    if _ffmpeg_has(f.ffmpeg_codec):
        return "ffmpeg"
    if key == "aac" and _ffmpeg_has("libfdk_aac"):
        return "ffmpeg"
    return None


def availability() -> dict[str, str | None]:
    return {k: backend(k) for k in FORMATS}


def describe(key: str, opts: dict) -> str:
    if key == "flac":
        return f"FLAC level {opts.get('level', 8)}" + (" (verified)" if opts.get("verify", True) else "")
    if key == "mp3":
        return (f"LAME V{opts.get('vbr_quality', 0)}" if opts.get("mode", "vbr") == "vbr"
                else f"LAME CBR {opts.get('bitrate', 320)} kbps")
    if key == "opus":
        return f"Opus {opts.get('bitrate', 160)} kbps VBR"
    if key == "vorbis":
        return f"Vorbis q{opts.get('quality', 6)}"
    if key == "aac":
        return f"AAC {opts.get('bitrate', 256)} kbps"
    if key == "wavpack":
        return f"WavPack ({opts.get('mode', 'high')})"
    return FORMATS[key].label


def command(key: str, opts: dict, src: str, dst: str) -> list[str] | None:
    how = backend(key)
    if how is None:
        raise EncodeError(f"No encoder available for {FORMATS[key].label}. Install "
                          f"{FORMATS[key].native or 'ffmpeg'} (or ffmpeg).")
    if how == "copy":
        return None
    if how == "native":
        if key == "flac":
            c = ["flac", "-s", "-f", f"-{int(opts.get('level', 8))}"]
            if opts.get("verify", True):
                c.append("-V")
            return c + ["-o", dst, src]
        if key == "mp3":
            q = (["-V", str(opts.get("vbr_quality", 0))] if opts.get("mode", "vbr") == "vbr"
                 else ["-b", str(opts.get("bitrate", 320)), "--cbr"])
            return ["lame", "--quiet", "--noreplaygain", "-q", "0", *q, src, dst]
        if key == "opus":
            return ["opusenc", "--quiet", "--bitrate", str(opts.get("bitrate", 160)), src, dst]
        if key == "vorbis":
            return ["oggenc", "--quiet", "-q", str(opts.get("quality", 6)), "-o", dst, src]
        if key == "aac":
            return ["fdkaac", "--silent", "-m", "0", "-b", f"{opts.get('bitrate', 256)}k", "-o", dst, src]
        if key == "wavpack":
            mode = {"fast": "-f", "normal": "", "high": "-h", "extra": "-hh"}.get(opts.get("mode", "high"), "-h")
            return ["wavpack", "-q", "-y", *([mode] if mode else []), src, "-o", dst]
    # ffmpeg fallback
    c = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-map_metadata", "-1"]
    if key == "flac":
        c += ["-c:a", "flac", "-compression_level", str(opts.get("level", 8))]
    elif key == "alac":
        c += ["-c:a", "alac"]
    elif key == "wavpack":
        c += ["-c:a", "wavpack"]
    elif key == "mp3":
        c += ["-c:a", "libmp3lame"] + (["-q:a", str(opts.get("vbr_quality", 0))]
                                       if opts.get("mode", "vbr") == "vbr" else ["-b:a", f"{opts.get('bitrate', 320)}k"])
    elif key == "opus":
        c += ["-c:a", "libopus", "-b:a", f"{opts.get('bitrate', 160)}k", "-vbr", "on"]
    elif key == "vorbis":
        c += ["-c:a", "libvorbis", "-q:a", str(opts.get("quality", 6))]
    elif key == "aac":
        codec = "libfdk_aac" if _ffmpeg_has("libfdk_aac") else "aac"
        c += ["-c:a", codec, "-b:a", f"{opts.get('bitrate', 256)}k"]
    if FORMATS[key].ext == "m4a":
        c += ["-f", "ipod"]
    return c + [dst]


def encode(key: str, opts: dict, src: str, dst: str) -> None:
    os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
    tmp = dst + ".part"
    if FORMATS[key].ext in ("m4a",):
        tmp = dst[:-4] + ".part.m4a"   # ffmpeg picks the muxer from the extension
    cmd = command(key, opts, src, tmp)
    if cmd is None:
        shutil.copyfile(src, tmp)
    else:
        if cmd[0] == "ffmpeg" and FORMATS[key].ext != "m4a":
            cmd = cmd[:-1] + ["-f", {"flac": "flac", "wavpack": "wv", "mp3": "mp3", "opus": "opus",
                                     "vorbis": "ogg"}[key], tmp]
        p = system.run(cmd, capture_output=True, text=True, errors="replace")
        if p.returncode != 0 or not os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise EncodeError(f"{cmd[0]} failed: {(p.stderr or p.stdout).strip()[-300:]}")
    os.replace(tmp, dst)
