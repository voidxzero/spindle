"""Spectrum analysis for the mini visualiser (numpy only, no Qt)."""
from __future__ import annotations

import numpy as np

N = 2048                                   # samples per FFT (~46 ms at 44.1 kHz)
_WINDOW = np.hanning(N)


def band_edges(bands: int, lo: float = 50.0, hi: float = 16000.0, rate: int = 44100) -> np.ndarray:
    """FFT bin index boundaries for ``bands`` log-spaced bands, like Winamp's analyser."""
    freqs = np.geomspace(lo, hi, bands + 1)
    edges = np.round(freqs / (rate / N)).astype(int)
    edges = np.maximum.accumulate(np.maximum(edges, 1))
    for i in range(1, len(edges)):            # every band gets at least one bin
        if edges[i] <= edges[i - 1]:
            edges[i] = edges[i - 1] + 1
    return np.minimum(edges, N // 2)


def levels(pcm: bytes, bands: int = 19) -> np.ndarray:
    """0..1 loudness per band for 16-bit stereo PCM (uses the last N stereo samples)."""
    a = np.frombuffer(pcm[-N * 4:], dtype="<i2")
    if a.size < N * 2:
        return np.zeros(bands)
    mono = (a[0::2].astype(np.float32) + a[1::2].astype(np.float32)) / 65536.0
    mag = np.abs(np.fft.rfft(mono * _WINDOW)) / (N / 4)
    edges = band_edges(bands)
    out = np.empty(bands)
    for i in range(bands):
        seg = mag[edges[i]:edges[i + 1]]
        out[i] = seg.max() if seg.size else 0.0
    db = 20 * np.log10(np.maximum(out, 1e-6))
    # tilt so treble shows up the way it does on Winamp, then map -54..0 dB to 0..1
    db += np.linspace(0, 9, bands)
    return np.clip((db + 54) / 54, 0.0, 1.0)


class Smoother:
    """Winamp-ish motion: bars jump up, fall steadily; peak caps hold, then drift down."""

    def __init__(self, bands: int):
        self.bars = np.zeros(bands)
        self.peaks = np.zeros(bands)
        self.hold = np.zeros(bands, dtype=int)

    def step(self, target: np.ndarray | None, fall: float = 0.045, peak_fall: float = 0.012, hold: int = 12):
        if target is None:
            target = np.zeros_like(self.bars)
        self.bars = np.maximum(target, self.bars - fall)
        rising = self.bars >= self.peaks
        self.peaks = np.where(rising, self.bars, self.peaks)
        self.hold = np.where(rising, hold, self.hold - 1)
        drop = self.hold <= 0
        self.peaks = np.where(drop, np.maximum(self.bars, self.peaks - peak_fall), self.peaks)
        return self.bars, self.peaks

    @property
    def idle(self) -> bool:
        return float(self.bars.max(initial=0)) < 0.005 and float(self.peaks.max(initial=0)) < 0.005
