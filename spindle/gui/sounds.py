"""Finish sounds: a built-in chime (synthesised once, cached as WAV) or the user's own sound file."""
from __future__ import annotations

import os
import shutil
import subprocess
import wave
from pathlib import Path

import numpy as np

RATE = 44100
CACHE = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "spindle" / "sounds"
_VERSION = 1                  # bump when the synthesised sounds change, so caches are rebuilt

# (frequency Hz, start s) per note
_SUCCESS = [(1046.50, 0.00), (1318.51, 0.11), (1567.98, 0.22), (2093.00, 0.36)]   # C6 E6 G6 C7, rising
_PROBLEM = [(659.25, 0.00), (523.25, 0.22)]                                       # E5 -> C5, falling


def _bell(freq: float, dur: float, t: np.ndarray) -> np.ndarray:
    """Soft glockenspiel-like tone: a few inharmonic partials, quick attack, exponential decay."""
    partials = ((1.0, 1.0, 1.0), (2.76, 0.35, 1.8), (5.40, 0.12, 3.0))   # (ratio, level, faster decay)
    out = np.zeros_like(t)
    for ratio, level, k in partials:
        out += level * np.sin(2 * np.pi * freq * ratio * t) * np.exp(-t * 6.0 * k / dur)
    attack = np.minimum(1.0, t / 0.004)
    return out * attack


def synth(notes: list[tuple[float, float]], length: float = 1.3, level: float = 0.45) -> np.ndarray:
    n = int(RATE * length)
    mix = np.zeros(n)
    for freq, start in notes:
        i = int(start * RATE)
        t = np.arange(n - i) / RATE
        mix[i:] += _bell(freq, length - start, t)
    mix *= level / max(1e-9, np.abs(mix).max())
    fade = int(RATE * 0.05)
    mix[-fade:] *= np.linspace(1, 0, fade)
    return (mix * 32767).astype("<i2")


def _write(path: Path, pcm: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    tmp.replace(path)


def builtin(kind: str = "success") -> str:
    """Path of the built-in sound ('success' or 'problem'), created on first use."""
    path = CACHE / f"{kind}-v{_VERSION}.wav"
    if not path.exists():
        _write(path, synth(_PROBLEM if kind == "problem" else _SUCCESS,
                           length=1.0 if kind == "problem" else 1.3))
    return str(path)


def resolve(cfg_sound: dict, problem: bool) -> str | None:
    """The file to play for a finished rip, or None when sounds are off."""
    if not cfg_sound.get("on_finish", True):
        return None
    custom = (cfg_sound.get("file") or "").strip()
    if custom and os.path.isfile(os.path.expanduser(custom)) and not problem:
        return os.path.expanduser(custom)
    return builtin("problem" if problem else "success")


# --- playback ---------------------------------------------------------------------------------------
_players: list = []           # keep Qt players alive until they finish


def _play_qt(path: str, volume: float) -> bool:
    try:
        from PySide6.QtCore import QUrl
        from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
    except ImportError:
        return False
    try:
        out = QAudioOutput()
        out.setVolume(volume)
        p = QMediaPlayer()
        p.setAudioOutput(out)
        p.setSource(QUrl.fromLocalFile(path))
        pair = (p, out)
        _players.append(pair)

        def done(state):
            if state == QMediaPlayer.StoppedState and pair in _players:
                _players.remove(pair)
        p.playbackStateChanged.connect(done)
        p.errorOccurred.connect(lambda *a: _play_cli(path, volume) and None)
        p.play()
        return True
    except Exception:
        return False


def _play_cli(path: str, volume: float) -> bool:
    vol = max(0.0, min(1.0, volume))
    for cmd in (["pw-play", f"--volume={vol:.2f}", path],
                ["paplay", f"--volume={int(vol * 65536)}", path],
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "-volume", str(int(vol * 100)), path],
                ["aplay", "-q", path]):
        if shutil.which(cmd[0]):
            try:
                subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                 start_new_session=True)
                return True
            except OSError:
                continue
    return False


def play(path: str | None, volume: float = 0.8) -> bool:
    """Play a sound without blocking. Returns False if nothing could play it."""
    if not path:
        return False
    return _play_qt(path, volume) or _play_cli(path, volume)
