"""Colour adjustments for cover art, on numpy arrays (no Qt), so they can be tested anywhere.

Arrays are H x W x 4 uint8 in Qt's ARGB32 memory order: B, G, R, A.
"""
from __future__ import annotations

import numpy as np


def _box_blur(x: np.ndarray) -> np.ndarray:
    """3x3 box blur with edge padding (float32 H x W x C)."""
    p = np.pad(x, ((1, 1), (1, 1), (0, 0)), mode="edge")
    h, w = x.shape[:2]
    acc = np.zeros_like(x)
    for dy in range(3):
        for dx in range(3):
            acc += p[dy:dy + h, dx:dx + w]
    return acc / 9.0


def auto_levels(rgb: np.ndarray, clip: float = 0.5) -> np.ndarray:
    """Stretch so the darkest/brightest clip% of pixels become black/white (same for all channels,
    so colours don't shift)."""
    lum = rgb[..., 2] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 0] * 0.114
    lo, hi = np.percentile(lum, [clip, 100 - clip])
    if hi - lo < 8:                         # flat image: nothing sensible to stretch
        return rgb
    return (rgb - lo) * (255.0 / (hi - lo))


def adjust(arr: np.ndarray, brightness: int = 0, contrast: int = 0, saturation: int = 0,
           sharpen: int = 0, auto: bool = False) -> np.ndarray:
    """brightness/contrast/saturation: -100..100, sharpen: 0..100. Returns a new uint8 array."""
    if not (brightness or contrast or saturation or sharpen or auto):
        return arr.copy()
    out = arr.copy()
    rgb = arr[..., :3].astype(np.float32)
    if auto:
        rgb = auto_levels(rgb)
    if brightness:
        rgb += brightness * 1.28                       # +-100 -> +-128 levels
    if contrast:
        f = (100.0 + contrast) / 100.0 if contrast < 0 else 1.0 + contrast / 100.0 * 1.5
        rgb = (rgb - 128.0) * f + 128.0
    if saturation:
        gray = (rgb[..., 2] * 0.299 + rgb[..., 1] * 0.587 + rgb[..., 0] * 0.114)[..., None]
        rgb = gray + (rgb - gray) * (1.0 + saturation / 100.0)
    if sharpen:
        rgb = np.clip(rgb, 0, 255)
        rgb = rgb + (rgb - _box_blur(rgb)) * (sharpen / 100.0 * 1.5)
    out[..., :3] = np.clip(rgb + 0.5, 0, 255).astype(np.uint8)
    return out
